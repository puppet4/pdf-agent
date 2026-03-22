"""PDF/A conversion tool — convert PDF to PDF/A archival format via Ghostscript."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


# ICC color profile path (bundled with Ghostscript)
_ICC_PROFILE = "/usr/share/ghostscript/iccprofiles/default_rgb.icc"
_PDFA_DEF_TEMPLATE = """% This is included by pdfaSave.ps
[ /Title ({title})
  /DOCINFO pdfmark
[ /Author ({author})
  /DOCINFO pdfmark"""


class PdfATool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_pdfa",
            label="转换为 PDF/A",
            category="convert",
            description="将 PDF 转换为 PDF/A 格式（长期归档标准），使用 Ghostscript",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="level",
                    label="PDF/A 级别",
                    type="enum",
                    options=["1b", "2b", "3b"],
                    default="2b",
                    description="1b=基本级, 2b=通用级, 3b=支持可选内容",
                ),
            ],
            engine="ghostscript",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        level = params.get("level", "2b")
        if level not in ("1b", "2b", "3b"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid PDF/A level: {level}")
        return {"level": level}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        gs_bin = shutil.which("gs")
        if not gs_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Ghostscript (gs) is not installed")

        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], f"归档版_PDFA-{params['level'].upper()}")
        level_map = {"1b": "1", "2b": "2", "3b": "3"}
        pdfa_level = level_map[params["level"]]

        if reporter:
            reporter(10, f"Converting to PDF/A-{params['level']}...")

        cmd = [
            gs_bin,
            "-dBATCH", "-dNOPAUSE", "-dQUIET",
            "-sDEVICE=pdfwrite",
            f"-dPDFA={pdfa_level}",
            "-dPDFACompatibilityPolicy=1",
            "-dCompatibilityLevel=1.4",
            f"-sOutputFile={output_path}",
            str(inputs[0]),
        ]

        run_command(cmd)

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"level": f"PDF/A-{params['level']}", "size": output_path.stat().st_size},
            log=f"Converted to PDF/A-{params['level']}",
        )
