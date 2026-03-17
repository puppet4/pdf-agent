"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pdf_agent.config import settings
from pdf_agent.core import PDFAgentError
from pdf_agent.api.router import api_router
from pdf_agent.tools.registry import load_builtin_tools

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting %s ...", settings.app_name)
    settings.ensure_dirs()
    load_builtin_tools()
    logger.info("Loaded %d tools", len(__import__("pdf_agent.tools.registry", fromlist=["registry"]).registry))
    yield
    # Shutdown
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (permissive for local/dev use)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all API routes
app.include_router(api_router)


# Global error handler for PDFAgentError
@app.exception_handler(PDFAgentError)
async def pdf_agent_error_handler(request: Request, exc: PDFAgentError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error_code": exc.code, "message": exc.message},
    )
