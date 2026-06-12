"""Runtime contract tests for FastAPI app startup and cleanup helpers."""
from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
import uuid

import pytest

from pdf_agent import main
from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.storage import StorageTrimResult


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)


class _UploadRecordSession:
    def __init__(self, rows):
        self.rows = rows
        self.deleted = []
        self.committed = False

    async def execute(self, _query):
        return _QueryResult(self.rows)

    async def delete(self, record):
        self.deleted.append(record)

    async def commit(self):
        self.committed = True


class _Checkpointer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[str] = []

    async def adelete_thread(self, conversation_id: str) -> None:
        if self.fail:
            raise RuntimeError("checkpoint down")
        self.deleted.append(conversation_id)


def test_request_id_filter_sync_database_url_and_tracing_setup(monkeypatch: pytest.MonkeyPatch):
    record = logging.LogRecord("name", logging.INFO, "path", 1, "msg", (), None)
    assert main._RequestIdFilter().filter(record) is True
    assert record.request_id
    assert main._sync_database_url("postgresql+asyncpg://user:pass@db/app") == "postgresql://user:pass@db/app"

    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    monkeypatch.setattr(settings, "langsmith_api_key", "langsmith-key")
    monkeypatch.setattr(settings, "langsmith_project", "pdf-agent-test")

    main._setup_langsmith()

    assert main.os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert main.os.environ["LANGCHAIN_API_KEY"] == "langsmith-key"
    assert main.os.environ["LANGCHAIN_PROJECT"] == "pdf-agent-test"


def test_setup_sentry_success_and_disabled(monkeypatch: pytest.MonkeyPatch):
    init_calls: list[dict[str, object]] = []
    monkeypatch.setattr(settings, "sentry_dsn", "")
    main._setup_sentry()

    monkeypatch.setattr(settings, "sentry_dsn", "https://sentry.example.test/1")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(init=lambda **kwargs: init_calls.append(kwargs)),
    )

    main._setup_sentry()

    assert init_calls == [
        {
            "dsn": "https://sentry.example.test/1",
            "traces_sample_rate": 0.1,
            "environment": "development",
        }
    ]

    real_import = __import__

    def reject_sentry(name, *args, **kwargs):
        if name == "sentry_sdk":
            raise ImportError("sentry missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sentry_sdk", raising=False)
    monkeypatch.setattr("builtins.__import__", reject_sentry)
    main._setup_sentry()


@pytest.mark.asyncio
async def test_cleanup_helpers_remove_checkpoints_conversations_upload_records_and_trimmed_state(
    monkeypatch: pytest.MonkeyPatch,
):
    checkpointer = _Checkpointer()
    assert await main._cleanup_conversation_checkpoints(None, ["a"]) == 0
    assert await main._cleanup_conversation_checkpoints(checkpointer, ["a", "b"]) == 2
    assert checkpointer.deleted == ["a", "b"]
    assert await main._cleanup_conversation_checkpoints(_Checkpointer(fail=True), ["a"]) == 0

    cleaned: list[str] = []

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return ["old-1", "old-2"]

        def cleanup_conversation(self, conversation_id: str) -> None:
            cleaned.append(conversation_id)
            if conversation_id == "old-2":
                raise RuntimeError("disk error")

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult()

    import pdf_agent.storage as storage_module

    monkeypatch.setattr(storage_module, "storage", _Storage())
    assert await main._cleanup_expired_conversations_with_checkpointer(checkpointer) == 1
    assert cleaned == ["old-1", "old-2"]

    cleaned.clear()

    class _CleanStorage:
        def list_expired_conversations(self) -> list[str]:
            return ["old-3"]

        def cleanup_conversation(self, conversation_id: str) -> None:
            cleaned.append(conversation_id)

    monkeypatch.setattr(storage_module, "storage", _CleanStorage())
    assert await main._cleanup_expired_conversations_with_checkpointer(_Checkpointer(fail=True)) == 1
    assert cleaned == ["old-3"]

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    rows = [SimpleNamespace(id=first_id), SimpleNamespace(id=second_id)]
    upload_session = _UploadRecordSession(rows)
    monkeypatch.setattr(main, "async_session_factory", lambda: _AsyncSessionContext(upload_session))

    assert await main._cleanup_upload_records(["not-a-uuid"]) == 0
    assert await main._cleanup_upload_records([str(first_id), str(second_id)]) == 2
    assert upload_session.deleted == rows
    assert upload_session.committed is True

    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(len(upload_ids)))
    monkeypatch.setattr(main, "_cleanup_conversation_checkpoints", lambda cp, ids: _async_value(len(ids)))
    app = SimpleNamespace(state=SimpleNamespace(checkpointer=checkpointer))
    trimmed = await main._cleanup_trimmed_storage(app, ["c1", "c2"], ["u1"])
    assert trimmed == (1, 2)


