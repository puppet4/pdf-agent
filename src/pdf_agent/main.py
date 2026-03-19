"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import logging.config
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import AsyncConnectionPool

from pdf_agent.config import settings
from pdf_agent.core import PDFAgentError
from pdf_agent.api.router import api_router
from pdf_agent.api.middleware import ApiKeyMiddleware, JWTMiddleware, RateLimitMiddleware, RequestIdMiddleware
from pdf_agent.tools.registry import load_builtin_tools, registry


def _configure_logging():
    """Configure JSON structured logging with request_id support."""
    log_format = (
        '{"time": "%(asctime)s", "level": "%(levelname)s", '
        '"name": "%(name)s", "message": %(message)r}'
        if not settings.debug
        else "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format=log_format,
    )


_configure_logging()
logger = logging.getLogger(__name__)


async def _cleanup_loop():
    """Periodically clean up expired thread workdirs and uploaded files."""
    from pdf_agent.storage import storage

    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            removed_threads = storage.cleanup_expired_threads()
            removed_uploads = storage.cleanup_expired_uploads()
            if removed_threads or removed_uploads:
                logger.info("Cleaned up %d thread(s), %d upload(s)", removed_threads, removed_uploads)
        except Exception:
            logger.exception("Cleanup failed")


def _setup_langsmith():
    """Configure LangSmith tracing via environment variables if API key is set."""
    if not settings.langsmith_api_key:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
    logger.info("LangSmith tracing enabled (project=%s)", settings.langsmith_project)


def _setup_sentry():
    """Initialize Sentry error tracking if DSN is configured."""
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=0.1,
            environment="production" if not settings.debug else "development",
        )
        logger.info("Sentry error tracking enabled")
    except ImportError:
        logger.warning("sentry-sdk not installed — Sentry disabled. Run: pip install sentry-sdk")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting %s ...", settings.app_name)
    settings.ensure_dirs()

    if not settings.openai_api_key:
        raise RuntimeError(
            "PDF_AGENT_OPENAI_API_KEY is not set. "
            "Please set it via environment variable or .env file."
        )

    _setup_langsmith()
    _setup_sentry()

    load_builtin_tools()
    logger.info("Loaded %d tools", len(registry))

    # Run initial cleanup
    from pdf_agent.storage import storage
    removed = storage.cleanup_expired_threads()
    if removed:
        logger.info("Startup cleanup: removed %d expired thread(s)", removed)

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

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.app_name)
    cleanup_task.cancel()
    await pool.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware (outermost first)
if settings.metrics_enabled:
    from pdf_agent.api.metrics import MetricsMiddleware
    app.add_middleware(MetricsMiddleware)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(JWTMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(api_router)

# Static frontend
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")


@app.exception_handler(PDFAgentError)
async def pdf_agent_error_handler(request: Request, exc: PDFAgentError) -> JSONResponse:
    from pdf_agent.core import localized_error
    # Use Accept-Language header to pick locale
    accept_lang = request.headers.get("Accept-Language", "")
    locale = "zh" if "zh" in accept_lang else settings.default_locale
    return JSONResponse(
        status_code=400,
        content={
            "error_code": exc.code,
            "message": localized_error(exc.code, exc.message, locale),
        },
    )
