"""Image watermark tool - add image watermark to PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class WatermarkImageTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="watermark_image",
            label="图片水印",
            category="edit",
            description="在 PDF 页面上添加图片水印",
            inputs=ToolInputSpec(min=2, max=2, accept=["application/pdf", "image/png", "image/jpeg"]),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="opacity",
                    label="透明度",
                    type="float",
                    default=0.3,
                    min=0.01,
                    max=1.0,
                    description="水印透明度 (0.01-1.0)",
                ),
                ParamSpec(
                    name="scale",
                    label="缩放比例",
                    type="float",
                    default=0.3,
                    min=0.05,
                    max=2.0,
                    description="水印图片相对于页面宽度的缩放比例",
                ),
                ParamSpec(
                    name="position",
                    label="位置",
                    type="enum",
                    options=["center", "top_left", "top_right", "bottom_left", "bottom_right"],
                    default="center",
                    description="水印在页面上的位置",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要添加水印的页面范围",
                ),
            ],
            engine="reportlab+pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {
            "opacity": float(params.get("opacity", 0.3)),
            "scale": float(params.get("scale", 0.3)),
            "position": params.get("position", "center"),
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

        if len(inputs) < 2:
            raise ToolError(ErrorCode.INVALID_INPUT_FILE, "Need PDF as first input and image as second input")

        pdf_path = inputs[0]
        img_path = inputs[1]

        try:
            wm_img = Image.open(img_path)
        except Exception as exc:
            raise ToolError(ErrorCode.INVALID_INPUT_FILE, f"Cannot open watermark image: {exc}")

        if wm_img.mode == "RGBA":
            pass  # keep alpha
        elif wm_img.mode != "RGB":
            wm_img = wm_img.convert("RGBA")

        output_path = workdir / localized_output_name(inputs[0], "已加图片水印")

        with pikepdf.open(pdf_path) as pdf:
            total = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], total))

            for i in range(total):
                if i not in target_pages:
                    continue

                page = pdf.pages[i]
                mbox = page.mediabox
                page_w = float(mbox[2] - mbox[0])
                page_h = float(mbox[3] - mbox[1])

                overlay_buf = _make_image_overlay(
                    wm_img=wm_img,
                    page_w=page_w,
                    page_h=page_h,
                    opacity=params["opacity"],
                    scale=params["scale"],
                    position=params["position"],
                )
                with pikepdf.open(overlay_buf) as wm_pdf:
                    page.add_overlay(wm_pdf.pages[0])

                if reporter:
                    reporter(int((i + 1) / total * 100))

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"watermarked_pages": len(target_pages)},
            log=f"Added image watermark to {len(target_pages)} pages",
        )


_MARGIN = 30  # points from edge


def _make_image_overlay(
    wm_img: Image.Image,
    page_w: float,
    page_h: float,
    opacity: float,
    scale: float,
    position: str,
) -> io.BytesIO:
    # Calculate watermark dimensions
    wm_w = page_w * scale
    aspect = wm_img.height / wm_img.width
    wm_h = wm_w * aspect

    # Calculate position
    if position == "center":
        x = (page_w - wm_w) / 2
        y = (page_h - wm_h) / 2
    elif position == "top_left":
        x, y = _MARGIN, page_h - wm_h - _MARGIN
    elif position == "top_right":
        x, y = page_w - wm_w - _MARGIN, page_h - wm_h - _MARGIN
    elif position == "bottom_left":
        x, y = _MARGIN, _MARGIN
    else:  # bottom_right
        x, y = page_w - wm_w - _MARGIN, _MARGIN

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.saveState()
    c.setFillAlpha(opacity)

    # Convert PIL image to reportlab ImageReader
    img_buf = io.BytesIO()
    save_format = "PNG" if wm_img.mode == "RGBA" else "JPEG"
    wm_img.save(img_buf, format=save_format)
    img_buf.seek(0)
    img_reader = ImageReader(img_buf)

    c.drawImage(img_reader, x, y, width=wm_w, height=wm_h, mask="auto")
    c.restoreState()
    c.showPage()
    c.save()
    buf.seek(0)
    return buf
