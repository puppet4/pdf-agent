"""PDF to Markdown tool — extract text with structure using pdfminer."""
from __future__ import annotations

from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PdfToMarkdownTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_markdown",
            label="PDF 转 Markdown",
            category="convert",
            description="将 PDF 文本内容提取并转换为 Markdown 格式",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="text"),
            params=[
                ParamSpec(
                    name="preserve_layout",
                    label="保留布局",
                    type="bool",
                    default=False,
                    description="尝试保留原文档的段落结构",
                ),
            ],
            engine="pdfminer",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {"preserve_layout": bool(params.get("preserve_layout", False))}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        try:
            from pdfminer.high_level import extract_pages
            from pdfminer.layout import LTTextContainer
        except ImportError:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdfminer.six not installed. Run: pip install pdfminer.six")

        params = self.validate(params)
        workdir.mkdir(parents=True, exist_ok=True)
        output_path = workdir / "output.md"

        if reporter:
            reporter(10, "Extracting text...")

        lines = []
        try:
            for page_num, page_layout in enumerate(extract_pages(str(inputs[0]))):
                if reporter:
                    reporter(10 + page_num * 5, f"Processing page {page_num + 1}")
                if page_num > 0:
                    lines.append("\n---\n")
                lines.append(f"## Page {page_num + 1}\n")
                for element in page_layout:
                    if isinstance(element, LTTextContainer):
                        text = element.get_text().strip()
                        if text:
                            if params["preserve_layout"]:
                                lines.append(text + "\n")
                            else:
                                compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
                                if compact:
                                    lines.append(compact + "\n")
        except Exception as e:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Text extraction failed: {e}")

        md_content = "\n".join(lines)
        output_path.write_text(md_content, encoding="utf-8")

        if reporter:
            reporter(100, "Done")

        word_count = len(md_content.split())
        return ToolResult(
            output_files=[output_path],
            meta={"pages": page_num + 1, "word_count": word_count},
            log=f"Converted to Markdown: {page_num + 1} pages, ~{word_count} words",
        )
