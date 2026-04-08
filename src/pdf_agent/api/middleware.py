"""API middleware — authentication, rate limiting, request tracing."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from pdf_agent.config import settings

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id_var.get()


def _rate_limit_file() -> str:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return str(settings.data_dir / "rate_limit.json")


@contextmanager
def _rate_limit_lock():
    lock_path = settings.data_dir / ".rate_limit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_rate_limit_state() -> dict[str, list[float]]:
    path = settings.data_dir / "rate_limit.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    state: dict[str, list[float]] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, list):
            state[key] = [float(ts) for ts in value if isinstance(ts, (int, float))]
    return state


def _save_rate_limit_state(state: dict[str, list[float]]) -> None:
    path = settings.data_dir / "rate_limit.json"
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state), encoding="utf-8")
    tmp_path.replace(path)


def _should_rate_limit(request: Request) -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return request.url.path.startswith("/api/")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request for log tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


_AUTH_EXEMPT_PATHS = {"/healthz"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header when api_key is configured."""

    async def dispatch(self, request: Request, call_next):
        if not settings.api_key:
            return await call_next(request)

        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get("X-API-Key")
        if provided != settings.api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """File-backed sliding window rate limiter for mutating API routes."""

    _instance: RateLimitMiddleware | None = None

    def __init__(self, app):
        super().__init__(app)
        RateLimitMiddleware._instance = self

    def reset(self):
        """Clear all tracked requests (useful for testing)."""
        with _rate_limit_lock():
            path = settings.data_dir / "rate_limit.json"
            path.unlink(missing_ok=True)

    async def dispatch(self, request: Request, call_next):
        if settings.rate_limit_rpm <= 0:
            return await call_next(request)

        if not _should_rate_limit(request):
            return await call_next(request)

        client_key = request.headers.get("X-API-Key") or (request.client.host if request.client else "unknown")

        blocked = await asyncio.to_thread(self._check_and_record, client_key)
        if blocked:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded. Max {settings.rate_limit_rpm} requests/minute."
                },
            )
        return await call_next(request)

    @staticmethod
    def _check_and_record(client_key: str) -> bool:
        """Synchronous rate-limit check — runs in a thread to avoid blocking the event loop."""
        now = time.time()
        window_start = now - 60

        with _rate_limit_lock():
            state = _load_rate_limit_state()
            state = {
                key: [ts for ts in timestamps if ts > window_start]
                for key, timestamps in state.items()
            }
            state = {key: timestamps for key, timestamps in state.items() if timestamps}
            requests = state.get(client_key, [])

            if len(requests) >= settings.rate_limit_rpm:
                return True

            requests.append(now)
            state[client_key] = requests
            _save_rate_limit_state(state)
        return False
