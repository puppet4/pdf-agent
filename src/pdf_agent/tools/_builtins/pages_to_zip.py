"""Export pages to ZIP tool — convert each PDF page to an image and package as ZIP."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PagesToZipTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="pages_to_zip",
            label="页面导出为图片 ZIP",
            category="convert",
            description="将 PDF 每页转为图片，打包成 ZIP 压缩包下载",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="zip"),
            params=[
                ParamSpec(name="format", label="图片格式", type="enum",
                          options=["jpeg", "png", "tiff"], default="jpeg"),
                ParamSpec(name="dpi", label="DPI", type="int", default=150, min=72, max=300),
            ],
            engine="poppler",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        fmt = params.get("format", "jpeg")
        if fmt not in ("jpeg", "png", "tiff"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid format: {fmt}")
        return {
            "format": fmt,
            "dpi": max(72, min(300, int(params.get("dpi", 150)))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        pdftoppm = shutil.which("pdftoppm")
        if not pdftoppm:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm (poppler-utils) is not installed")

        params = self.validate(params)
        output_zip = workdir / "pages.zip"
        fmt = params["format"]
        ext = "jpg" if fmt == "jpeg" else fmt
        ppm_flag = f"-{fmt}"

        if reporter:
            reporter(10, "Rendering pages...")

        with tempfile.TemporaryDirectory() as td:
            out_stem = Path(td) / "page"
            run_command([pdftoppm, "-r", str(params["dpi"]), ppm_flag, str(inputs[0]), str(out_stem)])

            images = sorted(Path(td).glob(f"*.{ext}"))
            if not images:
                images = sorted(Path(td).iterdir())

            if reporter:
                reporter(80, f"Packaging {len(images)} images...")

            with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for img in images:
                    zf.write(img, arcname=img.name)

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_zip],
            meta={"page_count": len(images), "format": fmt, "dpi": params["dpi"]},
            log=f"Exported {len(images)} pages as {fmt.upper()} images in ZIP",
        )
