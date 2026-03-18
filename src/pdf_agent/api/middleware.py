"""API middleware — authentication and rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from pdf_agent.config import settings

# Paths that skip authentication
_PUBLIC_PATHS = {"/healthz", "/docs", "/redoc", "/openapi.json"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header (or api_key query param) when api_key is configured."""

    async def dispatch(self, request: Request, call_next):
        if not settings.api_key:
            return await call_next(request)

        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if provided != settings.api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding window rate limiter for the chat endpoint."""

    _instance: RateLimitMiddleware | None = None

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, list[float]] = defaultdict(list)
        RateLimitMiddleware._instance = self

    def reset(self):
        """Clear all tracked requests (useful for testing)."""
        self._requests.clear()

    async def dispatch(self, request: Request, call_next):
        if settings.rate_limit_rpm <= 0:
            return await call_next(request)

        # Only rate-limit the chat endpoint
        if request.url.path != "/api/agent/chat" or request.method != "POST":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - 60

        # Prune old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > window_start
        ]

        if len(self._requests[client_ip]) >= settings.rate_limit_rpm:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded. Max {settings.rate_limit_rpm} requests/minute."
                },
            )

        self._requests[client_ip].append(now)
        return await call_next(request)