@pytest.mark.asyncio
async def test_reconcile_idempotency_drift_success_and_degraded_failure(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple[str, str]] = []
    degradations: list[tuple[str, str]] = []

    class _Metrics:
        def record_idempotency_event(self, *, scope: str, action: str) -> None:
            events.append((scope, action))

        def record_degradation(self, *, path: str, reason: str) -> None:
            degradations.append((path, reason))

    class _Stats:
        fixed_success = 2
        fixed_failed = 1

    class _Idempotency:
        async def reconcile_file_upload_processing(self):
            return _Stats()

    import pdf_agent.api.metrics as metrics_module
    import pdf_agent.services.idempotency as idem_module

    monkeypatch.setattr(metrics_module, "metrics", _Metrics())
    monkeypatch.setattr(idem_module, "idempotency_service", _Idempotency())
    assert await main._reconcile_idempotency_drift() == (2, 1)
    assert events == [
        ("file_upload", "reconciled_success"),
        ("file_upload", "reconciled_failed"),
    ]

    class _FailingIdempotency:
        async def reconcile_file_upload_processing(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(idem_module, "idempotency_service", _FailingIdempotency())
    assert await main._reconcile_idempotency_drift() == (0, 0)
    assert degradations == [("system", "idempotency_reconcile_backend_unavailable")]


@pytest.mark.asyncio
async def test_cleanup_loop_runs_one_successful_cycle_then_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
):
    sleeps = 0

    async def fake_sleep(_seconds: int):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    class _Storage:
        def cleanup_expired_uploads(self) -> list[str]:
            return ["upload-1"]

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult(removed_conversation_ids=["conversation-1"], removed_upload_ids=["upload-2"])

    import pdf_agent.storage as storage_module

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda cp: _async_value(1))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(len(upload_ids)))
    monkeypatch.setattr(main, "_cleanup_trimmed_storage", lambda app, conversations, uploads: _async_value((len(uploads), len(conversations))))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((1, 1)))

    with pytest.raises(asyncio.CancelledError):
        await main._cleanup_loop(SimpleNamespace(state=SimpleNamespace(checkpointer=None)))


@pytest.mark.asyncio
async def test_cleanup_loop_logs_and_continues_after_cycle_error(monkeypatch: pytest.MonkeyPatch):
    sleeps = 0

    async def fake_sleep(_seconds: int):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    class _Storage:
        def cleanup_expired_uploads(self) -> list[str]:
            raise RuntimeError("disk offline")

    import pdf_agent.storage as storage_module

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda cp: _async_value(0))

    with pytest.raises(asyncio.CancelledError):
        await main._cleanup_loop(SimpleNamespace(state=SimpleNamespace(checkpointer=None)))


@pytest.mark.asyncio
async def test_pdf_agent_error_handler_localizes_response(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(headers={"Accept-Language": "zh-CN"})

    response = await main.pdf_agent_error_handler(
        request,  # type: ignore[arg-type]
        PDFAgentError(ErrorCode.INVALID_PARAMS, "bad input"),
    )

    assert response.status_code == 422
    assert b"INVALID_PARAMS" in response.body


@pytest.mark.asyncio
async def test_lifespan_starts_without_openai_key_and_cleans_up_background_task(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(1))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(len(upload_ids)))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((0, 0)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return ["expired"]

        def cleanup_expired_uploads(self) -> list[str]:
            return ["upload-1"]

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult(removed_upload_ids=["trimmed-upload"])

    import pdf_agent.storage as storage_module

    monkeypatch.setattr(storage_module, "storage", _Storage())

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph is None
        assert app.state.pool is None
        assert app.state.checkpointer is None


@pytest.mark.asyncio
async def test_lifespan_builds_graph_when_openai_key_is_present_and_persistence_disabled(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "disable_agent_persistence", True)
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_setup_langsmith", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(0))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(0))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((0, 0)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return []

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult()

    import pdf_agent.agent.graph as graph_module
    import pdf_agent.storage as storage_module

    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(graph_module, "build_graph", lambda checkpointer, registry: {"checkpointer": checkpointer})

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph == {"checkpointer": None}
        assert app.state.pool is None
        assert app.state.checkpointer is None


@pytest.mark.asyncio
async def test_lifespan_closes_pool_when_graph_initialization_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "disable_agent_persistence", False)
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_setup_langsmith", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(0))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(0))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((0, 0)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return []

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult()

    class _Pool:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        async def open(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True
            closed_pools.append(self)

    class _Saver:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def setup(self) -> None:
            return None

    import langgraph.checkpoint.postgres.aio as postgres_module
    import pdf_agent.agent.graph as graph_module
    import pdf_agent.storage as storage_module

    closed_pools: list[_Pool] = []
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "AsyncConnectionPool", _Pool)
    monkeypatch.setattr(postgres_module, "AsyncPostgresSaver", _Saver)
    monkeypatch.setattr(graph_module, "build_graph", lambda _checkpointer, _registry: (_ for _ in ()).throw(RuntimeError("graph bad")))

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph is None
        assert app.state.pool is None
        assert app.state.checkpointer is None

    assert closed_pools and closed_pools[0].closed is True


