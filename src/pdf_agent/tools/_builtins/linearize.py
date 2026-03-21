"""Linearize tool — optimize PDF for fast web viewing (linearization)."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class LinearizeTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="linearize",
            label="Web 优化（线性化）",
            category="optimize",
            description="对 PDF 进行线性化处理，使其能在下载完成前就开始在浏览器中显示（Fast Web View）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="qpdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        qpdf = shutil.which("qpdf")
        if not qpdf:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "qpdf is not installed")

        output_path = workdir / "linearized.pdf"
        if reporter:
            reporter(10, "Linearizing with qpdf...")

        run_command([qpdf, "--linearize", str(inputs[0]), str(output_path)])

        src_size = inputs[0].stat().st_size
        out_size = output_path.stat().st_size

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"original_size": src_size, "linearized_size": out_size},
            log=f"Linearized PDF for fast web viewing ({src_size} → {out_size} bytes)",
        )
