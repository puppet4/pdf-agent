"""Rotate tool - rotate pages in a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec


class RotateTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="rotate",
            label="旋转页面",
            category="page_ops",
            description="旋转 PDF 的指定页面（90/180/270 度）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="angle",
                    label="旋转角度",
                    type="enum",
                    options=["90", "180", "270"],
                    required=True,
                    description="顺时针旋转角度",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要旋转的页面范围，如 1-3,5",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        angle = int(params.get("angle", 0))
        if angle not in (90, 180, 270):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Angle must be 90, 180, or 270, got {angle}")
        return {"angle": angle, "page_range": params.get("page_range", "all")}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已旋转")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)
            for idx in pages:
                pdf.pages[idx].rotate(params["angle"], relative=True)
            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"rotated_pages": len(pages), "angle": params["angle"]},
            log=f"Rotated {len(pages)} pages by {params['angle']}°",
        )
