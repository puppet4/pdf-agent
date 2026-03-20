"""N-up tool — arrange multiple PDF pages onto one page (2-up, 4-up, etc.)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pikepdf
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult

# N-up layout: (columns, rows)
_LAYOUTS: dict[str, tuple[int, int]] = {
    "2-up": (2, 1),
    "4-up": (2, 2),
    "6-up": (3, 2),
    "9-up": (3, 3),
}


def _render_page_to_png(pdf_path: Path, page_idx: int, tmpdir: Path, dpi: int = 96) -> Path | None:
    """Render a single PDF page to PNG using pdftoppm."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    out_stem = tmpdir / f"p{page_idx}"
    subprocess.run(
        [pdftoppm, "-r", str(dpi), "-png", "-f", str(page_idx + 1), "-l", str(page_idx + 1),
         str(pdf_path), str(out_stem)],
        capture_output=True, timeout=30,
    )
    candidates = list(tmpdir.glob(f"p{page_idx}*.png"))
    return candidates[0] if candidates else None


class NUpTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="nup",
            label="N-up 拼版",
            category="page_ops",
            description="将多个 PDF 页面缩小排列在一张纸上，适合节省打印用纸",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="layout",
                    label="版式",
                    type="enum",
                    options=["2-up", "4-up", "6-up", "9-up"],
                    default="2-up",
                    description="每张纸排列的页面数量",
                ),
                ParamSpec(
                    name="paper_size",
                    label="纸张大小",
                    type="enum",
                    options=["A4", "A3", "Letter"],
                    default="A4",
                    description="输出纸张尺寸",
                ),
                ParamSpec(
                    name="orientation",
                    label="方向",
                    type="enum",
                    options=["portrait", "landscape"],
                    default="portrait",
                    description="纸张方向",
                ),
            ],
            engine="pikepdf+reportlab+poppler",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        layout = params.get("layout", "2-up")
        if layout not in _LAYOUTS:
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Unsupported layout: {layout}")
        return {
            "layout": layout,
            "paper_size": params.get("paper_size", "A4"),
            "orientation": params.get("orientation", "portrait"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        if not shutil.which("pdftoppm"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm (poppler-utils) is not installed")

        params = self.validate(params)
        output_path = workdir / "nup.pdf"

        paper_sizes = {"A4": (595.28, 841.89), "A3": (841.89, 1190.55), "Letter": (612, 792)}
        pw, ph = paper_sizes[params["paper_size"]]
        if params["orientation"] == "landscape":
            pw, ph = ph, pw

        cols, rows = _LAYOUTS[params["layout"]]
        cell_w = pw / cols
        cell_h = ph / rows
        margin = 4

        with pikepdf.open(inputs[0]) as src:
            total = len(src.pages)

        per_sheet = cols * rows
        sheet_count = (total + per_sheet - 1) // per_sheet

        import io
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pw, ph))

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            for sheet_idx in range(sheet_count):
                if reporter:
                    reporter(int(sheet_idx / sheet_count * 95), f"Sheet {sheet_idx+1}/{sheet_count}")
                for cell_idx in range(per_sheet):
                    page_idx = sheet_idx * per_sheet + cell_idx
                    if page_idx >= total:
                        break

                    col = cell_idx % cols
                    row = rows - 1 - (cell_idx // cols)

                    img_path = _render_page_to_png(inputs[0], page_idx, tmpdir)
                    if not img_path:
                        continue

                    x = col * cell_w + margin
                    y = row * cell_h + margin
                    w = cell_w - 2 * margin
                    h = cell_h - 2 * margin
                    c.drawImage(ImageReader(str(img_path)), x, y, width=w, height=h, preserveAspectRatio=True)

                c.showPage()

        c.save()
        buf.seek(0)
        output_path.write_bytes(buf.read())

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"original_pages": total, "output_sheets": sheet_count, "layout": params["layout"]},
            log=f"{total} pages arranged in {params['layout']} layout across {sheet_count} sheet(s)",
        )
