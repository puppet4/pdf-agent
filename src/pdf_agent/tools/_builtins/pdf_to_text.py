"""PDF to text tool - extract text content from PDF pages."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class PdfToTextTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_text",
            label="PDF 转文本",
            category="convert",
            description="提取 PDF 中的文本内容",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="text"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要提取文本的页面范围",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {"page_range": params.get("page_range", "all")}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        workdir.mkdir(parents=True, exist_ok=True)
        output_path = workdir / localized_output_name(inputs[0], "提取文本", ext=".txt")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            target_pages = parse_page_range(params["page_range"], total)
            text_parts: list[str] = []
            extracted_chars = 0

            for i, idx in enumerate(target_pages):
                page = pdf.pages[idx]
                page_text = _extract_page_text(page)
                extracted_chars += len(page_text.strip())
                text_parts.append(f"--- Page {idx + 1} ---\n{page_text}")
                if reporter:
                    reporter(int((i + 1) / len(target_pages) * 100))

        total_chars = extracted_chars
        if total_chars == 0:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "No extractable text found in the selected pages")

        output_path.write_text("\n\n".join(text_parts), encoding="utf-8")

        return ToolResult(
            output_files=[output_path],
            meta={"pages_extracted": len(target_pages), "total_chars": total_chars},
            log=f"Extracted text from {len(target_pages)} pages",
        )


def _extract_page_text(page: pikepdf.Page) -> str:
    """Extract text from a PDF page by parsing content stream text operators."""
    text_parts: list[str] = []
    try:
        instructions = pikepdf.parse_content_stream(page)
        for operands, operator in instructions:
            op = str(operator)
            if op in ("Tj", "TJ"):
                for operand in operands:
                    if isinstance(operand, pikepdf.String):
                        text_parts.append(str(operand))
                    elif isinstance(operand, pikepdf.Array):
                        for item in operand:
                            if isinstance(item, pikepdf.String):
                                text_parts.append(str(item))
    except Exception as exc:
        raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Failed to extract page text: {exc}") from exc
    return "".join(text_parts)
