"""Tile pages tool — combine multiple PDF pages side-by-side or top-to-bottom."""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

import pikepdf
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


def _render_first_page_png(pdf_path: Path, tmpdir: Path, dpi: int = 96) -> Path | None:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    out_stem = tmpdir / pdf_path.stem
    result = run_command(
        [pdftoppm, "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(pdf_path), str(out_stem)],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    candidates = list(tmpdir.glob(f"{pdf_path.stem}*.png"))
    return candidates[0] if candidates else None


class TilePagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="tile_pages",
            label="页面拼接",
            category="page_ops",
            description="将多个 PDF 的首页横向或纵向拼接成一个宽版/长版页面",
            inputs=ToolInputSpec(min=2, max=10, accept=["application/pdf"]),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="direction",
                    label="拼接方向",
                    type="enum",
                    options=["horizontal", "vertical"],
                    default="horizontal",
                    description="horizontal=横向并排, vertical=纵向叠加",
                ),
            ],
            engine="pikepdf+reportlab+poppler",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        direction = params.get("direction", "horizontal")
        if direction not in ("horizontal", "vertical"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid direction: {direction}")
        return {"direction": direction}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        if not shutil.which("pdftoppm"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm (poppler-utils) is not installed")

        params = self.validate(params)
        output_path = workdir / "tiled.pdf"
        direction = params["direction"]

        # Get page dimensions from each PDF
        page_dims = []
        for pdf_path in inputs:
            with pikepdf.open(pdf_path) as pdf:
                mbox = pdf.pages[0].mediabox
                w = float(mbox[2]) - float(mbox[0])
                h = float(mbox[3]) - float(mbox[1])
                page_dims.append((w, h))

        if direction == "horizontal":
            total_w = sum(d[0] for d in page_dims)
            total_h = max(d[1] for d in page_dims)
        else:
            total_w = max(d[0] for d in page_dims)
            total_h = sum(d[1] for d in page_dims)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(total_w, total_h))

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            x_offset = 0.0
            y_offset = total_h

            for i, pdf_path in enumerate(inputs):
                if reporter:
                    reporter(int(i / len(inputs) * 90), f"Processing {pdf_path.name}")
                pw, ph = page_dims[i]

                img_path = _render_first_page_png(pdf_path, tmpdir)
                if not img_path:
                    raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Failed to render {pdf_path.name}")

                if direction == "horizontal":
                    y = (total_h - ph) / 2
                    c.drawImage(ImageReader(str(img_path)), x_offset, y, width=pw, height=ph)
                    x_offset += pw
                else:
                    y_offset -= ph
                    x = (total_w - pw) / 2
                    c.drawImage(ImageReader(str(img_path)), x, y_offset, width=pw, height=ph)

        c.save()
        buf.seek(0)
        output_path.write_bytes(buf.read())

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"inputs": len(inputs), "direction": direction, "size": f"{total_w:.0f}x{total_h:.0f}pt"},
            log=f"Tiled {len(inputs)} PDFs {direction}ly into {total_w:.0f}x{total_h:.0f}pt page",
        )
