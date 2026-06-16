"""构建 LangGraph 状态图，负责驱动 agent 与工具节点循环执行。

图结构本身保持简单：
- `agent` 节点负责调用模型做出下一步决策；
- `tools` 节点负责执行模型选中的工具并更新文件状态；
- 条件边决定是否继续下一轮或结束。
"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any

import tiktoken
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, trim_messages
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph

from pdf_agent.agent.prompt import build_system_prompt, prepare_messages_for_model
from pdf_agent.agent.state import AgentState, FileInfo
from pdf_agent.agent.tools_adapter import get_adapted_tool_map, parse_tool_result_payload
from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 为系统提示词和模型回复预留空间，剩余预算用于历史消息。
MAX_HISTORY_TOKENS = 100_000
# 接近上限时优先摘要较老消息，尽量保留最近几轮原始上下文。
SUMMARY_THRESHOLD = 80_000
_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    """按当前模型懒加载 tiktoken 编码器。"""
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.encoding_for_model(settings.openai_model)
        except KeyError:
            _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _tiktoken_counter(messages: list) -> int:
    """使用 tiktoken 估算一组 LangChain 消息的 token 数。"""
    enc = _get_encoder()
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(content, str):
            total += len(enc.encode(content, disallowed_special=()))
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    total += len(enc.encode(part, disallowed_special=()))
                elif isinstance(part, dict) and "text" in part:
                    total += len(enc.encode(part["text"], disallowed_special=()))
        # 额外补上消息角色、分隔符等协议开销，避免估算过于乐观。
        total += 4
    return total


# ---------------------------------------------------------------------------
# Agent 节点
# ---------------------------------------------------------------------------

def _make_agent_node(model_with_tools: ChatOpenAI):
    """生成 agent 节点函数，负责整理上下文并调用 LLM。"""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        # 系统提示词依赖当前文件列表，因此每轮都要按最新状态动态构建。
        sys_prompt = build_system_prompt(
            files=state.get("files", []),
            current_files=state.get("current_files", []),
        )

        messages = state["messages"]
        token_count = _tiktoken_counter(messages)

        # 历史过长时优先裁剪到最近消息，防止直接撞到模型上下文上限。
        if token_count > MAX_HISTORY_TOKENS:
            messages = trim_messages(
                messages,
                max_tokens=MAX_HISTORY_TOKENS,
                token_counter=_tiktoken_counter,
                strategy="last",
                start_on="human",
                allow_partial=False,
            )
        elif token_count > SUMMARY_THRESHOLD:
            # 当历史开始逼近上限但还没超限时，先压缩旧消息，尽量保留最近几轮细节。
            old_msgs = messages[:-4] if len(messages) > 4 else []
            recent_msgs = messages[-4:] if len(messages) > 4 else messages
            if old_msgs:
                history_text = "\n".join(
                    f"{m.type}: {m.content[:200]}" for m in old_msgs if hasattr(m, "content") and isinstance(m.content, str)
                )
                try:
                    from langchain_core.messages import HumanMessage
                    summary_response = await model_with_tools.ainvoke([
                        HumanMessage(content=f"Summarize this conversation history in 2-3 sentences:\n{history_text}")
                    ])
                    from langchain_core.messages import AIMessage as _AI
                    summary_msg = _AI(content=f"[Earlier conversation summary: {summary_response.content}]")
                    messages = [summary_msg] + recent_msgs
                except Exception:
                    messages = recent_msgs

        messages = [SystemMessage(content=sys_prompt)] + prepare_messages_for_model(messages)

        response = await model_with_tools.ainvoke(messages)
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            if input_tokens or output_tokens:
                metrics.record_llm_tokens(input_tokens, output_tokens)
        else:
            response_metadata = getattr(response, "response_metadata", None)
            if isinstance(response_metadata, dict):
                token_usage = response_metadata.get("token_usage")
                if isinstance(token_usage, dict):
                    input_tokens = int(token_usage.get("prompt_tokens") or 0)
                    output_tokens = int(token_usage.get("completion_tokens") or 0)
                    if input_tokens or output_tokens:
                        metrics.record_llm_tokens(input_tokens, output_tokens)
        return {"messages": [response]}

    return agent_node


# ---------------------------------------------------------------------------
# 自定义工具节点
# ---------------------------------------------------------------------------

def _make_tool_node(lc_tools: list, tool_registry: ToolRegistry):
    """生成工具节点函数，负责执行工具并把产物写回状态。"""
    tool_map = {t.name: t for t in lc_tools}

    async def tool_node(state: AgentState) -> dict[str, Any]:
        last_msg: AIMessage = state["messages"][-1]
        if not last_msg.tool_calls:
            return {}

        new_messages = []
        new_files: list[FileInfo] = []
        latest_output_files: list[str] = []
        step = state.get("step_counter", 0)

        for call in last_msg.tool_calls:
            tool_name = call["name"]
            # 拷贝参数，避免直接修改来自 checkpoint 的原始状态对象。
            tool_args = dict(call["args"])
            call_id = call["id"]

            lc_tool = tool_map.get(tool_name)
            if not lc_tool:
                new_messages.append(
                    ToolMessage(content=f"Error: unknown tool '{tool_name}'", tool_call_id=call_id)
                )
                continue

            # 注入运行时状态字段，并直接调用底层协程。
            # 如果走 schema 校验，这些额外字段会被视为未知参数而被过滤掉。
            tool_args["state"] = state
            tool_args["tool_call_id"] = call_id

            parsed_result = None
            try:
                result_str = await lc_tool.coroutine(**tool_args)
                parsed_result = parse_tool_result_payload(result_str)
            except Exception as e:
                logger.exception("Tool %s raised exception", tool_name)
                result_str = f"Error: {e}"

            new_messages.append(
                ToolMessage(
                    content=result_str,
                    tool_call_id=call_id,
                    name=tool_name,
                    artifact={
                        "tool": tool_name,
                        "output_files": parsed_result.output_files if parsed_result else [],
                        "meta": parsed_result.meta if parsed_result else {},
                        "elapsed_seconds": parsed_result.elapsed_seconds if parsed_result else None,
                    },
                )
            )

            # 从字符串协议中还原产物路径，并同步更新文件列表与当前激活文件集。
            output_files = parsed_result.output_files if parsed_result else []
            if output_files:
                latest_output_files = output_files
                for fp in output_files:
                    p = Path(fp)
                    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    page_count = _get_page_count(p) if mime == "application/pdf" else None
                    new_files.append(FileInfo(
                        file_id=str(uuid.uuid4()),
                        path=fp,
                        orig_name=p.name,
                        mime_type=mime,
                        page_count=page_count,
                        source=tool_name,
                    ))

            step += 1

        update: dict[str, Any] = {
            "messages": new_messages,
            "step_counter": step,
        }
        if new_files:
            update["files"] = new_files
        if latest_output_files:
            update["current_files"] = latest_output_files

        return update

    return tool_node


def _get_page_count(path: Path) -> int | None:
    """尽量读取 PDF 页数；失败时返回 `None` 而不是中断工具流程。"""
    try:
        import pikepdf
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------

def _should_continue(state: AgentState) -> str:
    """根据最后一条消息是否包含工具调用决定下一跳。"""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        # 限制最大循环次数，防止模型持续重复调用工具导致死循环。
        if state.get("step_counter", 0) >= settings.agent_max_iterations:
            return END
        return "tools"
    return END


# ---------------------------------------------------------------------------
# 对外公开的图构建入口
# ---------------------------------------------------------------------------

def build_graph(
    checkpointer: AsyncPostgresSaver | None,
    tool_registry: ToolRegistry,
) -> Any:
    """编译最终可运行的 LangGraph 状态图。"""
    # 先把领域工具适配成 LangGraph 可绑定的 StructuredTool。
    lc_tools = list(get_adapted_tool_map(tool_registry).values())

    # 统一在这里创建模型实例，便于后续按配置切换模型参数或 base URL。
    model_kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": settings.agent_temperature,
        "api_key": settings.openai_api_key,
    }
    if settings.openai_base_url:
        model_kwargs["base_url"] = settings.openai_base_url

    llm = ChatOpenAI(**model_kwargs)
    model_with_tools = llm.bind_tools(lc_tools, parallel_tool_calls=False)

    # 图结构保持极简，只保留 agent 与 tools 两个节点。
    graph = StateGraph(AgentState)
    graph.add_node("agent", _make_agent_node(model_with_tools))
    graph.add_node("tools", _make_tool_node(lc_tools, tool_registry))

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)
