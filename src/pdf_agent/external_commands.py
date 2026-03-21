"""Tracked external command execution for execution cancellation support."""
from __future__ import annotations

import contextvars
import logging
import os
import signal
import subprocess
import threading
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError

logger = logging.getLogger(__name__)

_job_processes: dict[str, set[subprocess.Popen[bytes]]] = defaultdict(set)
_lock = threading.Lock()
_current_execution_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pdf_agent_current_execution_id",
    default=None,
)


@contextmanager
def bind_execution_context(execution_id: str | None):
    """Bind the current execution id so nested command calls are tracked automatically."""
    token = _current_execution_id.set(execution_id)
    try:
        yield
    finally:
        _current_execution_id.reset(token)


def run_command(
    cmd: list[str],
    *,
    job_id: str | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess while tracking it so execution cancellation can terminate it."""
    tracked_job_id = job_id or _current_execution_id.get()
    popen_kwargs = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    if tracked_job_id:
        with _lock:
            _job_processes[tracked_job_id].add(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout or settings.external_cmd_timeout_sec)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(proc)
        raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, f"Command timed out: {' '.join(cmd)}") from exc
    finally:
        if tracked_job_id:
            with _lock:
                _job_processes.get(tracked_job_id, set()).discard(proc)

    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if check and result.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or f"exit code {result.returncode}"
        raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, detail)
    return result


def cancel_job_processes(job_id: str) -> int:
    """Terminate all tracked subprocesses for the given execution id."""
    with _lock:
        processes = list(_job_processes.pop(job_id, set()))
    for proc in processes:
        _terminate_process(proc)
    return len(processes)


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        logger.warning("Failed to terminate process pid=%s cleanly", proc.pid, exc_info=True)
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            logger.exception("Failed to force kill process pid=%s", proc.pid)
