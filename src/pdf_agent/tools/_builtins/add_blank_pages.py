"""Add blank pages into a PDF at selected positions."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class AddBlankPagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="add_blank_pages",
            label="插入空白页",
            category="page_ops",
            description="在指定页面后插入一个或多个空白页",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="page_range", label="插入位置", type="page_range", default="all", description="在这些页之后插入空白页"),
                ParamSpec(name="count", label="每处插入页数", type="int", default=1, min=1, max=20),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {
            "page_range": params.get("page_range", "all"),
            "count": max(1, min(20, int(params.get("count", 1)))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已插入空白页")

        with pikepdf.open(inputs[0]) as src:
            total = len(src.pages)
            targets = set(parse_page_range(params["page_range"], total))
            if not targets:
                raise ToolError(ErrorCode.INVALID_PARAMS, "No pages selected for blank-page insertion")

            out = pikepdf.Pdf.new()
            first_box = src.pages[0].mediabox
            page_size = (float(first_box[2] - first_box[0]), float(first_box[3] - first_box[1]))

            for idx, page in enumerate(src.pages):
                out.pages.append(page)
                if idx in targets:
                    for _ in range(params["count"]):
                        out.add_blank_page(page_size=page_size)
                if reporter:
                    reporter(int((idx + 1) / total * 100), f"Processed {idx + 1}/{total} pages")

            out.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"inserted_after_pages": sorted(page + 1 for page in targets), "count_per_position": params["count"]},
            log=f"Inserted {params['count']} blank page(s) after {len(targets)} position(s)",
        )
