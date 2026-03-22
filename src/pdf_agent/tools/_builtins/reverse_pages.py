"""Reverse pages tool — reverse the page order of a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class ReversePagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="reverse_pages",
            label="页面倒序",
            category="page_ops",
            description="将 PDF 所有页面的顺序反转（最后一页变第一页）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        output_path = workdir / localized_output_name(inputs[0], "已倒序页面")
        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            out = pikepdf.Pdf.new()
            for i in range(total - 1, -1, -1):
                out.pages.append(pdf.pages[i])
            out.save(output_path)
        return ToolResult(
            output_files=[output_path],
            meta={"pages": total},
            log=f"Reversed {total} pages",
        )
