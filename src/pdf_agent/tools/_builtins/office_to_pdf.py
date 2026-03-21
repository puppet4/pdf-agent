"""Office to PDF conversion via LibreOffice headless mode."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class OfficeToPdfTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="office_to_pdf",
            label="Office 转 PDF",
            category="convert",
            description="将 Word、Excel、PowerPoint 等 Office 文件转为 PDF",
            inputs=ToolInputSpec(
                min=1,
                max=1,
                accept=[
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "application/msword",
                    "application/vnd.ms-excel",
                    "application/vnd.ms-powerpoint",
                ],
            ),
            outputs=ToolOutputSpec(type="pdf"),
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
            reporter(10, "Starting Office to PDF conversion")
        run_command(
            [lo_bin, "--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(inputs[0])],
            timeout=settings.external_cmd_timeout_sec,
        )
        output_path = workdir / f"{inputs[0].stem}.pdf"
        if not output_path.exists():
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "LibreOffice did not produce a PDF")
        if reporter:
            reporter(100, "Done")
        return ToolResult(
            output_files=[output_path],
            meta={"output": output_path.name},
            log=f"Converted {inputs[0].name} to PDF",
        )
