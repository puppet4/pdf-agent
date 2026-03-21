"""Prepare booklet page order for duplex printing."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class BookletTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="booklet",
            label="小册子排序",
            category="page_ops",
            description="按 booklet 方式重排页面，便于双面打印后折页装订",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="pikepdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        output_path = workdir / "booklet.pdf"

        with pikepdf.open(inputs[0]) as src:
            page_count = len(src.pages)
            first_box = src.pages[0].mediabox
            page_size = (float(first_box[2] - first_box[0]), float(first_box[3] - first_box[1]))

            padded = pikepdf.Pdf.new()
            padded.pages.extend(src.pages)
            while len(padded.pages) % 4 != 0:
                padded.add_blank_page(page_size=page_size)

            order: list[int] = []
            left = 0
            right = len(padded.pages) - 1
            while left < right:
                order.extend([right, left, left + 1, right - 1])
                left += 2
                right -= 2

            out = pikepdf.Pdf.new()
            for index, page_idx in enumerate(order):
                out.pages.append(padded.pages[page_idx])
                if reporter:
                    reporter(int((index + 1) / len(order) * 100))
            out.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"original_pages": page_count, "booklet_pages": len(order)},
            log=f"Prepared booklet order for {page_count} page(s)",
        )