@pytest.mark.asyncio
async def test_lifespan_persistence_setup_failure_falls_back_and_closes_pool(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "disable_agent_persistence", False)
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_setup_langsmith", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(0))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(0))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((0, 0)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return []

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult()

    class _Pool:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        async def open(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True
            closed_pools.append(self)

    class _Saver:
        def __init__(self, pool) -> None:
            self.pool = pool

        async def setup(self) -> None:
            raise RuntimeError("setup failed")

    import langgraph.checkpoint.postgres.aio as postgres_module
    import pdf_agent.agent.graph as graph_module
    import pdf_agent.storage as storage_module

    closed_pools: list[_Pool] = []
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "AsyncConnectionPool", _Pool)
    monkeypatch.setattr(postgres_module, "AsyncPostgresSaver", _Saver)
    monkeypatch.setattr(graph_module, "build_graph", lambda checkpointer, _registry: {"checkpointer": checkpointer})

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph == {"checkpointer": None}
        assert app.state.pool is None
        assert app.state.checkpointer is None

    assert closed_pools and closed_pools[0].closed is True


@pytest.mark.asyncio
async def test_lifespan_persistence_pool_open_failure_falls_back_and_closes_pool(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "disable_agent_persistence", False)
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_setup_langsmith", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(0))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(0))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((0, 0)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return []

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult()

    class _Pool:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        async def open(self) -> None:
            raise RuntimeError("pool open failed")

        async def close(self) -> None:
            self.closed = True
            closed_pools.append(self)

    import pdf_agent.agent.graph as graph_module
    import pdf_agent.storage as storage_module

    closed_pools: list[_Pool] = []
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "AsyncConnectionPool", _Pool)
    monkeypatch.setattr(graph_module, "build_graph", lambda checkpointer, _registry: {"checkpointer": checkpointer})

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph == {"checkpointer": None}
        assert app.state.pool is None
        assert app.state.checkpointer is None

    assert closed_pools and closed_pools[0].closed is True


@pytest.mark.asyncio
async def test_lifespan_successful_persistence_cleans_checkpoints_reconciles_and_closes_pool(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "disable_agent_persistence", False)
    monkeypatch.setattr(main, "validate_settings", lambda: None)
    monkeypatch.setattr(main, "_setup_sentry", lambda: None)
    monkeypatch.setattr(main, "_setup_langsmith", lambda: None)
    monkeypatch.setattr(main, "_cleanup_expired_conversations_with_checkpointer", lambda *args, **kwargs: _async_value(0))
    monkeypatch.setattr(main, "_cleanup_upload_records", lambda upload_ids: _async_value(0))
    monkeypatch.setattr(main, "_reconcile_idempotency_drift", lambda: _async_value((1, 1)))

    class _Storage:
        def list_expired_conversations(self) -> list[str]:
            return ["expired-conversation"]

        def cleanup_expired_uploads(self) -> list[str]:
            return []

        def trim_storage_lru_details(self) -> StorageTrimResult:
            return StorageTrimResult(removed_conversation_ids=["trimmed-conversation"])

    class _Pool:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        async def open(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True
            closed_pools.append(self)

    class _Saver:
        def __init__(self, pool) -> None:
            self.pool = pool
            self.deleted: list[str] = []

        async def setup(self) -> None:
            return None

        async def adelete_thread(self, conversation_id: str) -> None:
            self.deleted.append(conversation_id)

    import langgraph.checkpoint.postgres.aio as postgres_module
    import pdf_agent.agent.graph as graph_module
    import pdf_agent.storage as storage_module

    closed_pools: list[_Pool] = []
    monkeypatch.setattr(storage_module, "storage", _Storage())
    monkeypatch.setattr(main, "AsyncConnectionPool", _Pool)
    monkeypatch.setattr(postgres_module, "AsyncPostgresSaver", _Saver)
    monkeypatch.setattr(graph_module, "build_graph", lambda checkpointer, _registry: {"checkpointer": checkpointer})

    async def neverending_cleanup(_app):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_cleanup_loop", neverending_cleanup)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.checkpointer.deleted == ["expired-conversation", "trimmed-conversation"]
        assert app.state.pool is not None

    assert closed_pools and closed_pools[-1].closed is True


async def _async_value(value):
    return value
