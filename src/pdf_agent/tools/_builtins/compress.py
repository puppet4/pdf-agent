"""Compress tool - compress PDF using Ghostscript."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class CompressTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="compress",
            label="压缩 PDF",
            category="optimize",
            description="使用 Ghostscript 压缩 PDF 文件体积",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="level",
                    label="压缩级别",
                    type="enum",
                    options=["low", "medium", "high"],
                    default="medium",
                    description="low=质量优先, medium=均衡, high=体积优先",
                ),
            ],
            engine="ghostscript",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        level = params.get("level", "medium")
        if level not in ("low", "medium", "high"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid compression level: {level}")
        return {"level": level}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)

        gs_bin = shutil.which("gs")
        if not gs_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Ghostscript (gs) is not installed")

        output_path = workdir / "compressed.pdf"
        src_path = inputs[0]

        # Map compression level to Ghostscript PDF settings
        level_map = {
            "low": "/prepress",
            "medium": "/ebook",
            "high": "/screen",
        }
        pdf_settings = level_map[params["level"]]

        cmd = [
            gs_bin,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.5",
            f"-dPDFSETTINGS={pdf_settings}",
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            f"-sOutputFile={output_path}",
            str(src_path),
        ]

        try:
            run_command(cmd)
        except ToolError:
            raise

        src_size = src_path.stat().st_size
        out_size = output_path.stat().st_size
        ratio = (1 - out_size / src_size) * 100 if src_size > 0 else 0

        return ToolResult(
            output_files=[output_path],
            meta={
                "original_size": src_size,
                "compressed_size": out_size,
                "reduction_percent": round(ratio, 1),
            },
            log=f"Compressed PDF: {src_size} → {out_size} bytes ({ratio:.1f}% reduction)",
        )
