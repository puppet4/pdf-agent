"""PDF to Excel/PPT tool — convert PDF to xlsx or pptx via LibreOffice."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PdfToExcelTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_excel",
            label="PDF 转 Excel",
            category="convert",
            description="将 PDF 转换为 Excel 表格文件（.xlsx）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="xlsx"),
            params=[],
            engine="libreoffice",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
        if not lo_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "LibreOffice is not installed")
        if reporter:
            reporter(10, "Starting conversion...")
        run_command([lo_bin, "--headless", "--convert-to", "xlsx", "--outdir", str(workdir), str(inputs[0])])
        output_path = workdir / (inputs[0].stem + ".xlsx")
        if not output_path.exists():
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "No .xlsx file produced")
        if reporter:
            reporter(100, "Done")
        return ToolResult(output_files=[output_path], meta={"output": output_path.name}, log=f"Converted to {output_path.name}")


class PdfToPptTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_ppt",
            label="PDF 转 PPT",
            category="convert",
            description="将 PDF 转换为 PowerPoint 演示文稿（.pptx）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pptx"),
            params=[],
            engine="libreoffice",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
        if not lo_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "LibreOffice is not installed")
        if reporter:
            reporter(10, "Starting conversion...")
        run_command([lo_bin, "--headless", "--convert-to", "pptx", "--outdir", str(workdir), str(inputs[0])])
        output_path = workdir / (inputs[0].stem + ".pptx")
        if not output_path.exists():
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "No .pptx file produced")
        if reporter:
            reporter(100, "Done")
        return ToolResult(output_files=[output_path], meta={"output": output_path.name}, log=f"Converted to {output_path.name}")
