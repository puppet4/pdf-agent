"""Legacy compatibility endpoints with phased deprecation strategy."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pdf_agent.config import settings
from pdf_agent.tools.registry import registry

router = APIRouter(tags=["legacy-compat"])

_LEGACY_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LegacyExecutionCreateRequest(BaseModel):
    conversation_id: str | None = None
    title: str | None = None


def _phase() -> str:
    return settings.legacy_api_phase


def _legacy_headers(replacement: str) -> dict[str, str]:
    phase = _phase()
    warning_level = (
        "Endpoint is deprecated and will be removed"
        if phase == "deprecation"
        else "Endpoint is near sunset and should be migrated immediately"
    )
    headers = {
        "Deprecation": "true",
        "Sunset": settings.legacy_api_sunset_date,
        "Link": f'<{settings.legacy_api_migration_url}>; rel="deprecation"',
        "X-Replacement-Endpoint": replacement,
        "X-Legacy-Phase": phase,
    }
    if phase in {"deprecation", "warning"}:
        headers["Warning"] = f'299 - "{warning_level}"'
    return headers


def _legacy_sunset_payload(*, endpoint: str, replacement: str) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        headers=_legacy_headers(replacement),
        content={
            "detail": "Legacy API endpoint has reached sunset",
            "legacy_endpoint": endpoint,
            "replacement": replacement,
            "migration_url": settings.legacy_api_migration_url,
            "sunset_date": settings.legacy_api_sunset_date,
            "phase": _phase(),
        },
    )


def _legacy_notice_payload(
    *,
    endpoint: str,
    replacement: str,
    payload: dict,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=_legacy_headers(replacement),
        content={
            "deprecated": True,
            "phase": _phase(),
            "legacy_endpoint": endpoint,
            "replacement": replacement,
            "migration_url": settings.legacy_api_migration_url,
            "sunset_date": settings.legacy_api_sunset_date,
            **payload,
        },
    )


def _serialize_legacy_execution(conversation_dir) -> dict:
    stat = conversation_dir.stat()
    created_at = (
        conversation_dir.stat().st_ctime
        if not hasattr(stat, "st_birthtime")
        else getattr(stat, "st_birthtime")
    )
    return {
        "id": conversation_dir.name,
        "conversation_id": conversation_dir.name,
        "status": "UNKNOWN",
        "created_at": created_at,
        "updated_at": stat.st_mtime,
    }


def _list_legacy_execution_items(page: int, limit: int) -> tuple[list[dict], int, int, int]:
    conversations_dir = settings.conversations_dir
    if not conversations_dir.exists():
        return ([], 0, page, limit)

    entries = []
    for conversation_dir in conversations_dir.iterdir():
        if not conversation_dir.is_dir() or conversation_dir.name.startswith("direct_"):
            continue
        try:
            entries.append(_serialize_legacy_execution(conversation_dir))
        except OSError:
            continue

    entries.sort(key=lambda item: item["updated_at"], reverse=True)
    total = len(entries)
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    start = (page - 1) * limit
    end = start + limit
    return (entries[start:end], total, page, limit)


def _validate_legacy_conversation_id(candidate: str) -> str:
    if not _LEGACY_EXECUTION_ID_RE.fullmatch(candidate):
        raise ValueError("Invalid conversation id")
    return candidate


@router.get("/api/tools")
async def legacy_tools():
    if _phase() == "sunset":
        return _legacy_sunset_payload(
            endpoint="/api/tools",
            replacement="/api/conversations/{conversation_id}/messages",
        )
    manifests = [
        {
            "name": item["name"],
            "label": item["label"],
            "category": item.get("category", "tool"),
        }
        for item in registry.list_manifests()
    ]
    return _legacy_notice_payload(
        endpoint="/api/tools",
        replacement="/api/conversations/{conversation_id}/messages",
        payload={"tools": manifests},
    )


@router.get("/api/executions")
async def legacy_executions_list(
    page: int = Query(1),
    limit: int = Query(20),
):
    if _phase() == "sunset":
        return _legacy_sunset_payload(
            endpoint="/api/executions",
            replacement="/api/conversations?page=1&limit=20",
        )
    items, total, page, limit = _list_legacy_execution_items(page, limit)
    return _legacy_notice_payload(
        endpoint="/api/executions",
        replacement="/api/conversations?page=1&limit=20",
        payload={
            "executions": items,
            "total": total,
            "page": page,
            "limit": limit,
        },
    )


@router.post("/api/executions")
async def legacy_executions_create(req: LegacyExecutionCreateRequest):
    if _phase() == "sunset":
        return _legacy_sunset_payload(
            endpoint="/api/executions",
            replacement="/api/conversations/{conversation_id}/messages",
        )

    conversation_id = req.conversation_id or str(uuid.uuid4())
    try:
        conversation_id = _validate_legacy_conversation_id(conversation_id)
    except ValueError as exc:
        return _legacy_notice_payload(
            endpoint="/api/executions",
            replacement="/api/conversations/{conversation_id}/messages",
            payload={"detail": str(exc)},
            status_code=422,
        )

    conversation_dir = settings.conversations_dir / conversation_id
    conversation_dir.mkdir(parents=True, exist_ok=True)
    title = (req.title or "Legacy Execution").strip()[:48] or "Legacy Execution"
    title_path = conversation_dir / ".title.txt"
    title_path.write_text(title, encoding="utf-8")

    return _legacy_notice_payload(
        endpoint="/api/executions",
        replacement="/api/conversations/{conversation_id}/messages",
        payload={
            "execution": {
                "id": conversation_id,
                "conversation_id": conversation_id,
                "status": "CREATED",
                "created_at": conversation_dir.stat().st_mtime,
            },
            "next_step": f"POST /api/conversations/{conversation_id}/messages",
        },
        status_code=202,
    )


@router.get("/api/workflows")
async def legacy_workflows():
    if _phase() == "sunset":
        return _legacy_sunset_payload(
            endpoint="/api/workflows",
            replacement="/api/conversations/{conversation_id}/messages",
        )
    return _legacy_notice_payload(
        endpoint="/api/workflows",
        replacement="/api/conversations/{conversation_id}/messages",
        payload={
            "workflows": [],
            "detail": "Workflow APIs are replaced by conversation-driven orchestration",
        },
    )
