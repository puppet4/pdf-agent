"""Tools API - list tools, get manifests, and direct tool invocation."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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
    - **webhook_url**: Optional URL to POST results when tool completes

    Example for `rotate`:
    ```json
    {"file_ids": ["<uuid>"], "params": {"angle": "90", "page_range": "all"}}
    ```
    """
    file_ids: list[str]
    params: dict[str, Any] = {}
    webhook_url: str | None = None


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
    """Direct tool invocation — bypasses the LLM Agent."""
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

    # Create workdir
    run_id = str(uuid.uuid4())[:8]
    workdir = settings.threads_dir / f"direct_{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)

    # Validate params
    try:
        validated_params = tool.validate(req.params)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Run tool (offload to thread if async_hint)
    try:
        if manifest.async_hint:
            tool_result = await asyncio.wait_for(
                asyncio.to_thread(tool.run, inputs=input_paths, params=validated_params, workdir=workdir),
                timeout=settings.external_cmd_timeout_sec,
            )
        else:
            tool_result = tool.run(inputs=input_paths, params=validated_params, workdir=workdir)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"{tool_name} timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {exc}")

    # Build download URLs
    output_files = []
    for f in tool_result.output_files:
        rel = f.relative_to(settings.threads_dir)
        parts = rel.parts  # direct_{run_id} / filename  or  direct_{run_id} / step_X / filename
        if len(parts) == 2:
            url = f"/api/tools/results/{parts[0]}/{parts[1]}"
        else:
            url = f"/api/tools/results/{'/'.join(parts)}"
        output_files.append({
            "filename": f.name,
            "download_url": url,
            "size_bytes": f.stat().st_size,
        })

    response = ToolRunResponse(
        tool=tool_name,
        status="success",
        log=tool_result.log,
        meta=tool_result.meta,
        output_files=output_files,
    )

    # Fire webhook if configured
    if req.webhook_url:
        from pdf_agent.webhook import schedule_webhook
        schedule_webhook(req.webhook_url, {
            "event": "tool_complete",
            "tool": tool_name,
            "status": "success",
            "output_files": output_files,
            "log": tool_result.log,
        })

    return response


@router.get(
    "/results/{run_id}/{filename}",
    summary="Download direct tool result",
    include_in_schema=False,
)
async def download_tool_result(run_id: str, filename: str):
    """Download a file produced by POST /api/tools/{name}/run."""
    # run_id is always prefixed with 'direct_'
    if not run_id.startswith("direct_"):
        raise HTTPException(status_code=400, detail="Invalid result path")
    path = settings.threads_dir / run_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Result file not found")
    return FileResponse(path, filename=filename)
