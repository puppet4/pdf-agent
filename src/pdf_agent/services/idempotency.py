"""Idempotency coordination for write APIs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Literal
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import IdempotencyRecord

logger = logging.getLogger(__name__)

STATUS_PROCESSING = "PROCESSING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"


@dataclass(frozen=True)
class IdempotencyDecision:
    action: Literal["acquired", "replay", "in_progress", "conflict"]
    record_id: uuid.UUID | None = None
    response_code: int | None = None
    response_payload: dict[str, Any] | None = None
    message: str | None = None


def normalize_idempotency_key(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    if len(normalized) > settings.idempotency_max_key_length:
        raise ValueError(
            f"Idempotency-Key is too long (max {settings.idempotency_max_key_length} characters)"
        )
    return normalized


def build_request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_response_payload(raw_payload: str | None) -> dict[str, Any] | None:
    if not raw_payload:
        return None
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class IdempotencyService:
    async def acquire(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyDecision:
        now = _utcnow()
        expires_at = now + timedelta(hours=settings.idempotency_ttl_hours)
        key_hash = _hash_key(key)

        async with async_session_factory() as session:
            await session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < now))
            candidate = IdempotencyRecord(
                scope=scope,
                key_hash=key_hash,
                request_hash=request_hash,
                status=STATUS_PROCESSING,
                response_code=None,
                response_body=None,
                error_message=None,
                expires_at=expires_at,
            )
            session.add(candidate)
            try:
                await session.commit()
                await session.refresh(candidate)
                return IdempotencyDecision(action="acquired", record_id=candidate.id)
            except IntegrityError:
                await session.rollback()

            existing = await self._get_record(session, scope=scope, key_hash=key_hash)
            if existing is None:
                logger.warning("Idempotency conflict lookup missed scope=%s", scope)
                return IdempotencyDecision(action="conflict", message="Idempotency key conflict")

            if existing.expires_at < now:
                acquired = await self._try_takeover_expired(
                    session=session,
                    record=existing,
                    request_hash=request_hash,
                    expires_at=expires_at,
                )
                if acquired is not None:
                    return acquired

            if existing.request_hash != request_hash:
                return IdempotencyDecision(
                    action="conflict",
                    message="Idempotency key has already been used for a different request payload",
                )

            if existing.status == STATUS_SUCCEEDED:
                return IdempotencyDecision(
                    action="replay",
                    record_id=existing.id,
                    response_code=existing.response_code,
                    response_payload=_parse_response_payload(existing.response_body),
                )

            if existing.status == STATUS_FAILED:
                refreshed = await self._reopen_failed(
                    session=session,
                    record_id=existing.id,
                    request_hash=request_hash,
                    expires_at=expires_at,
                )
                if refreshed:
                    return IdempotencyDecision(action="acquired", record_id=existing.id)
                return IdempotencyDecision(
                    action="conflict",
                    message="Previous failed request with same idempotency key is still being reconciled",
                )

            stale_timeout = timedelta(seconds=settings.idempotency_processing_timeout_sec)
            if existing.updated_at and (now - existing.updated_at) > stale_timeout:
                recovered = await self._reopen_failed(
                    session=session,
                    record_id=existing.id,
                    request_hash=request_hash,
                    expires_at=expires_at,
                )
                if recovered:
                    return IdempotencyDecision(action="acquired", record_id=existing.id)

            return IdempotencyDecision(
                action="in_progress",
                record_id=existing.id,
                response_code=existing.response_code,
                response_payload=_parse_response_payload(existing.response_body),
                message="A request with the same idempotency key is already in progress",
            )

    async def mark_processing(
        self,
        *,
        record_id: uuid.UUID,
        response_payload: dict[str, Any] | None,
    ) -> None:
        await self._mark(
            record_id=record_id,
            status=STATUS_PROCESSING,
            response_code=102,
            response_payload=response_payload,
            error_message=None,
        )

    async def mark_succeeded(
        self,
        *,
        record_id: uuid.UUID,
        response_code: int,
        response_payload: dict[str, Any],
    ) -> None:
        await self._mark(
            record_id=record_id,
            status=STATUS_SUCCEEDED,
            response_code=response_code,
            response_payload=response_payload,
            error_message=None,
        )

    async def mark_failed(
        self,
        *,
        record_id: uuid.UUID,
        response_code: int,
        error_message: str,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        await self._mark(
            record_id=record_id,
            status=STATUS_FAILED,
            response_code=response_code,
            response_payload=response_payload,
            error_message=error_message[:512],
        )

    async def _mark(
        self,
        *,
        record_id: uuid.UUID,
        status: str,
        response_code: int,
        response_payload: dict[str, Any] | None,
        error_message: str | None,
    ) -> None:
        payload = json.dumps(response_payload, ensure_ascii=False, default=str) if response_payload else None
        async with async_session_factory() as session:
            await session.execute(
                update(IdempotencyRecord)
                .where(IdempotencyRecord.id == record_id)
                .values(
                    status=status,
                    response_code=response_code,
                    response_body=payload,
                    error_message=error_message,
                    updated_at=_utcnow(),
                )
            )
            await session.commit()

    async def _try_takeover_expired(
        self,
        *,
        session,
        record: IdempotencyRecord,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyDecision | None:
        result = await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == record.id, IdempotencyRecord.expires_at < _utcnow())
            .values(
                request_hash=request_hash,
                status=STATUS_PROCESSING,
                response_code=None,
                response_body=None,
                error_message=None,
                expires_at=expires_at,
                updated_at=_utcnow(),
            )
        )
        await session.commit()
        if result.rowcount:
            return IdempotencyDecision(action="acquired", record_id=record.id)
        return None

    async def _reopen_failed(
        self,
        *,
        session,
        record_id: uuid.UUID,
        request_hash: str,
        expires_at: datetime,
    ) -> bool:
        result = await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == record_id)
            .values(
                request_hash=request_hash,
                status=STATUS_PROCESSING,
                response_code=None,
                response_body=None,
                error_message=None,
                expires_at=expires_at,
                updated_at=_utcnow(),
            )
        )
        await session.commit()
        return bool(result.rowcount)

    @staticmethod
    async def _get_record(*, session, scope: str, key_hash: str) -> IdempotencyRecord | None:
        result = await session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key_hash == key_hash,
            )
        )
        return result.scalar_one_or_none()


idempotency_service = IdempotencyService()
