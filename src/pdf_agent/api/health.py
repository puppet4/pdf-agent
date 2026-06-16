"""增强版健康检查，验证数据库、模型配置和 agent 就绪状态。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from pdf_agent.config import settings
from pdf_agent.db import async_session_factory

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request):
    checks: dict = {"status": "ok"}

    # 数据库连通性
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        checks["status"] = "degraded"

    # 模型配置状态
    if settings.openai_api_key:
        checks["llm"] = "configured"
    else:
        checks["llm"] = "not configured"

    # Agent 图是否已就绪
    graph = getattr(request.app.state, "graph", None)
    checkpointer = getattr(request.app.state, "checkpointer", None)
    checks["agent"] = "ready" if graph is not None else "not initialized"
    checks["checkpointer"] = "enabled" if checkpointer is not None else "disabled"
    if settings.openai_api_key and graph is None:
        checks["status"] = "degraded"

    policy = settings.auth_policy
    checks["auth"] = {
        "enabled": policy.enabled,
        "mode": policy.mode,
        "environment": settings.environment,
    }
    checks["legacy_api_compatibility_mode"] = settings.legacy_api_compatibility_mode
    checks["legacy_api_phase"] = settings.legacy_api_phase

    # 已加载工具数量
    from pdf_agent.tools.registry import registry
    checks["tools_loaded"] = len(registry)
    checks["runtime"] = "single-process"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(content=checks, status_code=status_code)
