"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from pdf_agent.config import settings
from pdf_agent.core import PDFAgentError
from pdf_agent.api.router import api_router
from pdf_agent.tools.registry import load_builtin_tools, registry

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
    logger.info("Loaded %d tools", len(registry))

    # Initialize LangGraph checkpointer
    pool = AsyncConnectionPool(
        conninfo=settings.checkpointer_db_url,
        max_size=20,
        open=False,
    )
    await pool.open()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    # Build and compile graph
    from pdf_agent.agent.graph import build_graph
    app.state.graph = build_graph(checkpointer, registry)
    app.state.pool = pool
    logger.info("LangGraph agent initialized with model=%s", settings.openai_model)

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.app_name)
    await pool.close()


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
