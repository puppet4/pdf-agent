"""负责把工具调用真正落到本地文件运行时上的执行辅助逻辑。

这一层不关心 LangChain 的 schema 细节，只处理执行时必须统一保证的约束：
- 输入文件必须来自当前会话状态；
- 每一步工具都有独立工作目录；
- 长耗时工具需要并发限流与超时保护；
- 结果文件不得写出当前步骤目录。
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from pdf_agent.agent.state import AgentState
from pdf_agent.agent.tool_protocol import get_progress_queue
from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.external_commands import bind_conversation_run_context
from pdf_agent.schemas.tool import ToolManifest
from pdf_agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_ASYNC_SEMAPHORE: threading.Semaphore | None = None
_SEMAPHORE_LOCK = threading.Lock()
# 对 OCR、压缩等重任务做统一并发限流，避免单机被多个线程同时打满。
_MAX_CONCURRENT_ASYNC = 4


def _get_semaphore() -> threading.Semaphore:
    """惰性初始化全局并发信号量。"""
    global _ASYNC_SEMAPHORE
    if _ASYNC_SEMAPHORE is None:
        with _SEMAPHORE_LOCK:
            if _ASYNC_SEMAPHORE is None:
                _ASYNC_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT_ASYNC)
    return _ASYNC_SEMAPHORE


def _allowed_state_paths(state: AgentState) -> set[Path]:
    """返回当前 agent 状态里允许作为输入的规范化文件路径集合。"""
    allowed: set[Path] = set()
    for item in state.get("files", []):
        path = item.get("path") if isinstance(item, dict) else None
        if path:
            allowed.add(Path(path).resolve())
    for path in state.get("current_files", []):
        allowed.add(Path(path).resolve())
    return allowed


def _state_file_entries(paths: list[Path]) -> list[dict[str, Any]]:
    """把裸文件路径补齐为 AgentState 需要的文件条目结构。"""
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


async def _execute_tool_with_state(
    *,
    tool: BaseTool,
    manifest: ToolManifest,
    state: AgentState | dict[str, Any],
    kwargs: dict[str, Any],
    progress_reporter=None,
):
    """基于 agent 状态执行单个工具，并统一处理公共校验逻辑。

    这里集中处理输入文件解析、参数默认值、工作目录创建、进度上报、
    超时保护、输出路径约束等横切逻辑，避免每个工具自行重复实现。
    """
    explicit_paths = kwargs.pop("input_file_paths", None)
    if explicit_paths:
        # 显式传入路径时，只允许引用当前状态中已经激活的文件，防止越权访问任意本地路径。
        allowed_paths = _allowed_state_paths(state)
        input_paths = [Path(path).resolve() for path in explicit_paths]
        if not set(input_paths).issubset(allowed_paths):
            raise PDFAgentError(
                code=ErrorCode.INVALID_INPUT_FILE,
                message="explicit input_file_paths must stay within the active file set",
            )
    else:
        # 未显式指定输入时，默认使用 state.current_files，这就是对话里“继续处理上一步结果”的来源。
        input_paths = [Path(path).resolve() for path in state.get("current_files", [])]

    if len(input_paths) < manifest.inputs.min:
        raise PDFAgentError(
            code=ErrorCode.INVALID_INPUT_FILE,
            message=f"{manifest.name} requires at least {manifest.inputs.min} input file(s), got {len(input_paths)}",
        )
    if len(input_paths) > manifest.inputs.max:
        # 当前约定是“截断到工具允许的最大输入数”，而不是直接报错；
        # 这样多选文件时，单文件工具仍能继续处理第一批有效输入。
        input_paths = input_paths[: manifest.inputs.max]

    step = state.get("step_counter", 0)
    conversation_workdir = Path(state.get("conversation_workdir", "/tmp"))
    step_dir = conversation_workdir / f"step_{step}"
    # 每一步工具执行都分配独立目录，既便于产物管理，也便于限制输出范围。
    step_dir.mkdir(parents=True, exist_ok=True)

    params: dict[str, Any] = {}
    for param in manifest.params:
        if param.name in kwargs and kwargs[param.name] is not None:
            params[param.name] = kwargs[param.name]
        elif param.default is not None:
            params[param.name] = param.default
    # 这里先统一补默认值，再交给各工具自己的 validate() 做领域级校验。
    validated_params = tool.validate(params)

    configurable = state.get("configurable", {}) if isinstance(state, dict) else {}
    thread_id = configurable.get("thread_id", "") if isinstance(configurable, dict) else ""
    run_id = configurable.get("run_id", "") if isinstance(configurable, dict) else ""
    conversation_run_id = str(run_id or thread_id or "")
    # progress_queue 是可选的：直接调用工具时可能没有 run_id，这时只是不提供实时进度。
    progress_queue: queue.Queue | None = get_progress_queue(conversation_run_id) if conversation_run_id else None

    def reporter(percent: int, message: str = "") -> None:
        # 进度同时写入共享队列和可选的外部回调，两端分别服务 SSE 和直接调用路径。
        if progress_queue:
            try:
                progress_queue.put_nowait({"percent": percent, "message": message})
            except queue.Full:
                pass
        if progress_reporter:
            progress_reporter(percent, message)

    start = time.perf_counter()
    try:
        with bind_conversation_run_context(conversation_run_id or None):
            if manifest.async_hint:
                def _run_async_tool() -> ToolResult:
                    # 异步提示工具通常会调用外部命令或执行重 CPU/IO 任务，因此统一放到线程池执行。
                    # 信号量控制的是“系统里最多同时跑多少个重任务”，不是这个工具自己的并发。
                    semaphore = _get_semaphore()
                    semaphore.acquire()
                    try:
                        return tool.run(
                            inputs=input_paths,
                            params=validated_params,
                            workdir=step_dir,
                            reporter=reporter,
                        )
                    finally:
                        semaphore.release()

                result = await asyncio.wait_for(
                    asyncio.to_thread(_run_async_tool),
                    timeout=settings.external_cmd_timeout_sec,
                )
            else:
                # 轻量级工具直接在当前线程执行，减少一次线程切换开销。
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
        # 未分类异常统一包装成领域错误，避免上层 API 直接暴露底层实现细节。
        logger.exception("Tool %s failed", manifest.name)
        raise PDFAgentError(
            code=ErrorCode.ENGINE_EXEC_FAILED,
            message=f"{manifest.name} failed: {exc}",
        ) from exc

    input_set = {path.resolve() for path in input_paths}
    step_dir_resolved = step_dir.resolve()
    for output_file in result.output_files:
        if Path(output_file).resolve() in input_set:
            # 允许工具把原输入文件原样透传出去；这种情况不要求它必须位于 step_dir 内。
            continue
        # 新产物必须留在当前步骤目录内，避免工具误写或恶意写出会话沙箱。
        # 这样后续按 step 目录枚举产物时，也能稳定找到这一步真正新增的文件。
        if not Path(output_file).resolve().is_relative_to(step_dir_resolved):
            raise PDFAgentError(
                code=ErrorCode.OUTPUT_GENERATION_FAILED,
                message=f"{manifest.name} wrote output outside its work directory",
            )

    metrics.record_tool(manifest.name, time.perf_counter() - start)
    return result
