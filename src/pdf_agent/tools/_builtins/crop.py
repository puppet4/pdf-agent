"""Crop tool - crop PDF pages by adjusting the media box."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class CropTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="crop",
            label="裁剪页面",
            category="page_ops",
            description="裁剪 PDF 页面边距（按点数或百分比）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="top", label="上边距", type="float", default=0, min=0, description="上边裁剪量（点）"),
                ParamSpec(name="bottom", label="下边距", type="float", default=0, min=0, description="下边裁剪量（点）"),
                ParamSpec(name="left", label="左边距", type="float", default=0, min=0, description="左边裁剪量（点）"),
                ParamSpec(name="right", label="右边距", type="float", default=0, min=0, description="右边裁剪量（点）"),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要裁剪的页面范围",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        top = float(params.get("top", 0))
        bottom = float(params.get("bottom", 0))
        left = float(params.get("left", 0))
        right = float(params.get("right", 0))
        if top < 0 or bottom < 0 or left < 0 or right < 0:
            raise ToolError(ErrorCode.INVALID_PARAMS, "Crop margins must be non-negative")
        return {
            "top": top,
            "bottom": bottom,
            "left": left,
            "right": right,
            "page_range": params.get("page_range", "all"),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "cropped.pdf"

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], total))

            for i in range(total):
                if i not in target_pages:
                    continue
                page = pdf.pages[i]
                mbox = page.mediabox
                x0, y0, x1, y1 = float(mbox[0]), float(mbox[1]), float(mbox[2]), float(mbox[3])

                new_x0 = x0 + params["left"]
                new_y0 = y0 + params["bottom"]
                new_x1 = x1 - params["right"]
                new_y1 = y1 - params["top"]

                if new_x0 >= new_x1 or new_y0 >= new_y1:
                    raise ToolError(
                        ErrorCode.INVALID_PARAMS,
                        f"Crop margins too large for page {i + 1} ({x1 - x0:.0f}x{y1 - y0:.0f} pt)",
                    )

                page.mediabox = [new_x0, new_y0, new_x1, new_y1]
                page.cropbox = [new_x0, new_y0, new_x1, new_y1]

                if reporter:
                    reporter(int((i + 1) / total * 100))

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"cropped_pages": len(target_pages)},
            log=f"Cropped {len(target_pages)} pages",
        )
