"""PDF to images tool - convert PDF pages to images using pdftoppm."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pikepdf

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PdfToImagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pdf_to_images",
            label="PDF 转图片",
            category="convert",
            description="将 PDF 页面转换为图片（PNG/JPEG/WebP）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="images"),
            params=[
                ParamSpec(
                    name="format",
                    label="图片格式",
                    type="enum",
                    options=["png", "jpeg", "webp"],
                    default="png",
                    description="输出图片格式",
                ),
                ParamSpec(
                    name="dpi",
                    label="分辨率",
                    type="int",
                    default=150,
                    min=72,
                    max=600,
                    description="输出图片 DPI",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要转换的页面范围",
                ),
            ],
            engine="pdftoppm",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        fmt = params.get("format", "png")
        if fmt not in ("png", "jpeg", "webp"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid format: {fmt}")
        return {
            "format": fmt,
            "dpi": int(params.get("dpi", 150)),
            "page_range": params.get("page_range", "all"),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)

        pdftoppm_bin = shutil.which("pdftoppm")
        if not pdftoppm_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm (poppler-utils) is not installed")

        src_path = inputs[0]
        fmt = params["format"]
        dpi = params["dpi"]

        with pikepdf.open(src_path) as pdf:
            total = len(pdf.pages)

        target_pages = parse_page_range(params["page_range"], total)
        is_all_pages = len(target_pages) == total and target_pages == list(range(total))

        if is_all_pages:
            # Convert all pages directly
            output_files = self._convert_pdf(pdftoppm_bin, src_path, workdir, fmt, dpi)
        else:
            # Extract target pages to a temp PDF, then convert
            with tempfile.NamedTemporaryFile(suffix=".pdf", dir=workdir, delete=False) as tmp:
                tmp_path = Path(tmp.name)
            with pikepdf.open(src_path) as src:
                out = pikepdf.Pdf.new()
                for idx in target_pages:
                    out.pages.append(src.pages[idx])
                out.save(tmp_path)
            output_files = self._convert_pdf(pdftoppm_bin, tmp_path, workdir, fmt, dpi)
            tmp_path.unlink(missing_ok=True)

        if reporter:
            reporter(100)

        return ToolResult(
            output_files=output_files,
            meta={"page_count": len(target_pages), "format": fmt, "dpi": dpi},
            log=f"Converted {len(target_pages)} pages to {fmt} at {dpi} DPI",
        )

    @staticmethod
    def _convert_pdf(
        pdftoppm_bin: str,
        pdf_path: Path,
        workdir: Path,
        fmt: str,
        dpi: int,
    ) -> list[Path]:
        # pdftoppm format flags
        fmt_flag_map = {"png": "-png", "jpeg": "-jpeg", "webp": "-webp"}
        fmt_flag = fmt_flag_map[fmt]

        output_prefix = workdir / "page"
        cmd = [
            pdftoppm_bin,
            fmt_flag,
            "-r", str(dpi),
            str(pdf_path),
            str(output_prefix),
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=settings.external_cmd_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, "pdftoppm conversion timed out")
        except subprocess.CalledProcessError as exc:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"pdftoppm failed: {exc.stderr.decode(errors='replace')}")

        # Collect output files (pdftoppm names them: page-01.png, page-02.png, ...)
        ext = fmt if fmt != "jpeg" else "jpg"
        output_files = sorted(workdir.glob(f"page-*.{ext}"))
        if not output_files:
            # Try alternate extension for jpeg
            output_files = sorted(workdir.glob("page-*.jpeg"))
        if not output_files:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "pdftoppm produced no output files")

        return output_files
