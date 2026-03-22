"""Adapt existing BaseTool instances to LangChain StructuredTool for LangGraph."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from pdf_agent.agent.state import AgentState
from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.external_commands import bind_conversation_run_context
from pdf_agent.schemas.tool import ParamSpec, ToolManifest
from pdf_agent.tools.base import BaseTool
from pdf_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_RESULT_JSON_PREFIX = "Result JSON:"
_ERROR_RESULT_RE = re.compile(r"^Error:\s*(?:\[(?P<code>[A-Z_]+)\]\s*)?(?P<message>.+)$")

# ---------------------------------------------------------------------------
# Concurrency limiter for CPU-intensive async tools
# ---------------------------------------------------------------------------

_ASYNC_SEMAPHORE: asyncio.Semaphore | None = None
_MAX_CONCURRENT_ASYNC = 4  # max simultaneous OCR/compress/etc operations


def _get_semaphore() -> asyncio.Semaphore:
    global _ASYNC_SEMAPHORE
    if _ASYNC_SEMAPHORE is None:
        _ASYNC_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_ASYNC)
    return _ASYNC_SEMAPHORE

# ---------------------------------------------------------------------------
# ParamSpec → Pydantic field mapping
# ---------------------------------------------------------------------------

def _param_to_field(p: ParamSpec) -> tuple[type, Any]:
    """Convert a ParamSpec to a (python_type, Field) tuple for create_model."""
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
        field_kwargs.setdefault("description", "Page range, e.g. 'all', '1-3,5', 'odd', 'even'")
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
    """Dynamically build a Pydantic model from a tool's manifest params."""
    fields: dict[str, Any] = {}

    for p in manifest.params:
        fields[p.name] = _param_to_field(p)

    # Multi-file tools expose an explicit file path list parameter
    if manifest.inputs.max > 1:
        fields["input_file_paths"] = (
            Optional[list[str]],
            Field(default=None, description="Explicit input file paths. If omitted, uses current active files."),
        )

    model = create_model(f"{manifest.name}_Args", **fields)
    return model


def _allowed_state_paths(state: AgentState) -> set[Path]:
    """Return the normalized set of file paths already attached to the agent state."""
    allowed: set[Path] = set()
    for item in state.get("files", []):
        path = item.get("path") if isinstance(item, dict) else None
        if path:
            allowed.add(Path(path).resolve())
    for path in state.get("current_files", []):
        allowed.add(Path(path).resolve())
    return allowed


# ---------------------------------------------------------------------------
# SSE progress queue — keyed by conversation_id at the app layer
# ---------------------------------------------------------------------------

# Maps conversation_id -> queue of (percent, message) progress updates
_progress_queues: dict[str, queue.Queue] = {}
_progress_lock = threading.Lock()


def get_progress_queue(conversation_id: str) -> queue.Queue:
    with _progress_lock:
        if conversation_id not in _progress_queues:
            _progress_queues[conversation_id] = queue.Queue(maxsize=100)
        return _progress_queues[conversation_id]


def release_progress_queue(conversation_id: str):
    with _progress_lock:
        _progress_queues.pop(conversation_id, None)


# ---------------------------------------------------------------------------
# Structured tool-run result
# ---------------------------------------------------------------------------

@dataclass
class AdaptedToolRunResult:
    log: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    raw_output: str = ""
    elapsed_seconds: float | None = None


def parse_tool_result_payload(result_str: str) -> AdaptedToolRunResult:
    """Parse the formatted StructuredTool response back into structured data."""
    for line in result_str.splitlines():
        if line.startswith(_RESULT_JSON_PREFIX):
            raw = line[len(_RESULT_JSON_PREFIX):].strip()
            payload = json.loads(raw)
            elapsed = payload.get("elapsed_seconds")
            return AdaptedToolRunResult(
                log=str(payload.get("log", "") or ""),
                meta=payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {},
                output_files=[
                    str(path)
                    for path in payload.get("output_files", [])
                    if isinstance(path, str) and path
                ],
                raw_output=result_str,
                elapsed_seconds=float(elapsed) if isinstance(elapsed, (int, float)) else None,
            )
    return AdaptedToolRunResult(log=result_str.strip(), raw_output=result_str)


def _raise_for_error_output(result_str: str) -> None:
    match = _ERROR_RESULT_RE.match(result_str.strip())
    if not match:
        return
    code = match.group("code") or ErrorCode.ENGINE_EXEC_FAILED
    raise PDFAgentError(code=code, message=match.group("message").strip())


def _state_file_entries(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": str(index),
            "path": str(path.resolve()),
            "orig_name": path.name,
            "mime_type": "",
            "page_count": None,
            "source": "conversation_run",
        }
        for index, path in enumerate(paths, start=1)
    ]


# ---------------------------------------------------------------------------
# Wrapper that bridges LangChain tool call → BaseTool.run()
# ---------------------------------------------------------------------------

