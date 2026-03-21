"""Enhanced health check — verifies DB, LLM config, and agent readiness."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.execution_queue import get_worker_state

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request):
    checks: dict = {"status": "ok"}

    # Database connectivity
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        checks["status"] = "degraded"

    # LLM configuration
    if settings.openai_api_key:
        checks["llm"] = "configured"
    else:
        checks["llm"] = "not configured"

    # Agent graph readiness
    graph = getattr(request.app.state, "graph", None)
    checks["agent"] = "ready" if graph is not None else "not initialized"
    if settings.openai_api_key and graph is None:
        checks["status"] = "degraded"

    # Tool count
    from pdf_agent.tools.registry import registry
    checks["tools_loaded"] = len(registry)
    checks.update(get_worker_state())

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(content=checks, status_code=status_code)
