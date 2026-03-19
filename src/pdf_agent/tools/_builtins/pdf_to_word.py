"""PDF to Word tool — convert PDF to DOCX using LibreOffice."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PdfToWordTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_word",
            label="PDF 转 Word",
            category="convert",
            description="将 PDF 转换为可编辑的 Word 文档（.docx）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="docx"),
            params=[],
            engine="libreoffice",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
        if not lo_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "LibreOffice is not installed")

        if reporter:
            reporter(10, "Starting LibreOffice conversion...")

        cmd = [
            lo_bin,
            "--headless",
            "--convert-to", "docx",
            "--outdir", str(workdir),
            str(inputs[0]),
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=settings.external_cmd_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, "LibreOffice conversion timed out")
        except subprocess.CalledProcessError as exc:
            raise ToolError(
                ErrorCode.ENGINE_EXEC_FAILED,
                f"LibreOffice failed: {exc.stderr.decode(errors='replace')}",
            )

        # LibreOffice outputs file with same stem + .docx
        output_path = workdir / (inputs[0].stem + ".docx")
        if not output_path.exists():
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "LibreOffice did not produce a .docx file")

        if reporter:
            reporter(100, "Conversion complete")

        return ToolResult(
            output_files=[output_path],
            meta={"original": inputs[0].name, "output": output_path.name},
            log=f"Converted {inputs[0].name} to {output_path.name}",
        )
