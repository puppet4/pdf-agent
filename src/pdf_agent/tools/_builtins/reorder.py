"""Reorder tool - reorder pages in a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class ReorderTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="reorder",
            label="重排页面",
            category="page_ops",
            description="按指定顺序重新排列 PDF 页面",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="order",
                    label="页面顺序",
                    type="string",
                    required=True,
                    description="新的页面顺序，1-based 逗号分隔，如 3,1,2,5,4",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        order_str = params.get("order", "")
        if not order_str:
            raise ToolError(ErrorCode.INVALID_PARAMS, "order is required")
        try:
            order = [int(x.strip()) for x in order_str.split(",")]
        except ValueError:
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid order format: {order_str}")
        if not order:
            raise ToolError(ErrorCode.INVALID_PARAMS, "order cannot be empty")
        return {"order": order}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已重排页面")
        order = params["order"]

        with pikepdf.open(inputs[0]) as src:
            total = len(src.pages)
            for page_num in order:
                if page_num < 1 or page_num > total:
                    raise ToolError(
                        ErrorCode.INVALID_PAGE_RANGE,
                        f"Page {page_num} out of range (1-{total})",
                    )

            out = pikepdf.Pdf.new()
            for page_num in order:
                out.pages.append(src.pages[page_num - 1])
            out.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"source_pages": total, "output_pages": len(order)},
            log=f"Reordered {total}-page PDF to {len(order)} pages",
        )