async def _execute_tool_with_state(
    *,
    tool: BaseTool,
    manifest: ToolManifest,
    state: AgentState | dict[str, Any],
    kwargs: dict[str, Any],
    progress_reporter=None,
):
    """Execute one tool against an agent-like state with shared validation."""
    explicit_paths = kwargs.pop("input_file_paths", None)
    if explicit_paths:
        allowed_paths = _allowed_state_paths(state)
        input_paths = [Path(p).resolve() for p in explicit_paths]
        if not set(input_paths).issubset(allowed_paths):
            raise PDFAgentError(
                code=ErrorCode.INVALID_INPUT_FILE,
                message="explicit input_file_paths must stay within the active file set",
            )
    else:
        input_paths = [Path(p).resolve() for p in state.get("current_files", [])]

    if len(input_paths) < manifest.inputs.min:
        raise PDFAgentError(
            code=ErrorCode.INVALID_INPUT_FILE,
            message=f"{manifest.name} requires at least {manifest.inputs.min} input file(s), got {len(input_paths)}",
        )
    if len(input_paths) > manifest.inputs.max:
        input_paths = input_paths[: manifest.inputs.max]

    step = state.get("step_counter", 0)
    conversation_workdir = Path(state.get("conversation_workdir", "/tmp"))
    step_dir = conversation_workdir / f"step_{step}"
    step_dir.mkdir(parents=True, exist_ok=True)

    params: dict[str, Any] = {}
    for p in manifest.params:
        if p.name in kwargs and kwargs[p.name] is not None:
            params[p.name] = kwargs[p.name]
        elif p.default is not None:
            params[p.name] = p.default
    validated_params = tool.validate(params)

    conversation_id = state.get("configurable", {}).get("thread_id", "") if isinstance(state, dict) else ""
    prog_queue: queue.Queue | None = get_progress_queue(conversation_id) if conversation_id else None

    def reporter(percent: int, message: str = "") -> None:
        if prog_queue:
            try:
                prog_queue.put_nowait({"percent": percent, "message": message})
            except queue.Full:
                pass
        if progress_reporter:
            progress_reporter(percent, message)

    start = time.perf_counter()
    try:
        with bind_conversation_run_context(conversation_id or None):
            if manifest.async_hint:
                async with _get_semaphore():
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            tool.run,
                            inputs=input_paths,
                            params=validated_params,
                            workdir=step_dir,
                            reporter=reporter,
                        ),
                        timeout=settings.external_cmd_timeout_sec,
                    )
            else:
                result = tool.run(
                    inputs=input_paths,
                    params=validated_params,
                    workdir=step_dir,
                    reporter=reporter,
                )
    except asyncio.TimeoutError as exc:
        raise PDFAgentError(
            code=ErrorCode.ENGINE_EXEC_TIMEOUT,
            message=f"{manifest.name} timed out after {settings.external_cmd_timeout_sec}s",
        ) from exc
    except PDFAgentError:
        raise
    except Exception as exc:
        logger.exception("Tool %s failed", manifest.name)
        raise PDFAgentError(
            code=ErrorCode.ENGINE_EXEC_FAILED,
            message=f"{manifest.name} failed: {exc}",
        ) from exc

    metrics.record_tool(manifest.name, time.perf_counter() - start)
    return result


def _make_tool_wrapper(tool: BaseTool, manifest: ToolManifest):
    """Create the callable that runs when the LLM invokes this tool.

    The custom tool node injects 'state' and 'tool_call_id' into kwargs
    before calling this wrapper. They are NOT part of the args_schema.

    Long-running tools (async_hint=True) are executed in a thread pool
    via asyncio.to_thread() to avoid blocking the event loop.
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

        # --- Format result ---
        output_files = [str(f) for f in result.output_files]
        elapsed_seconds = round(time.perf_counter() - start, 3)
        payload = {
            "log": result.log,
            "meta": result.meta,
            "output_files": output_files,
            "elapsed_seconds": elapsed_seconds,
        }
        parts = []
        if result.log:
            parts.append(result.log)
        if result.meta:
            parts.append(f"Metadata: {json.dumps(result.meta, ensure_ascii=False, default=str)}")
        parts.append(f"Result JSON: {json.dumps(payload, ensure_ascii=False, default=str)}")

        result_str = "\n".join(parts) if parts else "Done (no output)."
        return result_str

    return async_wrapper


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def adapt_all_tools(registry: ToolRegistry) -> list[StructuredTool]:
    """Convert all registered BaseTool instances into LangChain StructuredTools."""
    tools: list[StructuredTool] = []

    for base_tool in registry.list_all():
        manifest = base_tool.manifest()
        args_schema = _build_args_schema(manifest)
        wrapper = _make_tool_wrapper(base_tool, manifest)

        lc_tool = StructuredTool(
            name=manifest.name,
            description=manifest.description or manifest.label,
            args_schema=args_schema,
            coroutine=wrapper,  # async wrapper
        )
        tools.append(lc_tool)

    logger.info("Adapted %d tools for LangGraph", len(tools))
    return tools


_ADAPTED_TOOL_MAP_CACHE: dict[ToolRegistry, tuple[int, dict[str, StructuredTool]]] = {}


def get_adapted_tool_map(registry: ToolRegistry) -> dict[str, StructuredTool]:
    """Return a cached name -> StructuredTool map for the current registry snapshot."""
    cached = _ADAPTED_TOOL_MAP_CACHE.get(registry)
    if cached is None or cached[0] != len(registry):
        tool_map = {tool.name: tool for tool in adapt_all_tools(registry)}
        _ADAPTED_TOOL_MAP_CACHE[registry] = (len(registry), tool_map)
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
    """Execute a tool through the LangChain StructuredTool adapter path."""
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
        "configurable": {"thread_id": conversation_id},
    }
    result_str = await lc_tool.coroutine(
        state=state,
        tool_call_id=f"conversation_run:{conversation_id}:{step_counter}:{tool_name}",
        input_file_paths=[str(path) for path in resolved_paths],
        progress_reporter=progress_reporter,
        **(params or {}),
    )
    _raise_for_error_output(result_str)
    return parse_tool_result_payload(result_str)
