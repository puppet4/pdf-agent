"""Workflow templates materialized into execution plans executed through the LangChain-aligned runtime."""
from __future__ import annotations

import json
import logging
import re
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pdf_agent.api.executions import create_execution_record
from pdf_agent.config import settings

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_WORKFLOW_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WorkflowStoreError(RuntimeError):
    pass


class WorkflowParam(BaseModel):
    name: str
    label: str
    type: str = "string"
    required: bool = True
    default: str | None = None


class WorkflowStepTemplate(BaseModel):
    tool: str
    input_mode: str = "prev"  # file | prev
    params_template: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplate(BaseModel):
    id: str
    name: str
    description: str
    steps_template: list[WorkflowStepTemplate] = Field(default_factory=list)
    params: list[WorkflowParam] = Field(default_factory=list)
    builtin: bool = True
    created_at: str | None = None


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    steps_template: list[WorkflowStepTemplate] = Field(default_factory=list)
    params: list[WorkflowParam] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    workflow_id: str
    file_ids: list[str] = Field(default_factory=list)
    params: dict[str, str] = Field(default_factory=dict)
    mode: str = "FORM"


BUILTIN_TEMPLATES: list[WorkflowTemplate] = [
    WorkflowTemplate(
        id="scan-to-searchable",
        name="Scan -> Searchable PDF",
        description="OCR a scanned PDF, then compress it.",
        steps_template=[
            WorkflowStepTemplate(tool="ocr", input_mode="file", params_template={"language": "{language}", "output_mode": "pdf"}),
            WorkflowStepTemplate(tool="compress", input_mode="prev", params_template={"level": "{level}"}),
        ],
        params=[
            WorkflowParam(name="language", label="OCR language", default="eng"),
            WorkflowParam(name="level", label="Compression level", default="medium"),
        ],
    ),
    WorkflowTemplate(
        id="watermark-and-protect",
        name="Watermark And Protect",
        description="Add a watermark, then encrypt the result.",
        steps_template=[
            WorkflowStepTemplate(tool="watermark_text", input_mode="file", params_template={"text": "{watermark_text}"}),
            WorkflowStepTemplate(
                tool="encrypt",
                input_mode="prev",
                params_template={
                    "owner_password": "{owner_password}",
                    "user_password": "{user_password}",
                    "allow_print": "true",
                },
            ),
        ],
        params=[
            WorkflowParam(name="watermark_text", label="Watermark text", default="CONFIDENTIAL"),
            WorkflowParam(name="owner_password", label="Owner password", default="owner-pass"),
            WorkflowParam(name="user_password", label="User password", required=False, default=""),
        ],
    ),
    WorkflowTemplate(
        id="split-and-number",
        name="Split And Add Page Numbers",
        description="Extract selected pages, then add page numbers.",
        steps_template=[
            WorkflowStepTemplate(tool="extract", input_mode="file", params_template={"page_range": "{page_range}"}),
            WorkflowStepTemplate(tool="add_page_numbers", input_mode="prev", params_template={}),
        ],
        params=[WorkflowParam(name="page_range", label="Page range", default="1-3")],
    ),
    WorkflowTemplate(
        id="images-to-pdf-compress",
        name="Images -> PDF -> Compress",
        description="Combine uploaded images into a PDF and compress it.",
        steps_template=[
            WorkflowStepTemplate(tool="images_to_pdf", input_mode="file", params_template={}),
            WorkflowStepTemplate(tool="compress", input_mode="prev", params_template={"level": "{level}"}),
        ],
        params=[WorkflowParam(name="level", label="Compression level", default="medium")],
    ),
    WorkflowTemplate(
        id="full-cleanup",
        name="Full Cleanup",
        description="Flatten, compress, then watermark a PDF.",
        steps_template=[
            WorkflowStepTemplate(tool="flatten", input_mode="file", params_template={}),
            WorkflowStepTemplate(tool="compress", input_mode="prev", params_template={"level": "{level}"}),
            WorkflowStepTemplate(tool="watermark_text", input_mode="prev", params_template={"text": "{watermark_text}"}),
        ],
        params=[
            WorkflowParam(name="level", label="Compression level", default="medium"),
            WorkflowParam(name="watermark_text", label="Watermark text", default="PROCESSED"),
        ],
    ),
    WorkflowTemplate(
        id="compare-versions",
        name="Compare Two PDFs",
        description="Compare two uploaded PDFs and produce visual plus text diff outputs.",
        steps_template=[WorkflowStepTemplate(tool="compare", input_mode="file", params_template={"highlight_color": "{color}"})],
        params=[WorkflowParam(name="color", label="Highlight color", default="red")],
    ),
]

_BUILTIN_BY_ID = {template.id: template for template in BUILTIN_TEMPLATES}


def _workflows_file() -> Path:
    return settings.data_dir / "workflows.json"


@contextmanager
def _workflow_lock():
    lock_path = settings.data_dir / ".workflows.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _workflow_http_500(exc: WorkflowStoreError) -> HTTPException:
    return HTTPException(status_code=500, detail=str(exc))


def _load_user_workflows() -> dict[str, WorkflowTemplate]:
    with _workflow_lock():
        p = _workflows_file()
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise WorkflowStoreError("Workflow store is corrupted") from exc
        try:
            return {workflow_id: _normalize_workflow_payload(payload) for workflow_id, payload in data.items()}
        except Exception as exc:
            raise WorkflowStoreError("Workflow store is corrupted") from exc


