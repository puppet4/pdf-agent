"""把领域工具模型适配为 LangChain `StructuredTool`。

本文件现在只保留“薄适配”职责：
- 根据 `ToolManifest` 生成 Pydantic 参数 schema；
- 为每个 `BaseTool` 生成 LangChain 可调用包装器；
- 暴露缓存后的工具映射，供 LangGraph 绑定。
"""
from __future__ import annotations

import logging
import time
import weakref
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from pdf_agent.agent.state import AgentState
from pdf_agent.agent.tool_execution import (
    _allowed_state_paths,
    _execute_tool_with_state,
    _get_semaphore,
    _state_file_entries,
)
from pdf_agent.agent.tool_protocol import (
    AdaptedToolRunResult,
    _raise_for_error_output,
    format_tool_result_payload,
    get_progress_queue,
    parse_tool_result_payload,
    release_progress_queue,
)
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.schemas.tool import ParamSpec, ToolManifest
from pdf_agent.tools.base import BaseTool
from pdf_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "AdaptedToolRunResult",
    "_allowed_state_paths",
    "_build_args_schema",
    "_execute_tool_with_state",
    "_get_semaphore",
    "_make_tool_wrapper",
    "_raise_for_error_output",
    "_state_file_entries",
    "adapt_all_tools",
    "get_adapted_tool_map",
    "get_progress_queue",
    "invoke_adapted_tool",
    "parse_tool_result_payload",
    "release_progress_queue",
]

# ---------------------------------------------------------------------------
# ParamSpec -> Pydantic 字段映射
# ---------------------------------------------------------------------------

def _param_to_field(p: ParamSpec) -> tuple[type, Any]:
    """把领域参数定义转换成 `create_model()` 需要的字段描述。"""
    field_kwargs: dict[str, Any] = {"description": p.description or p.label}

    if p.type == "string":
        py_type = str
    elif p.type == "int":
        py_type = int
        if p.min is not None:
            field_kwargs["ge"] = int(p.min)
        if p.max is not None:
            field_kwargs["le"] = int(p.max)
    elif p.type == "float":
        py_type = float
        if p.min is not None:
            field_kwargs["ge"] = p.min
        if p.max is not None:
            field_kwargs["le"] = p.max
    elif p.type == "bool":
        py_type = bool
    elif p.type == "enum":
        if p.options:
            py_type = Literal[tuple(p.options)]  # type: ignore[valid-type]
        else:
            py_type = str
    elif p.type == "page_range":
        py_type = str
        field_kwargs.setdefault("description", "页码范围，例如：all、1-3,5、odd、even")
    else:
        py_type = str

    if p.required:
        field_kwargs["default"] = ...
    elif p.default is not None:
        field_kwargs["default"] = p.default
    else:
        field_kwargs["default"] = None

    return (py_type if p.required else Optional[py_type], Field(**field_kwargs))


def _build_args_schema(manifest: ToolManifest) -> type[BaseModel]:
    """根据工具 manifest 动态构造 Pydantic 参数模型。"""
    fields: dict[str, Any] = {}

    for p in manifest.params:
        fields[p.name] = _param_to_field(p)

    # 多文件工具额外暴露显式文件路径列表，便于在对话状态里精确指定输入集合。
    if manifest.inputs.max > 1:
        fields["input_file_paths"] = (
            Optional[list[str]],
            Field(default=None, description="显式输入文件路径列表；省略时使用当前激活文件集。"),
        )

    return create_model(f"{manifest.name}_Args", **fields)


_SENSITIVE_PARAMS = frozenset({"owner_password", "user_password", "p12_password"})

# ---------------------------------------------------------------------------
# 连接 LangChain 调用与 BaseTool.run() 的包装器
# ---------------------------------------------------------------------------


