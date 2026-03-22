"""Resize tool - resize PDF pages to a target size."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name

# Standard page sizes in points (72 dpi)
_PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "Letter": (612, 792),
    "Legal": (612, 1008),
}


class ResizeTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="resize",
            label="调整页面大小",
            category="page_ops",
            description="将 PDF 页面缩放到指定尺寸",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="target_size",
                    label="目标尺寸",
                    type="enum",
                    options=["A3", "A4", "A5", "Letter", "Legal"],
                    default="A4",
                    description="目标页面尺寸",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要调整的页面范围",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        target_size = params.get("target_size", "A4")
        if target_size not in _PAGE_SIZES:
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Unsupported page size: {target_size}")
        return {
            "target_size": target_size,
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
        output_path = workdir / localized_output_name(inputs[0], "已调整尺寸")
        target_w, target_h = _PAGE_SIZES[params["target_size"]]

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], total))

            for i in range(total):
                if i not in target_pages:
                    continue

                page = pdf.pages[i]
                mbox = page.mediabox
                cur_w = float(mbox[2] - mbox[0])
                cur_h = float(mbox[3] - mbox[1])

                scale_x = target_w / cur_w
                scale_y = target_h / cur_h

                # Use uniform scale to maintain aspect ratio, then center
                scale = min(scale_x, scale_y)
                offset_x = (target_w - cur_w * scale) / 2
                offset_y = (target_h - cur_h * scale) / 2

                page.mediabox = [0, 0, target_w, target_h]
                page.cropbox = [0, 0, target_w, target_h]

                # Apply transform: translate + scale
                content = pikepdf.parse_content_stream(page)
                new_content = (
                    f"q {scale:.6f} 0 0 {scale:.6f} {offset_x:.6f} {offset_y:.6f} cm\n"
                ).encode()
                old_stream = pikepdf.unparse_content_stream(content)
                new_stream = new_content + old_stream + b"\nQ\n"
                page.Contents = pdf.make_stream(new_stream)

                if reporter:
                    reporter(int((i + 1) / total * 100))

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"resized_pages": len(target_pages), "target_size": params["target_size"]},
            log=f"Resized {len(target_pages)} pages to {params['target_size']}",
        )
