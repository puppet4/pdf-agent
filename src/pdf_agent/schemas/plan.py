"""Plan schema for job execution."""
from __future__ import annotations

from pydantic import BaseModel


class StepInput(BaseModel):
    type: str = "file"  # file | prev
    file_id: str | None = None


class PlanStep(BaseModel):
    tool: str
    inputs: list[StepInput] = []
    params: dict = {}


class PlanOutput(BaseModel):
    format: str = "pdf"


class PlanSchema(BaseModel):
    version: str = "1.0"
    steps: list[PlanStep]
    output: PlanOutput = PlanOutput()
