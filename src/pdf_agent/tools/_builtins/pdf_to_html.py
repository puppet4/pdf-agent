"""PDF to HTML tool — convert PDF to HTML using pdfminer or poppler."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PdfToHtmlTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_html",
            label="PDF 转 HTML",
            category="convert",
            description="将 PDF 转换为 HTML 格式，保留文本结构（使用 pdftohtml 或 LibreOffice）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="html"),
            params=[
                ParamSpec(
                    name="single_page",
                    label="单页模式",
                    type="bool",
                    default=True,
                    description="生成单个 HTML 文件（否则每页生成独立文件）",
                ),
            ],
            engine="poppler",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {"single_page": bool(params.get("single_page", True))}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)

        # Try pdftohtml (poppler) first
        pdftohtml = shutil.which("pdftohtml")
        if pdftohtml:
            return self._run_pdftohtml(pdftohtml, inputs[0], workdir, params, reporter)

        # Fall back to LibreOffice
        lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
        if lo_bin:
            return self._run_libreoffice(lo_bin, inputs[0], workdir, reporter)

        raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Neither pdftohtml (poppler) nor LibreOffice is installed")

    def _run_pdftohtml(self, bin_path: str, pdf_path: Path, workdir: Path, params: dict, reporter) -> ToolResult:
        if reporter:
            reporter(10, "Converting with pdftohtml...")
        output_stem = workdir / pdf_path.stem
        cmd = [bin_path, "-noframes", "-nodrm"]
        if params["single_page"]:
            cmd.append("-s")  # single HTML file
        cmd += [str(pdf_path), str(output_stem)]
        run_command(cmd)

        # Find output file
        html_files = list(workdir.glob("*.html")) + list(workdir.glob("*.htm"))
        if not html_files:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "pdftohtml produced no output")

        if reporter:
            reporter(100, "Done")
        return ToolResult(
            output_files=html_files,
            meta={"engine": "pdftohtml", "files": len(html_files)},
            log=f"Converted to {len(html_files)} HTML file(s)",
        )

    def _run_libreoffice(self, lo_bin: str, pdf_path: Path, workdir: Path, reporter) -> ToolResult:
        if reporter:
            reporter(10, "Converting with LibreOffice...")
        run_command([lo_bin, "--headless", "--convert-to", "html", "--outdir", str(workdir), str(pdf_path)])

        output_path = workdir / (pdf_path.stem + ".html")
        if not output_path.exists():
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "LibreOffice produced no HTML file")

        if reporter:
            reporter(100, "Done")
        return ToolResult(
            output_files=[output_path],
            meta={"engine": "libreoffice"},
            log=f"Converted to {output_path.name}",
        )
