"""Split tool - split PDF by page ranges or fixed chunk size."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec


class SplitTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="split",
            label="拆分 PDF",
            category="page_ops",
            description="按页范围、每页一个或固定页数拆分 PDF",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="zip"),
            params=[
                ParamSpec(
                    name="mode",
                    label="拆分模式",
                    type="enum",
                    options=["range", "each_page", "chunk"],
                    default="each_page",
                    description="range=按页范围, each_page=每页一个, chunk=按固定页数",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    description="拆分模式为 range 时使用，如 1-3,5,7-9",
                ),
                ParamSpec(
                    name="chunk_size",
                    label="每块页数",
                    type="int",
                    default=1,
                    min=1,
                    description="拆分模式为 chunk 时使用",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        mode = params.get("mode", "each_page")
        if mode not in ("range", "each_page", "chunk"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid split mode: {mode}")
        return {
            "mode": mode,
            "page_range": params.get("page_range", ""),
            "chunk_size": int(params.get("chunk_size", 1)),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        src_path = inputs[0]
        output_files: list[Path] = []

        with pikepdf.open(src_path) as src:
            total = len(src.pages)
            mode = params["mode"]

            if mode == "range":
                pages = parse_page_range(params["page_range"], total)
                out = pikepdf.Pdf.new()
                for idx in pages:
                    out.pages.append(src.pages[idx])
                out_path = workdir / "split_range.pdf"
                out.save(out_path)
                output_files.append(out_path)

            elif mode == "each_page":
                for i in range(total):
                    out = pikepdf.Pdf.new()
                    out.pages.append(src.pages[i])
                    out_path = workdir / f"page_{i + 1:04d}.pdf"
                    out.save(out_path)
                    output_files.append(out_path)
                    if reporter:
                        reporter(int((i + 1) / total * 100))

            elif mode == "chunk":
                chunk_size = params["chunk_size"]
                for chunk_start in range(0, total, chunk_size):
                    out = pikepdf.Pdf.new()
                    for i in range(chunk_start, min(chunk_start + chunk_size, total)):
                        out.pages.append(src.pages[i])
                    chunk_idx = chunk_start // chunk_size + 1
                    out_path = workdir / f"chunk_{chunk_idx:04d}.pdf"
                    out.save(out_path)
                    output_files.append(out_path)

        return ToolResult(
            output_files=output_files,
            meta={"total_pages": total, "output_count": len(output_files)},
            log=f"Split {total} pages into {len(output_files)} files ({params['mode']})",
        )
