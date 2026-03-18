"""Main router - aggregates all API routers."""
from __future__ import annotations

from fastapi import APIRouter

from pdf_agent.api.health import router as health_router
from pdf_agent.api.tools import router as tools_router
from pdf_agent.api.files import router as files_router
from pdf_agent.api.agent import router as agent_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(tools_router)
api_router.include_router(files_router)
api_router.include_router(agent_router)
