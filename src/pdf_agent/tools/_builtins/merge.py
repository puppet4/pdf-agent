"""Merge tool - combine multiple PDFs into one."""
from __future__ import annotations

import contextlib
from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name
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
                    options=["sequential", "interleave", "insert"],
                    default="sequential",
                    description="sequential=顺序合并, interleave=交错合并, insert=在指定位置插入第二个 PDF",
                ),
                ParamSpec(
                    name="insert_position",
                    label="插入位置",
                    type="int",
                    default=1,
                    min=1,
                    description="insert 模式时，在第 N 页之后插入第二个 PDF",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        mode = params.get("mode", "sequential")
        if mode not in ("sequential", "interleave", "insert"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid merge mode: {mode}")
        return {"mode": mode, "insert_position": max(1, int(params.get("insert_position", 1)))}

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
        output_path = workdir / localized_output_name(inputs[0], "已合并")

        pdf_out = pikepdf.Pdf.new()
        mode = params["mode"]

        try:
            if mode == "sequential":
                for i, f in enumerate(inputs):
                    with pikepdf.open(f) as src:
                        pdf_out.pages.extend(src.pages)
                    if reporter:
                        reporter(int((i + 1) / len(inputs) * 100), f"Merged {i + 1}/{len(inputs)}")
                pdf_out.save(output_path)
            else:
                with contextlib.ExitStack() as stack:
                    opened = [stack.enter_context(pikepdf.open(f)) for f in inputs]
                    if mode == "interleave":
                        max_pages = max(len(p.pages) for p in opened)
                        for page_idx in range(max_pages):
                            for pdf in opened:
                                if page_idx < len(pdf.pages):
                                    pdf_out.pages.append(pdf.pages[page_idx])
                    else:
                        if len(opened) < 2:
                            raise ToolError(ErrorCode.INVALID_INPUT_FILE, "Insert merge requires 2 PDFs")
                        base_pdf = opened[0]
                        insert_pdf = opened[1]
                        insert_after = min(params["insert_position"], len(base_pdf.pages))
                        for page_idx, page in enumerate(base_pdf.pages, start=1):
                            pdf_out.pages.append(page)
                            if page_idx == insert_after:
                                pdf_out.pages.extend(insert_pdf.pages)
                        if insert_after >= len(base_pdf.pages):
                            pass
                    pdf_out.save(output_path)
        finally:
            pdf_out.close()

        with pikepdf.open(output_path) as result_pdf:
            page_count = len(result_pdf.pages)

        return ToolResult(
            output_files=[output_path],
            meta={"page_count": page_count},
            log=f"Merged {len(inputs)} files ({mode})",
        )
