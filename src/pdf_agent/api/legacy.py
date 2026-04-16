"""Legacy compatibility endpoints with explicit deprecation guidance."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pdf_agent.config import settings
from pdf_agent.tools.registry import registry

router = APIRouter(tags=["legacy-compat"])


def _legacy_headers(replacement: str) -> dict[str, str]:
    return {
        "Deprecation": "true",
        "Sunset": settings.legacy_api_sunset_date,
        "Link": f'<{settings.legacy_api_migration_url}>; rel="deprecation"',
        "X-Replacement-Endpoint": replacement,
    }


def _legacy_payload(*, endpoint: str, replacement: str, status: int = 410) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers=_legacy_headers(replacement),
        content={
            "detail": "Legacy API endpoint is deprecated",
            "legacy_endpoint": endpoint,
            "replacement": replacement,
            "migration_url": settings.legacy_api_migration_url,
            "sunset_date": settings.legacy_api_sunset_date,
        },
    )


@router.get("/api/tools")
async def legacy_tools():
    manifests = [
        {
            "name": item["name"],
            "label": item["label"],
            "category": item.get("category", "tool"),
        }
        for item in registry.list_manifests()
    ]
    return JSONResponse(
        status_code=200,
        headers=_legacy_headers("/api/conversations/{conversation_id}/messages"),
        content={
            "deprecated": True,
            "migration_url": settings.legacy_api_migration_url,
            "sunset_date": settings.legacy_api_sunset_date,
            "tools": manifests,
        },
    )


@router.get("/api/executions")
async def legacy_executions_list():
    return _legacy_payload(
        endpoint="/api/executions",
        replacement="/api/conversations?page=1&limit=20",
    )


@router.post("/api/executions")
async def legacy_executions_create():
    return _legacy_payload(
        endpoint="/api/executions",
        replacement="/api/conversations/{conversation_id}/messages",
    )


@router.get("/api/workflows")
async def legacy_workflows():
    return _legacy_payload(
        endpoint="/api/workflows",
        replacement="/api/conversations/{conversation_id}/messages",
    )
