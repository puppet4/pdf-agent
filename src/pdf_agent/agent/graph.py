"""LangGraph StateGraph construction — agent + custom tool node."""
from __future__ import annotations

import json
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

from pdf_agent.agent.prompt import build_system_prompt
from pdf_agent.agent.state import AgentState, FileInfo
from pdf_agent.agent.tools_adapter import adapt_all_tools
from pdf_agent.config import settings
from pdf_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Reserve tokens for system prompt + response; use the rest for history
MAX_HISTORY_TOKENS = 100_000
_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    """Lazy-load the tiktoken encoder for the configured model."""
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.encoding_for_model(settings.openai_model)
        except KeyError:
            _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def _tiktoken_counter(messages: list) -> int:
    """Count tokens for a list of LangChain messages using tiktoken."""
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
        # Overhead per message (role, separators)
        total += 4
    return total


# ---------------------------------------------------------------------------
# Agent node
# ---------------------------------------------------------------------------

def _make_agent_node(model_with_tools: ChatOpenAI):
    """Return the agent node function that calls the LLM."""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        # Build dynamic system prompt
        sys_prompt = build_system_prompt(
            files=state.get("files", []),
            current_files=state.get("current_files", []),
        )

        # Trim messages to stay within context window (token-level)
        messages = trim_messages(
            state["messages"],
            max_tokens=MAX_HISTORY_TOKENS,
            token_counter=_tiktoken_counter,
            strategy="last",
            start_on="human",
            allow_partial=False,
        )
        messages = [SystemMessage(content=sys_prompt)] + messages

        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    return agent_node


# ---------------------------------------------------------------------------
# Custom tool node
# ---------------------------------------------------------------------------

def _make_tool_node(lc_tools: list, tool_registry: ToolRegistry):
    """Return a custom tool node that executes tools and updates file state."""
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
            tool_args = call["args"]
            call_id = call["id"]

            lc_tool = tool_map.get(tool_name)
            if not lc_tool:
                new_messages.append(
                    ToolMessage(content=f"Error: unknown tool '{tool_name}'", tool_call_id=call_id)
                )
                continue

            # Inject state and tool_call_id, call underlying func directly
            # (bypasses schema validation which would strip these extra keys)
            tool_args["state"] = state
            tool_args["tool_call_id"] = call_id

            try:
                result_str = lc_tool.func(**tool_args)
            except Exception as e:
                logger.exception("Tool %s raised exception", tool_name)
                result_str = f"Error: {e}"

            new_messages.append(ToolMessage(content=result_str, tool_call_id=call_id))

            # Parse output files from result string
            output_files = _parse_output_files(result_str)
            if output_files:
                latest_output_files = output_files
                base_tool = tool_registry.get(tool_name)
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


def _parse_output_files(result_str: str) -> list[str]:
    """Extract output file paths from the tool result string."""
    for line in result_str.splitlines():
        if line.startswith("Output files:"):
            raw = line[len("Output files:"):].strip()
            try:
                return json.loads(raw.replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                pass
    return []


def _get_page_count(path: Path) -> int | None:
    """Try to get PDF page count."""
    try:
        import pikepdf
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Conditional edge
# ---------------------------------------------------------------------------

def _should_continue(state: AgentState) -> str:
    """Route: if last message has tool_calls → 'tools', else → END."""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        # Guard against infinite loops
        if state.get("step_counter", 0) >= settings.agent_max_iterations:
            return END
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Public: build the compiled graph
# ---------------------------------------------------------------------------

def build_graph(
    checkpointer: AsyncPostgresSaver,
    tool_registry: ToolRegistry,
) -> Any:  # CompiledStateGraph
    """Compile the LangGraph StateGraph with agent and tool nodes."""
    # Adapt tools
    lc_tools = adapt_all_tools(tool_registry)

    # Create LLM
    model_kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": settings.agent_temperature,
        "api_key": settings.openai_api_key,
    }
    if settings.openai_base_url:
        model_kwargs["base_url"] = settings.openai_base_url

    llm = ChatOpenAI(**model_kwargs)
    model_with_tools = llm.bind_tools(lc_tools, parallel_tool_calls=False)

    # Build graph
    graph = StateGraph(AgentState)
    graph.add_node("agent", _make_agent_node(model_with_tools))
    graph.add_node("tools", _make_tool_node(lc_tools, tool_registry))

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)
