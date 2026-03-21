"""Main router - aggregates all API routers."""
from __future__ import annotations

from fastapi import APIRouter

from pdf_agent.api.health import router as health_router
from pdf_agent.api.files import router as files_router
from pdf_agent.api.agent import router as agent_router
from pdf_agent.api.metrics import router as metrics_router
from pdf_agent.config import settings

def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(files_router)
    router.include_router(agent_router)

    if settings.metrics_enabled:
        router.include_router(metrics_router)
    return router

api_router = build_api_router()
