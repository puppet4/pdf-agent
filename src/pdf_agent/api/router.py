"""Main router - aggregates all API routers."""
from __future__ import annotations

from fastapi import APIRouter

from pdf_agent.api.health import router as health_router
from pdf_agent.api.tools import router as tools_router
from pdf_agent.api.files import router as files_router
from pdf_agent.api.agent import router as agent_router
from pdf_agent.api.metrics import router as metrics_router
from pdf_agent.api.workflows import router as workflows_router
from pdf_agent.config import settings

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(tools_router)
api_router.include_router(files_router)
api_router.include_router(agent_router)
api_router.include_router(workflows_router)

if settings.metrics_enabled:
    api_router.include_router(metrics_router)
