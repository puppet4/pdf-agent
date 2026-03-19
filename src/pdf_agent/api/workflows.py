"""Workflow templates — predefined multi-tool chains."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# Template definitions
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


TEMPLATES: list[WorkflowTemplate] = [
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
        prompt_template='Extract pages {page_range} from the PDF, then add page numbers to the result.',
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
]

_TEMPLATES_BY_ID = {t.id: t for t in TEMPLATES}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    workflow_id: str
    thread_id: str | None = None
    file_ids: list[str] = []
    params: dict[str, str] = {}


@router.get("")
async def list_workflows():
    """List all available workflow templates."""
    return {"workflows": [t.model_dump() for t in TEMPLATES]}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get a single workflow template."""
    tmpl = _TEMPLATES_BY_ID.get(workflow_id)
    if not tmpl:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")
    return tmpl.model_dump()


@router.post("/{workflow_id}/render")
async def render_workflow(workflow_id: str, req: ExecuteRequest):
    """Render the workflow prompt with provided parameters.

    Returns the prompt string to send to /api/agent/chat along with file_ids.
    The frontend can then POST it to the chat endpoint.
    """
    tmpl = _TEMPLATES_BY_ID.get(workflow_id)
    if not tmpl:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        prompt = tmpl.prompt_template.format(**req.params)
    except KeyError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=f"Missing parameter: {exc}")

    return {
        "workflow_id": workflow_id,
        "prompt": prompt,
        "file_ids": req.file_ids,
        "thread_id": req.thread_id,
    }