def _save_user_workflows(workflows: dict[str, WorkflowTemplate]) -> None:
    with _workflow_lock():
        p = _workflows_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps({workflow_id: workflow.model_dump() for workflow_id, workflow in workflows.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(p)


def _normalize_workflow_payload(payload: dict[str, Any]) -> WorkflowTemplate:
    if "steps_template" not in payload and payload.get("prompt_template"):
        payload = {
            **payload,
            "steps_template": [],
        }
    return WorkflowTemplate(**payload)


def _validate_params(params: list[WorkflowParam]) -> None:
    param_names = [param.name for param in params]
    if len(param_names) != len(set(param_names)):
        raise HTTPException(status_code=422, detail="Workflow params must have unique names")
    if any(not _WORKFLOW_PARAM_RE.fullmatch(name) for name in param_names):
        raise HTTPException(status_code=422, detail="Workflow param names are invalid")


def _build_workflow_template(*, workflow_id: str, req: CreateWorkflowRequest, builtin: bool, created_at: str | None) -> WorkflowTemplate:
    _validate_params(req.params)
    if not req.steps_template:
        raise HTTPException(status_code=422, detail="steps_template must not be empty")
    return WorkflowTemplate(
        id=workflow_id,
        name=req.name,
        description=req.description,
        steps_template=req.steps_template,
        params=req.params,
        builtin=builtin,
        created_at=created_at,
    )


def _all_workflows() -> list[WorkflowTemplate]:
    return BUILTIN_TEMPLATES + list(_load_user_workflows().values())


def _get_workflow(workflow_id: str) -> WorkflowTemplate | None:
    if workflow_id in _BUILTIN_BY_ID:
        return _BUILTIN_BY_ID[workflow_id]
    return _load_user_workflows().get(workflow_id)


def _render_template_value(value: Any, params: dict[str, str]) -> Any:
    if isinstance(value, str):
        try:
            return string.Formatter().vformat(value, (), params)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"Missing parameter: {exc.args[0]}") from exc
    if isinstance(value, dict):
        return {key: _render_template_value(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template_value(item, params) for item in value]
    return value


def _materialize_workflow_plan(workflow: WorkflowTemplate, file_ids: list[str], params: dict[str, str]) -> dict[str, Any]:
    if not file_ids:
        raise HTTPException(status_code=422, detail="file_ids must not be empty")
    default_params = {param.name: param.default or "" for param in workflow.params}
    merged_params = {**default_params, **params}
    steps: list[dict[str, Any]] = []
    for index, step_template in enumerate(workflow.steps_template):
        inputs = [{"type": "file", "file_id": file_id} for file_id in file_ids] if step_template.input_mode == "file" and index == 0 else [{"type": step_template.input_mode}]
        if step_template.input_mode == "file" and index > 0:
            inputs = [{"type": "file", "file_id": file_id} for file_id in file_ids]
        steps.append(
            {
                "tool": step_template.tool,
                "inputs": inputs,
                "params": _render_template_value(step_template.params_template, merged_params),
            }
        )
    return {"version": "1.0", "steps": steps, "output": {}}


@router.get("")
async def list_workflows():
    try:
        return {"workflows": [workflow.model_dump() for workflow in _all_workflows()]}
    except WorkflowStoreError as exc:
        raise _workflow_http_500(exc)


@router.post("", status_code=201)
async def create_workflow(req: CreateWorkflowRequest):
    try:
        workflows = _load_user_workflows()
        workflow_id = uuid.uuid4().hex
        workflow = _build_workflow_template(
            workflow_id=workflow_id,
            req=req,
            builtin=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        workflows[workflow_id] = workflow
        _save_user_workflows(workflows)
        return workflow.model_dump()
    except WorkflowStoreError as exc:
        raise _workflow_http_500(exc)


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str):
    try:
        workflow = _get_workflow(workflow_id)
    except WorkflowStoreError as exc:
        raise _workflow_http_500(exc)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow.model_dump()


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, req: CreateWorkflowRequest):
    if workflow_id in _BUILTIN_BY_ID:
        raise HTTPException(status_code=403, detail="Cannot modify built-in workflows")
    try:
        workflows = _load_user_workflows()
        if workflow_id not in workflows:
            raise HTTPException(status_code=404, detail="Workflow not found")
        existing = workflows[workflow_id]
        updated = _build_workflow_template(
            workflow_id=workflow_id,
            req=req,
            builtin=False,
            created_at=existing.created_at,
        )
        workflows[workflow_id] = updated
        _save_user_workflows(workflows)
        return updated.model_dump()
    except WorkflowStoreError as exc:
        raise _workflow_http_500(exc)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    if workflow_id in _BUILTIN_BY_ID:
        raise HTTPException(status_code=403, detail="Cannot delete built-in workflows")
    try:
        workflows = _load_user_workflows()
        if workflow_id not in workflows:
            raise HTTPException(status_code=404, detail="Workflow not found")
        del workflows[workflow_id]
        _save_user_workflows(workflows)
        return {"deleted": True, "id": workflow_id}
    except WorkflowStoreError as exc:
        raise _workflow_http_500(exc)


@router.post("/{workflow_id}/plan")
async def preview_workflow_plan(workflow_id: str, req: ExecuteRequest):
    if req.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="workflow_id in path and body must match")
    workflow = _get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    plan = _materialize_workflow_plan(workflow, req.file_ids, req.params)
    return {"workflow_id": workflow_id, "plan": plan, "mode": req.mode}


@router.post("/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, req: ExecuteRequest):
    preview = await preview_workflow_plan(workflow_id, req)
    # Workflow templates stay declarative; actual step execution goes through the shared execution/tool adapter path.
    return await create_execution_record(
        mode=req.mode,
        instruction=f"Workflow: {workflow_id}",
        steps=preview["plan"]["steps"],
        file_ids=req.file_ids,
        output=preview["plan"].get("output", {}),
    )
