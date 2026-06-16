"""封装 agent 运行时共享的进度与结果协议辅助逻辑。

这里负责两件事：
1. 管理一次会话执行期间的进度队列，供 SSE 层轮询读取。
2. 在文本工具协议与结构化结果之间做双向转换，避免上层重复解析。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import queue
import re
import threading
import time
from typing import Any, Iterable

from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.tools.base import ToolResult

_RESULT_JSON_PREFIX = "Result JSON:"
_ERROR_RESULT_RE = re.compile(r"^Error:\s*(?:\[(?P<code>[A-Z_]+)\]\s*)?(?P<message>.+)$")

# 以会话执行 ID 为键保存进度队列，并记录最近一次活跃时间，便于后续清理陈旧队列。
_progress_queues: dict[str, tuple[queue.Queue, float]] = {}
_progress_lock = threading.Lock()
# 队列默认保留 1 小时，既能覆盖较长的工具执行，也能避免内存无限增长。
_PROGRESS_TTL_SEC = 3600


@dataclass
class AdaptedToolRunResult:
    """统一描述适配层返回的工具执行结果。"""

    log: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    raw_output: str = ""
    elapsed_seconds: float | None = None


def get_progress_queue(conversation_run_id: str) -> queue.Queue:
    """获取会话的进度队列，并顺带清理过期队列。"""
    with _progress_lock:
        now = time.time()
        stale = [key for key, (_, created_at) in _progress_queues.items() if now - created_at > _PROGRESS_TTL_SEC]
        for key in stale:
            _progress_queues.pop(key, None)
        if conversation_run_id not in _progress_queues:
            _progress_queues[conversation_run_id] = (queue.Queue(maxsize=100), now)
        else:
            progress_queue, _ = _progress_queues[conversation_run_id]
            _progress_queues[conversation_run_id] = (progress_queue, now)
        return _progress_queues[conversation_run_id][0]


def release_progress_queue(conversation_run_id: str) -> None:
    """在一次会话执行结束后主动释放进度队列。"""
    with _progress_lock:
        _progress_queues.pop(conversation_run_id, None)


def parse_tool_result_payload(result_str: str) -> AdaptedToolRunResult:
    """把适配层格式化后的字符串结果还原为结构化对象。

    LangChain 工具调用最终只能返回字符串，因此这里约定：
    - 人类可读日志仍然保留在文本里；
    - 机器可读字段追加在 `Result JSON:` 前缀之后。
    """
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
    """识别错误字符串协议，并抛出统一的领域异常。"""
    match = _ERROR_RESULT_RE.match(result_str.strip())
    if not match:
        return
    code = match.group("code") or ErrorCode.ENGINE_EXEC_FAILED
    raise PDFAgentError(code=code, message=match.group("message").strip())


def format_tool_result_payload(
    result: ToolResult,
    *,
    elapsed_seconds: float,
    sensitive_params: Iterable[str] = (),
) -> str:
    """把工具结果编码成 SSE 层和工具节点都能消费的文本协议。

    这样做的原因是当前 LangChain/LangGraph 工具调用链主要以字符串透传结果，
    所以这里需要显式附加一段 JSON 负载，供后续节点恢复输出文件、元数据和耗时。
    """
    output_files = [str(path) for path in result.output_files]
    redacted = set(sensitive_params)
    safe_meta = {key: value for key, value in (result.meta or {}).items() if key not in redacted}
    payload = {
        "log": result.log,
        "meta": safe_meta,
        "output_files": output_files,
        "elapsed_seconds": elapsed_seconds,
    }
    parts = []
    if result.log:
        parts.append(result.log)
    if safe_meta:
        parts.append(f"Metadata: {json.dumps(safe_meta, ensure_ascii=False, default=str)}")
    parts.append(f"{_RESULT_JSON_PREFIX} {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(parts) if parts else "Done (no output)."
