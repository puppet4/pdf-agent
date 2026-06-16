"""FastAPI 应用入口，负责装配运行时依赖与生命周期管理。"""
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
from psycopg.rows import dict_row
from sqlalchemy import select

from pdf_agent.config import settings, validate_settings
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
        """为每条日志补齐 request_id，便于跨请求追踪。"""
        record.request_id = getattr(record, "request_id", get_request_id())
        return True


def _configure_logging():
    """配置带 `request_id` 的结构化日志格式。"""
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
    """把异步 SQLAlchemy/Postgres URL 转成 psycopg 需要的同步格式。"""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _cleanup_conversation_checkpoints(checkpointer, conversation_ids: list[str]) -> int:
    """删除指定会话对应的持久化 checkpoint 状态。"""
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
    """删除过期会话目录，并尽量同步清理对应的 checkpoint 状态。"""
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
    """删除磁盘上已经不存在的上传目录对应的数据库记录。"""
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


async def _cleanup_trimmed_storage(app: FastAPI, removed_conversation_ids: list[str], removed_upload_ids: list[str]) -> tuple[int, int]:
    """在 LRU 裁剪存储后，同步修复数据库和 checkpoint 的残留状态。"""
    removed_uploads = await _cleanup_upload_records(removed_upload_ids)
    removed_checkpoints = await _cleanup_conversation_checkpoints(
        getattr(app.state, "checkpointer", None),
        removed_conversation_ids,
    )
    return removed_uploads, removed_checkpoints


def _is_backend_connection_error(exc: Exception) -> bool:
    """判断异常是否更像后端连接失败，而不是本地逻辑 bug。"""
    if isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError)):
        return True
    lowered = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "connection",
        "connect",
        "timeout",
        "refused",
        "reset by peer",
        "server closed",
        "couldn't get a connection",
    )
    return any(marker in lowered for marker in markers)


async def _reconcile_idempotency_drift() -> tuple[int, int]:
    from pdf_agent.services.idempotency import idempotency_service
    from pdf_agent.api.metrics import metrics

    try:
        stats = await idempotency_service.reconcile_file_upload_processing()
        if stats.fixed_success:
            metrics.record_idempotency_event(scope="file_upload", action="reconciled_success")
        if stats.fixed_failed:
            metrics.record_idempotency_event(scope="file_upload", action="reconciled_failed")
        return stats.fixed_success, stats.fixed_failed
    except Exception as exc:
        if _is_backend_connection_error(exc):
            logger.warning("Idempotency reconciliation skipped because backend is unavailable: %s", exc)
        else:
            logger.warning("Idempotency reconciliation skipped because backend is unavailable", exc_info=True)
        metrics.record_degradation(path="system", reason="idempotency_reconcile_backend_unavailable")
        return (0, 0)


async def _cleanup_loop(app: FastAPI):
    """后台定期清理过期上传、陈旧会话和存储压力导致的残留状态。"""
    from pdf_agent.storage import storage

    while True:
        # 固定按小时轮询，避免把清理逻辑压到每次请求上执行。
        await asyncio.sleep(3600)
        try:
            removed_conversations = await _cleanup_expired_conversations_with_checkpointer(
                getattr(app.state, "checkpointer", None)
            )
            removed_upload_ids = storage.cleanup_expired_uploads()
            removed_uploads = await _cleanup_upload_records(removed_upload_ids)
            trim_result = storage.trim_storage_lru_details()
            trimmed_uploads, trimmed_checkpoints = await _cleanup_trimmed_storage(
                app,
                trim_result.removed_conversation_ids,
                trim_result.removed_upload_ids,
            )
            if removed_conversations or removed_uploads or trim_result.total_removed:
                logger.info(
                    "Cleaned up %d expired conversation(s), %d expired upload(s), trimmed %d dir(s), synced %d upload record(s), %d checkpoint(s)",
                    removed_conversations,
                    removed_uploads,
                    trim_result.total_removed,
                    trimmed_uploads,
                    trimmed_checkpoints,
                )
            reconciled_success, reconciled_failed = await _reconcile_idempotency_drift()
            if reconciled_success or reconciled_failed:
                logger.warning(
                    "Idempotency reconciliation fixed success=%d failed=%d stale records",
                    reconciled_success,
                    reconciled_failed,
                )
        except Exception:
            logger.exception("Cleanup failed")


def _setup_langsmith():
    """在配置了 API Key 时开启 LangSmith tracing。"""
    if not settings.langsmith_api_key:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
    logger.info("LangSmith tracing enabled (project=%s)", settings.langsmith_project)


