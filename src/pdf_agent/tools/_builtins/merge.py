"""Merge tool - combine multiple PDFs into one."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec


class MergeTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="merge",
            label="合并 PDF",
            category="page_ops",
            description="将多个 PDF 文件按顺序合并为一个",
            inputs=ToolInputSpec(min=2, max=50),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="mode",
                    label="合并模式",
                    type="enum",
                    options=["sequential", "interleave"],
                    default="sequential",
                    description="sequential=顺序合并, interleave=交错合并",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        mode = params.get("mode", "sequential")
        if mode not in ("sequential", "interleave"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid merge mode: {mode}")
        return {"mode": mode}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        if len(inputs) < 2:
            raise ToolError(ErrorCode.INVALID_INPUT_FILE, "Merge requires at least 2 input files")

        params = self.validate(params)
        output_path = workdir / "merged.pdf"

        pdf_out = pikepdf.Pdf.new()
        mode = params["mode"]

        if mode == "sequential":
            for i, f in enumerate(inputs):
                with pikepdf.open(f) as src:
                    pdf_out.pages.extend(src.pages)
                if reporter:
                    reporter(int((i + 1) / len(inputs) * 100), f"Merged {i + 1}/{len(inputs)}")
        else:
            # Interleave: take one page from each file alternately
            opened = [pikepdf.open(f) for f in inputs]
            max_pages = max(len(p.pages) for p in opened)
            for page_idx in range(max_pages):
                for pdf in opened:
                    if page_idx < len(pdf.pages):
                        pdf_out.pages.append(pdf.pages[page_idx])
            for pdf in opened:
                pdf.close()

        pdf_out.save(output_path)
        pdf_out.close()

        return ToolResult(
            output_files=[output_path],
            meta={"page_count": len(pikepdf.open(output_path).pages)},
            log=f"Merged {len(inputs)} files ({mode})",
        )
