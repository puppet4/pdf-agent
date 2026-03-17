"""Delete tool - delete pages from a PDF by page range."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class DeleteTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="delete",
            label="删除页面",
            category="page_ops",
            description="按页范围删除页面，保留剩余页面",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    required=True,
                    description="要删除的页面范围，如 1-3,5",
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
        output_path = workdir / "deleted.pdf"

        with pikepdf.open(inputs[0]) as src:
            total = len(src.pages)
            delete_indices = set(parse_page_range(params["page_range"], total))
            keep_indices = [i for i in range(total) if i not in delete_indices]

            if not keep_indices:
                raise ToolError(ErrorCode.INVALID_PARAMS, "Cannot delete all pages")

            out = pikepdf.Pdf.new()
            for idx in keep_indices:
                out.pages.append(src.pages[idx])
            out.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"source_pages": total, "deleted_pages": len(delete_indices), "remaining_pages": len(keep_indices)},
            log=f"Deleted {len(delete_indices)} pages, {len(keep_indices)} remaining",
        )
