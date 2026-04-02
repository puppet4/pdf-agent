"""Deskew tool is intentionally disabled; use OCR deskew instead."""
from __future__ import annotations

from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class DeskewTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="deskew",
            label="自动纠偏",
            category="page_ops",
            description="当前独立纠偏工具已禁用；请改用 OCR 工具并设置 deskew=true",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="保留兼容的页范围参数；请改用 OCR 工具处理",
                ),
                ParamSpec(
                    name="min_angle",
                    label="最小校正角度(度)",
                    type="float",
                    default=0.5,
                    min=0.1,
                    max=45.0,
                    description="保留兼容参数；当前独立纠偏工具已禁用",
                ),
            ],
            engine="ocrmypdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {
            "page_range": params.get("page_range", "all"),
            "min_angle": max(0.1, float(params.get("min_angle", 0.5))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        self.validate(params)
        raise ToolError(
            ErrorCode.INVALID_PARAMS,
            "The standalone deskew tool is disabled. Use the ocr tool with deskew=true instead.",
        )
