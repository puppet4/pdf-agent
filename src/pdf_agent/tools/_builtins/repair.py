"""Repair tool — attempt to repair a corrupted PDF using Ghostscript."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class RepairTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="repair",
            label="修复 PDF",
            category="optimize",
            description="尝试修复损坏或格式错误的 PDF 文件",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="ghostscript",
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
        gs_bin = shutil.which("gs")
        if not gs_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Ghostscript (gs) is not installed")

        output_path = workdir / "repaired.pdf"

        if reporter:
            reporter(10, "Running Ghostscript repair...")

        cmd = [
            gs_bin,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.5",
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            "-dFIXEDMEDIA",
            f"-sOutputFile={output_path}",
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
            raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, "Ghostscript repair timed out")
        except subprocess.CalledProcessError as exc:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Repair failed: {exc.stderr.decode(errors='replace')}")

        if reporter:
            reporter(100, "Repair complete")

        return ToolResult(
            output_files=[output_path],
            meta={"original_size": inputs[0].stat().st_size, "repaired_size": output_path.stat().st_size},
            log="PDF repaired successfully",
        )
