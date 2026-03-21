"""Tools API - direct toolbox entrypoints over the execution backbone."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pdf_agent.api.executions import create_execution_record, wait_for_execution_terminal
from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.tools.registry import registry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get(
    "",
    summary="List all tools",
    description="Returns manifest list for all 34 registered PDF tools.",
)
async def list_tools() -> dict:
    """Return manifest list for all registered tools."""
    return {"tools": registry.list_manifests()}


@router.get(
    "/{tool_name}",
    summary="Get tool manifest",
    description="Return the manifest for a specific tool including params, input/output specs.",
)
async def get_tool(tool_name: str) -> dict:
    """Return manifest for a specific tool."""
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return tool.manifest().model_dump()


class ToolRunRequest(BaseModel):
    """
    Direct tool invocation request.

    - **file_ids**: IDs of previously-uploaded files (from POST /api/files)
    - **params**: Tool-specific parameters (see GET /api/tools/{name} for the param spec)

    Example for `rotate`:
    ```json
    {"file_ids": ["<uuid>"], "params": {"angle": "90", "page_range": "all"}}
    ```
    """
    file_ids: list[str]
    params: dict[str, Any] = {}


class ToolRunResponse(BaseModel):
    tool: str
    status: str
    log: str
    meta: dict[str, Any]
    output_files: list[dict]  # [{filename, download_url, size_bytes}]


@router.post(
    "/{tool_name}/run",
    summary="Run a tool directly",
    description=(
        "Execute a PDF tool synchronously without going through the Agent. "
        "Upload files first via POST /api/files, then pass their IDs here. "
        "Returns tool output including download URLs for result files."
    ),
    response_model=ToolRunResponse,
)
async def run_tool(tool_name: str, req: ToolRunRequest):
    """Run a tool through the shared execution backbone and wait for completion."""
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    manifest = tool.manifest()

    # Resolve input files from DB
    from sqlalchemy import select
    from pdf_agent.db import async_session_factory
    from pdf_agent.db.models import FileRecord

    input_paths: list[Path] = []
    async with async_session_factory() as session:
        for fid in req.file_ids:
            try:
                uid = uuid.UUID(fid)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid file_id: {fid}")
            result = await session.execute(select(FileRecord).where(FileRecord.id == uid))
            record = result.scalar_one_or_none()
            if not record:
                raise HTTPException(status_code=404, detail=f"File {fid} not found")
            path = Path(record.storage_path)
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"File {fid} not on disk")
            input_paths.append(path)

    # Validate input count
    if len(input_paths) < manifest.inputs.min:
        raise HTTPException(
            status_code=422,
            detail=f"{tool_name} requires at least {manifest.inputs.min} file(s), got {len(input_paths)}",
        )
    if len(input_paths) > manifest.inputs.max:
        input_paths = input_paths[: manifest.inputs.max]

    try:
        tool.validate(req.params)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid tool parameters")

    created = await create_execution_record(
        mode="FORM",
        instruction=f"Direct tool run: {tool_name}",
        steps=[{"tool": tool_name, "params": req.params}],
        file_ids=req.file_ids,
    )
    execution = await wait_for_execution_terminal(created["id"], timeout=settings.external_cmd_timeout_sec + 5)
    if execution["status"] == "FAILED":
        raise HTTPException(status_code=400, detail=execution["error_message"] or "Tool execution failed")
    if execution["status"] == "CANCELED":
        raise HTTPException(status_code=409, detail="Execution canceled")

    output_files = [
        {
            "filename": item["filename"],
            "download_url": item["download_url"],
            "size_bytes": item.get("size_bytes", 0),
        }
        for item in execution.get("outputs", [])
    ]
    last_step = (execution.get("logs") or [{}])[-1]
    response = ToolRunResponse(
        tool=tool_name,
        status="success",
        log=last_step.get("log_text", ""),
        meta={"execution_id": execution["id"], "result_type": execution.get("result_type")},
        output_files=output_files,
    )
    metrics.record_tool(tool_name, 0.0)

    return response


# ---------------------------------------------------------------------------
# Drag-and-drop page reorder
# ---------------------------------------------------------------------------

class ReorderRequest(BaseModel):
    """
    Reorder pages via drag-and-drop.

    - **file_id**: ID of uploaded PDF
    - **order**: New page order as list of 1-based page numbers, e.g. [3,1,2]
    """
    file_id: str
    order: list[int]


@router.post(
    "/reorder",
    summary="Reorder PDF pages (drag-and-drop)",
    description="Reorder pages of an uploaded PDF by providing the desired page order as a list.",
)
async def reorder_pages(req: ReorderRequest):
    """Reorder pages through the shared execution backbone and wait for completion."""
    if not req.order:
        raise HTTPException(status_code=422, detail="order must not be empty")
    created = await create_execution_record(
        mode="FORM",
        instruction="Direct reorder run",
        steps=[{"tool": "reorder", "params": {"order": ",".join(str(p) for p in req.order)}}],
        file_ids=[req.file_id],
    )
    execution = await wait_for_execution_terminal(created["id"], timeout=settings.external_cmd_timeout_sec + 5)
    if execution["status"] != "SUCCESS":
        raise HTTPException(status_code=400, detail=execution.get("error_message") or "Tool execution failed")

    output_files = [
        {
            "filename": item["filename"],
            "download_url": item["download_url"],
            "size_bytes": item.get("size_bytes", 0),
        }
        for item in execution.get("outputs", [])
    ]
    last_step = (execution.get("logs") or [{}])[-1]
    return {"status": "success", "log": last_step.get("log_text", ""), "output_files": output_files}
