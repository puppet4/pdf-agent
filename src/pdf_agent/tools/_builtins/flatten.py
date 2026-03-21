"""Flatten tool - flatten PDF form fields and annotations into static content."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class FlattenTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="flatten",
            label="扁平化 PDF",
            category="edit",
            description="将 PDF 表单字段和注释扁平化为静态内容",
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
        self.validate(params)

        gs_bin = shutil.which("gs")
        if not gs_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Ghostscript (gs) is not installed")

        output_path = workdir / "flattened.pdf"

        cmd = [
            gs_bin,
            "-sDEVICE=pdfwrite",
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            "-dPreserveAnnots=false",
            f"-sOutputFile={output_path}",
            str(inputs[0]),
        ]

        run_command(cmd)

        return ToolResult(
            output_files=[output_path],
            meta={},
            log="PDF flattened successfully",
        )
