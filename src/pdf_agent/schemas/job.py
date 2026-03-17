"""Job-related schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from pdf_agent.schemas.plan import PlanSchema


class JobCreateRequest(BaseModel):
    file_ids: list[uuid.UUID]
    plan: PlanSchema
    mode: str = "FORM"
    instruction: str | None = None


class JobStepResponse(BaseModel):
    id: uuid.UUID
    idx: int
    tool_name: str
    params_json: dict
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    log_text: str | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
    mode: str
    instruction: str | None = None
    plan_json: dict
    progress: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    result_path: str | None = None
    result_type: str | None = None
    steps: list[JobStepResponse] = []


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