def _setup_sentry():
    """在配置了 DSN 时初始化 Sentry 错误追踪。"""
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
    """管理应用启动与关闭期间的依赖初始化、清理和资源释放。"""

    # 启动阶段先准备目录、监控和工具注册，确保后续 API 依赖可用。
    logger.info("Starting %s ...", settings.app_name)
    validate_settings()
    settings.ensure_dirs()
    _setup_sentry()
    auth_policy = settings.auth_policy
    logger.info(
        "Authentication policy: enabled=%s mode=%s env=%s (%s)",
        auth_policy.enabled,
        auth_policy.mode,
        settings.environment,
        auth_policy.reason,
    )

    if len(registry) == 0:
        load_builtin_tools()
    logger.info("Loaded %d tools", len(registry))

    # 先做一次启动清理，把上次异常退出遗留的过期目录和记录收干净。
    from pdf_agent.storage import storage
    expired_conversation_ids = storage.list_expired_conversations()
    removed = await _cleanup_expired_conversations_with_checkpointer(None, conversation_ids=expired_conversation_ids)
    if removed:
        logger.info("Startup cleanup: removed %d expired conversation(s)", removed)
    removed_upload_ids = storage.cleanup_expired_uploads()
    removed_uploads = await _cleanup_upload_records(removed_upload_ids)
    if removed_uploads:
        logger.info("Startup cleanup: removed %d expired upload(s)", removed_uploads)
    trim_result = storage.trim_storage_lru_details()
    if trim_result.total_removed:
        logger.info("Startup cleanup: trimmed %d old storage dir(s)", trim_result.total_removed)

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
                # LangGraph 持久化依赖 psycopg 连接池；初始化失败时直接降级为无持久化模式。
                pool = AsyncConnectionPool(
                    conninfo=_sync_database_url(settings.database_url),
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                    },
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

            # 即使 checkpoint 不可用，只要模型可用，agent 仍可退化为无持久化运行。
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

    # 二次兜底清理：处理在存储裁剪过程中被删掉、但 checkpoint 仍残留的会话状态。
    trimmed_conversation_ids = trim_result.removed_conversation_ids if 'trim_result' in locals() else []
    checkpoint_cleanup_ids = list(dict.fromkeys([*expired_conversation_ids, *trimmed_conversation_ids]))
    if app.state.checkpointer is not None and checkpoint_cleanup_ids:
        removed_checkpoints = await _cleanup_conversation_checkpoints(app.state.checkpointer, checkpoint_cleanup_ids)
        if removed_checkpoints:
            logger.info("Startup cleanup: removed %d expired checkpoint conversation(s)", removed_checkpoints)
    if trim_result.removed_upload_ids:
        trimmed_uploads = await _cleanup_upload_records(trim_result.removed_upload_ids)
        if trimmed_uploads:
            logger.info("Startup cleanup: removed %d LRU-trimmed upload record(s)", trimmed_uploads)

    reconciled_success, reconciled_failed = await _reconcile_idempotency_drift()
    if reconciled_success or reconciled_failed:
        logger.warning(
            "Startup idempotency reconciliation fixed success=%d failed=%d stale records",
            reconciled_success,
            reconciled_failed,
        )

    # 后台清理任务独立运行，不阻塞主应用启动。
    cleanup_task = asyncio.create_task(_cleanup_loop(app))

    yield

    # 关闭阶段按相反顺序释放后台任务与外部连接池。
    logger.info("Shutting down %s", settings.app_name)
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
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

# 中间件按“越通用越外层”的顺序注册，保证指标、鉴权和请求追踪行为稳定。
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
if settings.cors_origin_list == ["*"]:
    logger.warning("CORS allows all origins (cors_origins='*'). Set PDF_AGENT_CORS_ORIGINS for production.")

# 统一挂载 API 路由。
app.include_router(api_router)


@app.exception_handler(PDFAgentError)
async def pdf_agent_error_handler(request: Request, exc: PDFAgentError) -> JSONResponse:
    """把领域异常转换成带本地化消息的 HTTP JSON 响应。"""
    from pdf_agent.core import error_http_status, localized_error
    accept_lang = request.headers.get("Accept-Language", "")
    locale = "zh" if "zh" in accept_lang else settings.default_locale
    return JSONResponse(
        status_code=error_http_status(exc.code),
        content={
            "error_code": exc.code,
            "message": localized_error(exc.code, exc.message, locale),
        },
    )
