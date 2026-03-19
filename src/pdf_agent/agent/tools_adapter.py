"""Adapt existing BaseTool instances to LangChain StructuredTool for LangGraph."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from pdf_agent.agent.state import AgentState
from pdf_agent.config import settings
from pdf_agent.schemas.tool import ParamSpec, ToolManifest
from pdf_agent.tools.base import BaseTool
from pdf_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

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
# Result cache — keyed by (tool_name, sha256(inputs), params_hash)
# ---------------------------------------------------------------------------

_result_cache: dict[str, str] = {}  # cache_key -> result string
_cache_lock = threading.Lock()
_MAX_CACHE = 256


def _cache_key(tool_name: str, input_paths: list[Path], params: dict) -> str:
    h = hashlib.sha256()
    h.update(tool_name.encode())
    for p in sorted(input_paths):
        if p.exists():
            h.update(p.read_bytes())
    h.update(json.dumps(params, sort_keys=True).encode())
    return h.hexdigest()


def _get_cached(key: str) -> str | None:
    with _cache_lock:
        return _result_cache.get(key)


def _set_cached(key: str, value: str) -> None:
    with _cache_lock:
        if len(_result_cache) >= _MAX_CACHE:
            # Evict oldest (first inserted)
            oldest = next(iter(_result_cache))
            del _result_cache[oldest]
        _result_cache[key] = value

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


# ---------------------------------------------------------------------------
# SSE progress queue — keyed by thread_id
# ---------------------------------------------------------------------------

# Maps thread_id -> queue of (percent, message) progress updates
_progress_queues: dict[str, queue.Queue] = {}
_progress_lock = threading.Lock()


def get_progress_queue(thread_id: str) -> queue.Queue:
    with _progress_lock:
        if thread_id not in _progress_queues:
            _progress_queues[thread_id] = queue.Queue(maxsize=100)
        return _progress_queues[thread_id]


def release_progress_queue(thread_id: str):
    with _progress_lock:
        _progress_queues.pop(thread_id, None)


# ---------------------------------------------------------------------------
# Wrapper that bridges LangChain tool call → BaseTool.run()
# ---------------------------------------------------------------------------

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

        # --- Resolve input files ---
        explicit_paths = kwargs.pop("input_file_paths", None)
        if explicit_paths:
            input_paths = [Path(p) for p in explicit_paths]
        else:
            input_paths = [Path(p) for p in state.get("current_files", [])]

        # Validate input count
        if len(input_paths) < manifest.inputs.min:
            return f"Error: {manifest.name} requires at least {manifest.inputs.min} input file(s), got {len(input_paths)}."
        if len(input_paths) > manifest.inputs.max:
            input_paths = input_paths[: manifest.inputs.max]

        # --- Create step workdir ---
        step = state.get("step_counter", 0)
        thread_workdir = Path(state.get("thread_workdir", "/tmp"))
        step_dir = thread_workdir / f"step_{step}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # --- Build params dict (remaining kwargs) ---
        params: dict[str, Any] = {}
        for p in manifest.params:
            if p.name in kwargs and kwargs[p.name] is not None:
                params[p.name] = kwargs[p.name]
            elif p.default is not None:
                params[p.name] = p.default

        # --- Build progress reporter that pushes to SSE queue ---
        thread_id = state.get("configurable", {}).get("thread_id", "") if isinstance(state, dict) else ""
        prog_queue: queue.Queue | None = None
        if thread_id:
            prog_queue = get_progress_queue(thread_id)

        def reporter(percent: int, message: str = "") -> None:
            if prog_queue:
                try:
                    prog_queue.put_nowait({"percent": percent, "message": message})
                except queue.Full:
                    pass

        # --- Execute tool (async_hint=True → offload to thread pool with timeout) ---
        # Check cache for cacheable (non-async_hint) tools
        cache_key = None
        if not manifest.async_hint:
            cache_key = _cache_key(manifest.name, input_paths, params)
            cached = _get_cached(cache_key)
            if cached is not None:
                logger.debug("Cache hit for %s", manifest.name)
                return cached

        try:
            if manifest.async_hint:
                async with _get_semaphore():
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            tool.run,
                            inputs=input_paths,
                            params=params,
                            workdir=step_dir,
                            reporter=reporter,
                        ),
                        timeout=settings.external_cmd_timeout_sec,
                    )
            else:
                result = tool.run(
                    inputs=input_paths,
                    params=params,
                    workdir=step_dir,
                    reporter=reporter,
                )
        except asyncio.TimeoutError:
            return f"Error: {manifest.name} timed out after {settings.external_cmd_timeout_sec}s"
        except Exception as e:
            logger.exception("Tool %s failed", manifest.name)
            return f"Error executing {manifest.name}: {e}"

        # --- Format result ---
        output_files = [str(f) for f in result.output_files]
        parts = []
        if result.log:
            parts.append(result.log)
        if result.meta:
            parts.append(f"Metadata: {json.dumps(result.meta, ensure_ascii=False, default=str)}")
        if output_files:
            parts.append(f"Output files: {output_files}")

        result_str = "\n".join(parts) if parts else "Done (no output)."

        # Store in cache for non-async tools
        if cache_key is not None:
            _set_cached(cache_key, result_str)

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
