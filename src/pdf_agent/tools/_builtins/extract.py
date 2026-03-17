"""Extract tool - extract pages from a PDF by page range."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class ExtractTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="extract",
            label="提取页面",
            category="page_ops",
            description="按页范围提取页面生成新 PDF",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    required=True,
                    description="要提取的页面范围，如 1-3,5,7-9",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        page_range = params.get("page_range", "")
        if not page_range:
            raise ToolError(ErrorCode.INVALID_PARAMS, "page_range is required")
        return {"page_range": page_range}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "extracted.pdf"

        with pikepdf.open(inputs[0]) as src:
            total = len(src.pages)
            pages = parse_page_range(params["page_range"], total)
            out = pikepdf.Pdf.new()
            for i, idx in enumerate(pages):
                out.pages.append(src.pages[idx])
                if reporter:
                    reporter(int((i + 1) / len(pages) * 100))
            out.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"source_pages": total, "extracted_pages": len(pages)},
            log=f"Extracted {len(pages)} pages from {total}-page PDF",
        )
