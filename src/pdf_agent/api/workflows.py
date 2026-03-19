"""Workflow templates — built-in presets + user-defined CRUD."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pdf_agent.config import settings

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class WorkflowParam(BaseModel):
    name: str
    label: str
    type: str = "string"
    required: bool = True
    default: str | None = None


class WorkflowTemplate(BaseModel):
    id: str
    name: str
    description: str
    prompt_template: str
    params: list[WorkflowParam] = []
    builtin: bool = True
    created_at: str | None = None


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    prompt_template: str
    params: list[WorkflowParam] = []


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------

BUILTIN_TEMPLATES: list[WorkflowTemplate] = [
    WorkflowTemplate(
        id="scan-to-searchable",
        name="Scan → Searchable PDF",
        description="OCR a scanned PDF to make it searchable, then compress to reduce file size.",
        prompt_template="Please OCR this scanned PDF to make it searchable, then compress the result.",
    ),
    WorkflowTemplate(
        id="watermark-and-protect",
        name="Watermark & Encrypt",
        description="Add a text watermark to every page, then encrypt with a password.",
        prompt_template='Add a "{watermark_text}" watermark to all pages, then encrypt with password "{password}".',
        params=[
            WorkflowParam(name="watermark_text", label="Watermark text", default="CONFIDENTIAL"),
            WorkflowParam(name="password", label="Password"),
        ],
    ),
    WorkflowTemplate(
        id="split-and-number",
        name="Split & Add Page Numbers",
        description="Extract a page range and add page numbers to the result.",
        prompt_template="Extract pages {page_range} from the PDF, then add page numbers to the result.",
        params=[
            WorkflowParam(name="page_range", label="Page range", default="all"),
        ],
    ),
    WorkflowTemplate(
        id="images-to-pdf-compress",
        name="Images → PDF → Compress",
        description="Convert images to a single PDF, then compress it.",
        prompt_template="Convert all the uploaded images into a single PDF, then compress the result.",
    ),
    WorkflowTemplate(
        id="full-cleanup",
        name="Full Cleanup",
        description="Flatten form fields, compress, and add a watermark.",
        prompt_template='Flatten any form fields in this PDF, then compress it, and finally add a "{watermark_text}" watermark.',
        params=[
            WorkflowParam(name="watermark_text", label="Watermark text", default="PROCESSED"),
        ],
    ),
    WorkflowTemplate(
        id="pdf-to-word-compress",
        name="PDF → Word",
        description="Convert PDF to editable Word document.",
        prompt_template="Convert this PDF to a Word document.",
    ),
    WorkflowTemplate(
        id="compare-versions",
        name="Compare Two PDFs",
        description="Highlight differences between two PDF versions.",
        prompt_template="Compare these two PDF files and highlight the differences. Use highlight color {color}.",
        params=[
            WorkflowParam(name="color", label="Highlight color", default="red"),
        ],
    ),
]

_BUILTIN_BY_ID = {t.id: t for t in BUILTIN_TEMPLATES}


# ---------------------------------------------------------------------------
# User-defined workflow storage (filesystem JSON)
# ---------------------------------------------------------------------------

def _workflows_file() -> Path:
    path = settings.data_dir / "workflows.json"
    return path


def _load_user_workflows() -> dict[str, WorkflowTemplate]:
    p = _workflows_file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return {k: WorkflowTemplate(**v) for k, v in data.items()}
    except (json.JSONDecodeError, KeyError, ValueError):
        return {}


def _save_user_workflows(workflows: dict[str, WorkflowTemplate]) -> None:
    p = _workflows_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v.model_dump() for k, v in workflows.items()}, indent=2, ensure_ascii=False))


def _all_workflows() -> list[WorkflowTemplate]:
    user = _load_user_workflows()
    return BUILTIN_TEMPLATES + list(user.values())


def _get_workflow(workflow_id: str) -> WorkflowTemplate | None:
    if workflow_id in _BUILTIN_BY_ID:
        return _BUILTIN_BY_ID[workflow_id]
    return _load_user_workflows().get(workflow_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    workflow_id: str
    thread_id: str | None = None
    file_ids: list[str] = []
    params: dict[str, str] = {}


@router.get(
    "",
    summary="List all workflow templates",
    description="Returns built-in workflow presets and any user-defined custom workflows.",
)
async def list_workflows():
    return {"workflows": [t.model_dump() for t in _all_workflows()]}


@router.post(
    "",
    summary="Create a custom workflow",
    description="Save a reusable workflow template with a prompt and optional parameters.",
    status_code=201,
)
async def create_workflow(req: CreateWorkflowRequest):
    user_workflows = _load_user_workflows()
    wf_id = str(uuid.uuid4())[:8]
    wf = WorkflowTemplate(
        id=wf_id,
        name=req.name,
        description=req.description,
        prompt_template=req.prompt_template,
        params=req.params,
        builtin=False,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    user_workflows[wf_id] = wf
    _save_user_workflows(user_workflows)
    return wf.model_dump()


@router.get(
    "/{workflow_id}",
    summary="Get a workflow template",
)
async def get_workflow(workflow_id: str):
    tmpl = _get_workflow(workflow_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return tmpl.model_dump()


@router.put(
    "/{workflow_id}",
    summary="Update a custom workflow",
    description="Only user-defined workflows can be updated.",
)
async def update_workflow(workflow_id: str, req: CreateWorkflowRequest):
    if workflow_id in _BUILTIN_BY_ID:
        raise HTTPException(status_code=403, detail="Cannot modify built-in workflows")
    user_workflows = _load_user_workflows()
    if workflow_id not in user_workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    existing = user_workflows[workflow_id]
    updated = WorkflowTemplate(
        id=workflow_id,
        name=req.name,
        description=req.description,
        prompt_template=req.prompt_template,
        params=req.params,
        builtin=False,
        created_at=existing.created_at,
    )
    user_workflows[workflow_id] = updated
    _save_user_workflows(user_workflows)
    return updated.model_dump()


@router.delete(
    "/{workflow_id}",
    summary="Delete a custom workflow",
)
async def delete_workflow(workflow_id: str):
    if workflow_id in _BUILTIN_BY_ID:
        raise HTTPException(status_code=403, detail="Cannot delete built-in workflows")
    user_workflows = _load_user_workflows()
    if workflow_id not in user_workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    del user_workflows[workflow_id]
    _save_user_workflows(user_workflows)
    return {"deleted": True, "id": workflow_id}


@router.post(
    "/{workflow_id}/render",
    summary="Render workflow prompt",
    description="Substitute parameters into the workflow prompt template. Returns the prompt to send to /api/agent/chat.",
)
async def render_workflow(workflow_id: str, req: ExecuteRequest):
    tmpl = _get_workflow(workflow_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        prompt = tmpl.prompt_template.format(**req.params)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Missing parameter: {exc}")
    return {
        "workflow_id": workflow_id,
        "prompt": prompt,
        "file_ids": req.file_ids,
        "thread_id": req.thread_id,
    }
