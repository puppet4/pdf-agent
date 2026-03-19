"""Prometheus metrics collection and /metrics endpoint."""
from __future__ import annotations

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
        self.request_count: dict[str, int] = {}        # method:path:status
        self.request_duration: dict[str, list[float]] = {}  # method:path
        self.tool_count: dict[str, int] = {}            # tool_name
        self.tool_duration: dict[str, list[float]] = {} # tool_name
        self.llm_tokens_in: int = 0
        self.llm_tokens_out: int = 0

    def record_request(self, method: str, path: str, status: int, duration: float):
        key = f'{method}:{path}:{status}'
        self.request_count[key] = self.request_count.get(key, 0) + 1
        dur_key = f'{method}:{path}'
        self.request_duration.setdefault(dur_key, []).append(duration)

    def record_tool(self, name: str, duration: float):
        self.tool_count[name] = self.tool_count.get(name, 0) + 1
        self.tool_duration.setdefault(name, []).append(duration)

    def record_llm_tokens(self, input_tokens: int, output_tokens: int):
        self.llm_tokens_in += input_tokens
        self.llm_tokens_out += output_tokens

    def exposition(self) -> str:
        lines: list[str] = []

        # Request counter
        lines.append("# HELP pdf_agent_http_requests_total Total HTTP requests")
        lines.append("# TYPE pdf_agent_http_requests_total counter")
        for key, count in sorted(self.request_count.items()):
            method, path, status = key.split(":", 2)
            lines.append(f'pdf_agent_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')

        # Request duration
        lines.append("# HELP pdf_agent_http_request_duration_seconds Request duration")
        lines.append("# TYPE pdf_agent_http_request_duration_seconds summary")
        for key, durations in sorted(self.request_duration.items()):
            method, path = key.split(":", 1)
            total = sum(durations)
            count = len(durations)
            lines.append(f'pdf_agent_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.4f}')
            lines.append(f'pdf_agent_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {count}')

        # Tool counter
        lines.append("# HELP pdf_agent_tool_calls_total Total tool invocations")
        lines.append("# TYPE pdf_agent_tool_calls_total counter")
        for name, count in sorted(self.tool_count.items()):
            lines.append(f'pdf_agent_tool_calls_total{{tool="{name}"}} {count}')

        # Tool duration
        lines.append("# HELP pdf_agent_tool_duration_seconds Tool execution duration")
        lines.append("# TYPE pdf_agent_tool_duration_seconds summary")
        for name, durations in sorted(self.tool_duration.items()):
            total = sum(durations)
            count = len(durations)
            lines.append(f'pdf_agent_tool_duration_seconds_sum{{tool="{name}"}} {total:.4f}')
            lines.append(f'pdf_agent_tool_duration_seconds_count{{tool="{name}"}} {count}')

        # LLM tokens
        lines.append("# HELP pdf_agent_llm_tokens_total Total LLM tokens")
        lines.append("# TYPE pdf_agent_llm_tokens_total counter")
        lines.append(f'pdf_agent_llm_tokens_total{{direction="input"}} {self.llm_tokens_in}')
        lines.append(f'pdf_agent_llm_tokens_total{{direction="output"}} {self.llm_tokens_out}')

        lines.append("")
        return "\n".join(lines)


metrics = _Metrics()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    """Record request count and latency for each endpoint."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        # Normalize path to avoid cardinality explosion
        path = request.url.path
        if "/threads/" in path:
            path = "/api/agent/threads/{id}"
        elif "/files/" in path and "/api/files/" in path:
            path = "/api/files/{id}"

        metrics.record_request(request.method, path, response.status_code, duration)
        return response


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def prometheus_metrics():
    return Response(content=metrics.exposition(), media_type="text/plain; charset=utf-8")
