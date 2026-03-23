"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import logging.config
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select

from pdf_agent.config import settings
from pdf_agent.core import PDFAgentError
from pdf_agent.api.router import api_router
from pdf_agent.api.middleware import (
    ApiKeyMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    get_request_id,
)
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import FileRecord
from pdf_agent.tools.registry import load_builtin_tools, registry


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", get_request_id())
        return True


def _configure_logging():
    """Configure JSON structured logging with request_id support."""
    log_format = (
        '{"time": "%(asctime)s", "level": "%(levelname)s", '
        '"name": "%(name)s", "request_id": "%(request_id)s", "message": %(message)r}'
        if not settings.debug
        else "%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s"
    )
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format=log_format,
        force=True,
    )
    request_id_filter = _RequestIdFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(request_id_filter)


_configure_logging()
logger = logging.getLogger(__name__)


def _sync_database_url(database_url: str) -> str:
    """Convert async SQLAlchemy/Postgres URLs into a sync psycopg URL."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _cleanup_conversation_checkpoints(checkpointer, conversation_ids: list[str]) -> int:
    """Delete persisted checkpoint state for known conversation ids."""
    if checkpointer is None:
        return 0

    removed = 0
    for conversation_id in conversation_ids:
        try:
            await checkpointer.adelete_thread(conversation_id)
            removed += 1
        except Exception:
            logger.warning("Failed to clean up checkpoint state for %s", conversation_id, exc_info=True)
    return removed


async def _cleanup_expired_conversations_with_checkpointer(
    checkpointer,
    conversation_ids: list[str] | None = None,
) -> int:
    """Delete expired conversation workdirs and matching checkpoint state."""
    from pdf_agent.storage import storage

    expired = conversation_ids if conversation_ids is not None else storage.list_expired_conversations()
    removed = 0
    for conversation_id in expired:
        try:
            storage.cleanup_conversation(conversation_id)
            removed += 1
            if checkpointer is not None:
                try:
                    await checkpointer.adelete_thread(conversation_id)
                except Exception:
                    logger.warning("Failed to clean up checkpoint state for %s", conversation_id, exc_info=True)
        except Exception:
            logger.exception("Failed to clean up expired conversation %s", conversation_id)
    return removed


async def _cleanup_upload_records(upload_ids: list[str]) -> int:
    """Delete FileRecord rows for upload directories already removed from disk."""
    parsed_ids: list[uuid.UUID] = []
    for upload_id in upload_ids:
        try:
            parsed_ids.append(uuid.UUID(upload_id))
        except ValueError:
            continue
    if not parsed_ids:
        return 0

    async with async_session_factory() as session:
        result = await session.execute(select(FileRecord).where(FileRecord.id.in_(parsed_ids)))
        records = result.scalars().all()
        for record in records:
            await session.delete(record)
        await session.commit()
        return len(records)


async def _cleanup_loop(app: FastAPI):
    """Periodically clean up expired uploads, stale conversation state, and storage pressure."""
    from pdf_agent.storage import storage

    while True:
        await asyncio.sleep(3600)  # every hour
        try:
            removed_conversations = await _cleanup_expired_conversations_with_checkpointer(
                getattr(app.state, "checkpointer", None)
            )
            removed_upload_ids = storage.cleanup_expired_uploads()
            removed_uploads = await _cleanup_upload_records(removed_upload_ids)
            trimmed = storage.trim_storage_lru()
            if removed_conversations or removed_uploads or trimmed:
                logger.info(
                    "Cleaned up %d conversation(s), %d upload(s), trimmed %d dir(s)",
                    removed_conversations,
                    removed_uploads,
                    trimmed,
                )
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
    _setup_sentry()

    if len(registry) == 0:
        load_builtin_tools()
    logger.info("Loaded %d tools", len(registry))

    # Run initial cleanup
    from pdf_agent.storage import storage
    expired_conversation_ids = storage.list_expired_conversations()
    removed = await _cleanup_expired_conversations_with_checkpointer(None, conversation_ids=expired_conversation_ids)
    if removed:
        logger.info("Startup cleanup: removed %d expired conversation(s)", removed)
    removed_upload_ids = storage.cleanup_expired_uploads()
    removed_uploads = await _cleanup_upload_records(removed_upload_ids)
    if removed_uploads:
        logger.info("Startup cleanup: removed %d expired upload(s)", removed_uploads)
    trimmed = storage.trim_storage_lru()
    if trimmed:
        logger.info("Startup cleanup: trimmed %d old storage dir(s)", trimmed)

    app.state.graph = None
    app.state.pool = None
    app.state.checkpointer = None

    if settings.openai_api_key:
        _setup_langsmith()

        pool: AsyncConnectionPool | None = None
        checkpointer = None

        if settings.disable_agent_persistence:
            logger.info("Agent persistence disabled by configuration")
        else:
            try:
                pool = AsyncConnectionPool(
                    conninfo=_sync_database_url(settings.database_url),
                    max_size=20,
                    open=False,
                )
                await pool.open()

                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                checkpointer = AsyncPostgresSaver(pool)
                try:
                    await checkpointer.setup()
                except Exception as exc:
                    logger.warning("Checkpointer setup failed; disabling persistence: %s", exc)
                    checkpointer = None
                    await pool.close()
                    pool = None
            except Exception as exc:
                logger.warning("Checkpointer unavailable; agent memory persistence disabled: %s", exc)
                if pool is not None:
                    await pool.close()
                    pool = None
                checkpointer = None

        try:
            from pdf_agent.agent.graph import build_graph

            app.state.graph = build_graph(checkpointer, registry)
            app.state.pool = pool
            app.state.checkpointer = checkpointer
            logger.info(
                "LangGraph agent initialized with model=%s persistence=%s",
                settings.openai_model,
                "postgres" if checkpointer is not None else "disabled",
            )
        except Exception as exc:
            logger.warning("Agent initialization failed; agent endpoints disabled: %s", exc)
            if pool is not None:
                await pool.close()
            app.state.graph = None
            app.state.pool = None
            app.state.checkpointer = None
    else:
        logger.info("OpenAI API key not configured; agent endpoints disabled")

    # Catch any expired conversations that may still have persisted checkpoint state.
    if app.state.checkpointer is not None and expired_conversation_ids:
        removed_checkpoints = await _cleanup_conversation_checkpoints(app.state.checkpointer, expired_conversation_ids)
        if removed_checkpoints:
            logger.info("Startup cleanup: removed %d expired checkpoint conversation(s)", removed_checkpoints)

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_loop(app))

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.app_name)
    cleanup_task.cancel()
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        await pool.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.expose_api_docs else None,
    redoc_url="/redoc" if settings.expose_api_docs else None,
    openapi_url="/openapi.json" if settings.expose_api_docs else None,
)
app.state.graph = None
app.state.pool = None
app.state.checkpointer = None

# Middleware (outermost first)
if settings.metrics_enabled:
    from pdf_agent.api.metrics import MetricsMiddleware
    app.add_middleware(MetricsMiddleware)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(api_router)


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
