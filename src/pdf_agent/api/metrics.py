"""Prometheus metrics collection and /metrics endpoint."""
from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["metrics"])

# ---------------------------------------------------------------------------
# Simple metric storage (no external dependency required)
# ---------------------------------------------------------------------------

class _Metrics:
    """In-process metrics store with Prometheus text exposition."""

    def __init__(self):
        self._lock = threading.Lock()
        self.request_count: dict[str, int] = {}        # method:path:status
        self.request_duration: dict[str, tuple[int, float]] = {}  # method:path -> (count, total)
        self.tool_count: dict[str, int] = {}            # tool_name
        self.tool_duration: dict[str, tuple[int, float]] = {} # tool_name -> (count, total)
        self.conversation_run_count: dict[str, int] = {}
        self.conversation_duration: dict[str, tuple[int, float]] = {}
        self.llm_tokens_in: int = 0
        self.llm_tokens_out: int = 0

    def record_request(self, method: str, path: str, status: int, duration: float):
        key = f'{method}:{path}:{status}'
        with self._lock:
            self.request_count[key] = self.request_count.get(key, 0) + 1
            dur_key = f'{method}:{path}'
            count, total = self.request_duration.get(dur_key, (0, 0.0))
            self.request_duration[dur_key] = (count + 1, total + duration)

    def record_tool(self, name: str, duration: float):
        with self._lock:
            self.tool_count[name] = self.tool_count.get(name, 0) + 1
            count, total = self.tool_duration.get(name, (0, 0.0))
            self.tool_duration[name] = (count + 1, total + duration)

    def record_conversation_run(self, *, status: str, duration: float | None):
        with self._lock:
            self.conversation_run_count[status] = self.conversation_run_count.get(status, 0) + 1
            if duration is not None:
                count, total = self.conversation_duration.get(status, (0, 0.0))
                self.conversation_duration[status] = (count + 1, total + duration)

    def record_llm_tokens(self, input_tokens: int, output_tokens: int):
        with self._lock:
            self.llm_tokens_in += input_tokens
            self.llm_tokens_out += output_tokens

    def exposition(self) -> str:
        with self._lock:
            return self._exposition_unlocked()

    def _exposition_unlocked(self) -> str:
        lines: list[str] = ["# HELP pdf_agent_http_requests_total Total HTTP requests",
                            "# TYPE pdf_agent_http_requests_total counter"]

        # Request counter
        for key, count in sorted(self.request_count.items()):
            try:
                method, path, status = key.split(":", 2)
            except ValueError:
                continue
            lines.append(f'pdf_agent_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')

        # Request duration
        lines.append("# HELP pdf_agent_http_request_duration_seconds Request duration")
        lines.append("# TYPE pdf_agent_http_request_duration_seconds summary")
        for key, (count, total) in sorted(self.request_duration.items()):
            method, path = key.split(":", 1)
            lines.append(f'pdf_agent_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.4f}')
            lines.append(f'pdf_agent_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {count}')

        # Tool counter
        lines.append("# HELP pdf_agent_tool_calls_total Total tool invocations")
        lines.append("# TYPE pdf_agent_tool_calls_total counter")
        for name, count in sorted(self.tool_count.items()):
            lines.append(f'pdf_agent_tool_calls_total{{tool="{name}"}} {count}')

        # Tool duration
        lines.append("# HELP pdf_agent_tool_duration_seconds Tool run duration")
        lines.append("# TYPE pdf_agent_tool_duration_seconds summary")
        for name, (count, total) in sorted(self.tool_duration.items()):
            lines.append(f'pdf_agent_tool_duration_seconds_sum{{tool="{name}"}} {total:.4f}')
            lines.append(f'pdf_agent_tool_duration_seconds_count{{tool="{name}"}} {count}')

        lines.append("# HELP pdf_agent_conversation_runs_total Conversation run status transitions")
        lines.append("# TYPE pdf_agent_conversation_runs_total counter")
        for status, count in sorted(self.conversation_run_count.items()):
            lines.append(f'pdf_agent_conversation_runs_total{{status="{status}"}} {count}')

        lines.append("# HELP pdf_agent_conversation_run_duration_seconds Conversation run duration")
        lines.append("# TYPE pdf_agent_conversation_run_duration_seconds summary")
        for status, (count, total) in sorted(self.conversation_duration.items()):
            lines.append(f'pdf_agent_conversation_run_duration_seconds_sum{{status="{status}"}} {total:.4f}')
            lines.append(f'pdf_agent_conversation_run_duration_seconds_count{{status="{status}"}} {count}')

        # LLM tokens
        lines.append("# HELP pdf_agent_llm_tokens_total Total LLM tokens")
        lines.append("# TYPE pdf_agent_llm_tokens_total counter")
        lines.append(f'pdf_agent_llm_tokens_total{{direction="input"}} {self.llm_tokens_in}')
        lines.append(f'pdf_agent_llm_tokens_total{{direction="output"}} {self.llm_tokens_out}')

        lines.append("")
        return "\n".join(lines)


metrics = _Metrics()

_PATH_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/api/files/[^/]+/pages/\d+$"), "/api/files/{file_id}/pages/{page}"),
    (re.compile(r"^/api/files/[^/]+/(download|thumbnail)$"), "/api/files/{file_id}/{action}"),
    (re.compile(r"^/api/files/[^/]+$"), "/api/files/{file_id}"),
    (re.compile(r"^/api/conversations/[^/]+/artifacts/.+$"), "/api/conversations/{conversation_id}/artifacts/{artifact_path}"),
    (re.compile(r"^/api/conversations/[^/]+/messages$"), "/api/conversations/{conversation_id}/messages"),
    (re.compile(r"^/api/conversations/[^/]+/artifacts$"), "/api/conversations/{conversation_id}/artifacts"),
    (re.compile(r"^/api/conversations/[^/]+$"), "/api/conversations/{conversation_id}"),
]


def _normalize_metric_path(path: str, route_path: str | None = None) -> str:
    if route_path:
        return route_path
    for pattern, replacement in _PATH_NORMALIZERS:
        if pattern.match(path):
            return replacement
    return path


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count and latency for each endpoint."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        path = _normalize_metric_path(request.url.path, route_path)

        metrics.record_request(request.method, path, response.status_code, duration)
        return response


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def prometheus_metrics():
    return Response(content=metrics.exposition(), media_type="text/plain; charset=utf-8")
