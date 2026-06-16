"""工具 manifest 使用的 schema 定义。"""
from __future__ import annotations

from pydantic import BaseModel


class ParamSpec(BaseModel):
    """描述工具 manifest 中的单个参数定义。"""
    name: str
    label: str
    # 支持的参数类型：string / int / float / bool / enum / page_range / file
    type: str
    required: bool = False
    default: str | int | float | bool | None = None
    # 仅在 `enum` 类型下使用的候选值列表
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None
    description: str = ""


class ToolManifest(BaseModel):
    """面向外部的工具 manifest，描述能力、输入输出与参数。"""
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
    # 可接受的 MIME 类型列表
    accept: list[str] = ["application/pdf"]


class ToolOutputSpec(BaseModel):
    # 输出类型枚举：pdf / zip / images / text / json
    type: str = "pdf"