def _make_tool_wrapper(tool: BaseTool, manifest: ToolManifest):
    """创建真正供 LLM 调用的包装函数。

    自定义工具节点会在调用前额外注入 `state`、`tool_call_id` 等运行时字段。
    这些字段不属于公开 schema，因此需要在包装层手动接收并转交给执行层。
    """

    async def async_wrapper(**kwargs: Any) -> str:
        state: AgentState = kwargs.pop("state", {})
        kwargs.pop("tool_call_id", None)
        progress_reporter = kwargs.pop("progress_reporter", None)
        start = time.perf_counter()
        try:
            result = await _execute_tool_with_state(
                tool=tool,
                manifest=manifest,
                state=state,
                kwargs=kwargs,
                progress_reporter=progress_reporter,
            )
        except PDFAgentError as exc:
            return f"Error: [{exc.code}] {exc.message}"
        except Exception as exc:
            logger.exception("Tool %s failed unexpectedly", manifest.name)
            return f"Error: [{ErrorCode.ENGINE_EXEC_FAILED}] {exc}"

        # 返回值保持字符串协议，便于 LangGraph 工具消息和 SSE 层复用同一套解析逻辑。
        return format_tool_result_payload(
            result,
            elapsed_seconds=round(time.perf_counter() - start, 3),
            sensitive_params=_SENSITIVE_PARAMS,
        )

    return async_wrapper


# ---------------------------------------------------------------------------
# 对外公开的适配入口
# ---------------------------------------------------------------------------

def adapt_all_tools(registry: ToolRegistry) -> list[StructuredTool]:
    """把注册表里的全部领域工具适配为 LangChain `StructuredTool`。"""
    tools: list[StructuredTool] = []

    for base_tool in registry.list_all():
        manifest = base_tool.manifest()
        args_schema = _build_args_schema(manifest)
        wrapper = _make_tool_wrapper(base_tool, manifest)

        lc_tool = StructuredTool(
            name=manifest.name,
            description=manifest.description or manifest.label,
            args_schema=args_schema,
            # 使用异步包装器，保证 LangGraph 在事件循环内调用时不会阻塞。
            coroutine=wrapper,
        )
        tools.append(lc_tool)

    logger.info("Adapted %d tools for LangGraph", len(tools))
    return tools


_ADAPTED_TOOL_MAP_CACHE: weakref.WeakKeyDictionary[ToolRegistry, tuple[tuple[str, ...], dict[str, StructuredTool]]] = weakref.WeakKeyDictionary()


def get_adapted_tool_map(registry: ToolRegistry) -> dict[str, StructuredTool]:
    """返回当前注册表快照对应的缓存工具映射。"""
    current_keys = tuple(sorted(t.name for t in registry.list_all()))
    cached = _ADAPTED_TOOL_MAP_CACHE.get(registry)
    if cached is None or cached[0] != current_keys:
        tool_map = {tool.name: tool for tool in adapt_all_tools(registry)}
        _ADAPTED_TOOL_MAP_CACHE[registry] = (current_keys, tool_map)
        return tool_map
    return cached[1]


async def invoke_adapted_tool(
    *,
    registry: ToolRegistry,
    tool_name: str,
    input_paths: list[Path],
    params: dict[str, Any],
    conversation_workdir: Path,
    step_counter: int,
    conversation_id: str,
    progress_reporter=None,
) -> AdaptedToolRunResult:
    """通过适配后的 LangChain 工具路径执行一次工具调用。"""
    tool_map = get_adapted_tool_map(registry)
    lc_tool = tool_map.get(tool_name)
    if lc_tool is None:
        raise PDFAgentError(code=ErrorCode.INVALID_PARAMS, message=f"Unknown tool: {tool_name}")

    resolved_paths = [path.resolve() for path in input_paths]
    state = {
        "files": _state_file_entries(resolved_paths),
        "current_files": [str(path) for path in resolved_paths],
        "conversation_workdir": str(conversation_workdir),
        "step_counter": step_counter,
        "configurable": {"thread_id": conversation_id, "run_id": conversation_id},
    }
    # 这里主动传入 state 和 tool_call_id，模拟真实 LangGraph 工具节点的调用上下文。
    result_str = await lc_tool.coroutine(
        state=state,
        tool_call_id=f"conversation_run:{conversation_id}:{step_counter}:{tool_name}",
        input_file_paths=[str(path) for path in resolved_paths],
        progress_reporter=progress_reporter,
        **(params or {}),
    )
    _raise_for_error_output(result_str)
    return parse_tool_result_payload(result_str)
