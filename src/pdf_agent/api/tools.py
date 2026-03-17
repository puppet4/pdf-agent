"""Tools API - list available tools and their manifests."""
from __future__ import annotations

from fastapi import APIRouter

from pdf_agent.tools.registry import registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools() -> list[dict]:
    """Return manifest list for all registered tools."""
    return registry.list_manifests()


@router.get("/{tool_name}")
async def get_tool(tool_name: str) -> dict:
    """Return manifest for a specific tool."""
    tool = registry.get(tool_name)
    if not tool:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return tool.manifest().model_dump()
