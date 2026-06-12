from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from pdf_agent.services import idempotency as idempotency_module
from pdf_agent.services.idempotency import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_SUCCEEDED,
    IdempotencyDecision,
    IdempotencyService,
    build_request_hash,
    hash_idempotency_key,
    normalize_idempotency_key,
)


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, *, execute_results=None, commit_error: Exception | None = None):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = []
        self.executed = []
        self.execute_results = list(execute_results or [])
        self.commit_error = commit_error

    def add(self, record) -> None:
        self.added.append(record)

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, record) -> None:
        self.refreshes.append(record)
        if getattr(record, "id", None) is None:
            record.id = uuid.uuid4()

    async def execute(self, statement):
        self.executed.append(statement)
        if self.execute_results:
            return self.execute_results.pop(0)
        return _ScalarOneResult(None)


class _ScalarOneResult:
    def __init__(self, record):
        self.record = record

    def scalar_one_or_none(self):
        return self.record


class _RowcountResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _ScalarsResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _ScalarRows(self.rows)


def _record(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "scope": "scope",
        "key_hash": "key-hash",
        "request_hash": "hash",
        "status": STATUS_PROCESSING,
        "response_code": None,
        "response_body": None,
        "error_message": None,
        "updated_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _use_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    monkeypatch.setattr(idempotency_module, "async_session_factory", lambda: _AsyncSessionContext(session))


def _async_get_record(record):
    async def _inner(_session, **_kwargs):
        return record

    return _inner


def _async_bool(value: bool):
    async def _inner(**_kwargs):
        return value

    return _inner


def _async_decision(decision):
    async def _inner(**_kwargs):
        return decision

    return _inner


def test_build_request_hash_is_stable_across_dict_key_order():
    payload_a = {"b": 2, "a": 1, "nested": {"z": 9, "x": 1}}
    payload_b = {"a": 1, "nested": {"x": 1, "z": 9}, "b": 2}

    assert build_request_hash(payload_a) == build_request_hash(payload_b)


def test_normalize_idempotency_key_rejects_oversized_values(monkeypatch: pytest.MonkeyPatch):
    from pdf_agent.config import settings

    monkeypatch.setattr(settings, "idempotency_max_key_length", 8)

    with pytest.raises(ValueError, match="too long"):
        normalize_idempotency_key("123456789")


def test_normalize_idempotency_key_trims_blank_values_to_none():
    assert normalize_idempotency_key("   ") is None
    assert normalize_idempotency_key(None) is None
    assert normalize_idempotency_key("  key-1  ") == "key-1"


def test_hash_idempotency_key_normalizes_input():
    assert hash_idempotency_key("key-1") == hash_idempotency_key("  key-1  ")


def test_hash_idempotency_key_rejects_blank_values():
    with pytest.raises(ValueError, match="non-empty"):
        hash_idempotency_key("   ")


def test_parse_response_payload_rejects_empty_invalid_and_non_object_values():
    assert idempotency_module._parse_response_payload(None) is None
    assert idempotency_module._parse_response_payload("") is None
    assert idempotency_module._parse_response_payload("{") is None
    assert idempotency_module._parse_response_payload("[1]") is None
    assert idempotency_module._parse_response_payload('{"ok": true}') == {"ok": True}


@pytest.mark.asyncio
async def test_acquire_inserts_new_processing_record(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession()
    _use_session(monkeypatch, session)

    decision = await IdempotencyService().acquire(scope="uploads", key="key-1", request_hash="hash-1")

    assert decision.action == "acquired"
    assert isinstance(decision.record_id, uuid.UUID)
    assert session.added[0].scope == "uploads"
    assert session.added[0].request_hash == "hash-1"
    assert session.commits == 1
    assert session.refreshes == [session.added[0]]


@pytest.mark.asyncio
async def test_acquire_returns_conflict_when_insert_races_but_lookup_misses(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    service = IdempotencyService()
    monkeypatch.setattr(service, "_get_record", _async_get_record(None))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash-1")

    assert decision.action == "conflict"
    assert decision.message == "Idempotency key conflict"
    assert session.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (
            _record(status=STATUS_SUCCEEDED, response_code=200, response_body='{"id": "file-1"}'),
            IdempotencyDecision(action="replay", response_code=200, response_payload={"id": "file-1"}),
        ),
        (
            _record(request_hash="other-hash"),
            IdempotencyDecision(
                action="conflict",
                message="Idempotency key has already been used for a different request payload",
            ),
        ),
        (
            _record(response_code=102, response_body='{"status": "PROCESSING"}'),
            IdempotencyDecision(
                action="in_progress",
                response_code=102,
                response_payload={"status": "PROCESSING"},
                message="A request with the same idempotency key is already in progress",
            ),
        ),
    ],
)
async def test_acquire_existing_record_decisions(monkeypatch: pytest.MonkeyPatch, existing, expected):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    service = IdempotencyService()
    monkeypatch.setattr(service, "_get_record", _async_get_record(existing))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash")

    assert decision.action == expected.action
    assert decision.response_code == expected.response_code
    assert decision.response_payload == expected.response_payload
    assert decision.message == expected.message
    if expected.action in {"replay", "in_progress"}:
        assert decision.record_id == existing.id


@pytest.mark.asyncio
async def test_acquire_reopens_failed_record(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    existing = _record(status=STATUS_FAILED)
    service = IdempotencyService()
    monkeypatch.setattr(service, "_get_record", _async_get_record(existing))
    monkeypatch.setattr(service, "_reopen_failed", _async_bool(True))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash")

    assert decision.action == "acquired"
    assert decision.record_id == existing.id


@pytest.mark.asyncio
async def test_acquire_failed_record_conflicts_when_reopen_loses_race(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    service = IdempotencyService()
    monkeypatch.setattr(service, "_get_record", _async_get_record(_record(status=STATUS_FAILED)))
    monkeypatch.setattr(service, "_reopen_failed", _async_bool(False))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash")

    assert decision.action == "conflict"
    assert "Previous failed request" in decision.message


@pytest.mark.asyncio
async def test_acquire_takes_over_expired_record(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    fixed_now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    existing = _record(expires_at=fixed_now - timedelta(seconds=1))
    takeover = IdempotencyDecision(action="acquired", record_id=existing.id)
    service = IdempotencyService()
    monkeypatch.setattr(idempotency_module, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(service, "_get_record", _async_get_record(existing))
    monkeypatch.setattr(service, "_try_takeover_expired", _async_decision(takeover))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash")

    assert decision == takeover


@pytest.mark.asyncio
async def test_acquire_recovers_stale_processing_record(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession(commit_error=IntegrityError("insert", {}, Exception("duplicate")))
    _use_session(monkeypatch, session)
    fixed_now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    existing = _record(updated_at=fixed_now - timedelta(seconds=901))
    service = IdempotencyService()
    monkeypatch.setattr(idempotency_module, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(idempotency_module.settings, "idempotency_processing_timeout_sec", 900)
    monkeypatch.setattr(service, "_get_record", _async_get_record(existing))
    monkeypatch.setattr(service, "_reopen_failed", _async_bool(True))

    decision = await service.acquire(scope="uploads", key="key-1", request_hash="hash")

    assert decision.action == "acquired"
    assert decision.record_id == existing.id


@pytest.mark.asyncio
async def test_reconcile_file_upload_processing_marks_success_skipped_and_failed(
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    success_row = _record(key_hash="success", updated_at=fixed_now - timedelta(seconds=999))
    recent_row = _record(key_hash="recent", updated_at=fixed_now)
    stale_row = _record(key_hash="stale", updated_at=fixed_now - timedelta(seconds=999))
    file_record = SimpleNamespace(
        id=uuid.uuid4(),
        orig_name="invoice.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        page_count=2,
        created_at=fixed_now,
    )
    session = _FakeSession(execute_results=[
        _ScalarsResult([success_row, recent_row, stale_row]),
        _ScalarsResult([file_record]),
        _ScalarsResult([]),
        _ScalarsResult([]),
    ])
    _use_session(monkeypatch, session)
    monkeypatch.setattr(idempotency_module, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(idempotency_module.settings, "idempotency_processing_timeout_sec", 900)

    stats = await IdempotencyService().reconcile_file_upload_processing()

    assert stats.fixed_success == 1
    assert stats.skipped_recent == 1
    assert stats.fixed_failed == 1
    assert success_row.status == STATUS_SUCCEEDED
    assert success_row.response_code == 200
    assert '"orig_name": "invoice.pdf"' in success_row.response_body
    assert stale_row.status == STATUS_FAILED
    assert stale_row.response_code == 408
    assert stale_row.error_message == "Idempotency request timed out before completion"
    assert recent_row.status == STATUS_PROCESSING
    assert session.commits == 1


@pytest.mark.asyncio
async def test_mark_helpers_execute_expected_updates(monkeypatch: pytest.MonkeyPatch):
    record_id = uuid.uuid4()
    session = _FakeSession()
    _use_session(monkeypatch, session)
    service = IdempotencyService()

    await service.mark_processing(record_id=record_id, response_payload={"status": "PROCESSING"})
    await service.mark_succeeded(record_id=record_id, response_code=201, response_payload={"ok": True})
    await service.mark_failed(record_id=record_id, response_code=500, error_message="x" * 600)

    assert len(session.executed) == 3
    assert session.commits == 3


@pytest.mark.asyncio
async def test_mark_uses_external_session_without_opening_factory(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession()
    monkeypatch.setattr(
        idempotency_module,
        "async_session_factory",
        lambda: pytest.fail("external session should be used"),
    )

    await IdempotencyService()._mark(
        record_id=uuid.uuid4(),
        status=STATUS_SUCCEEDED,
        response_code=200,
        response_payload={"ok": True},
        error_message=None,
        external_session=session,
    )

    assert len(session.executed) == 1
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rowcount, expected", [(1, "acquired"), (0, None)])
async def test_try_takeover_expired_returns_decision_when_update_wins(rowcount: int, expected: str | None):
    record_id = uuid.uuid4()
    session = _FakeSession(execute_results=[_RowcountResult(rowcount)])

    decision = await IdempotencyService()._try_takeover_expired(
        session=session,
        record=_record(id=record_id),
        request_hash="next-hash",
        expires_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )

    assert (decision.action if decision else None) == expected
    assert (decision.record_id if decision else None) in {record_id, None}
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rowcount, expected", [(1, True), (0, False)])
async def test_reopen_failed_reports_whether_update_wins(rowcount: int, expected: bool):
    session = _FakeSession(execute_results=[_RowcountResult(rowcount)])

    reopened = await IdempotencyService()._reopen_failed(
        session=session,
        record_id=uuid.uuid4(),
        request_hash="next-hash",
        expires_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )

    assert reopened is expected
    assert session.commits == 1


@pytest.mark.asyncio
async def test_get_record_returns_scalar_one_or_none():
    record = _record()
    session = _FakeSession(execute_results=[_ScalarOneResult(record)])

    assert await IdempotencyService._get_record(session=session, scope="scope", key_hash="key-hash") is record
