"""Tool manifest schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ParamSpec(BaseModel):
    """A single parameter definition in the tool manifest."""
    name: str
    label: str
    type: str  # string | int | float | bool | enum | page_range | file
    required: bool = False
    default: str | int | float | bool | None = None
    options: list[str] | None = None  # for enum type
    min: float | None = None
    max: float | None = None
    description: str = ""


class ToolManifest(BaseModel):
    """Public manifest describing a tool's capabilities and parameters."""
    name: str
    label: str
    category: str
    description: str = ""
    inputs: ToolInputSpec
    outputs: ToolOutputSpec
    params: list[ParamSpec] = []
    engine: str = ""
    async_hint: bool = False


class ToolInputSpec(BaseModel):
    min: int = 1
    max: int = 1
    accept: list[str] = ["application/pdf"]  # MIME types


class ToolOutputSpec(BaseModel):
    type: str = "pdf"  # pdf | zip | images | text | json
