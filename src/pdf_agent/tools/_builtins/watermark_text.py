"""Watermark text tool - add text watermark to PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name

# Register CJK font for Chinese watermark support
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


class WatermarkTextTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="watermark_text",
            label="文字水印",
            category="edit",
            description="在 PDF 页面上添加文字水印，支持中英文",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="text",
                    label="水印文字",
                    type="string",
                    required=True,
                    description="水印内容",
                ),
                ParamSpec(
                    name="font_size",
                    label="字号",
                    type="int",
                    default=48,
                    min=8,
                    max=200,
                    description="水印字号",
                ),
                ParamSpec(
                    name="rotation",
                    label="旋转角度",
                    type="int",
                    default=45,
                    min=0,
                    max=360,
                    description="水印旋转角度",
                ),
                ParamSpec(
                    name="opacity",
                    label="透明度",
                    type="float",
                    default=0.15,
                    min=0.01,
                    max=1.0,
                    description="水印透明度 (0.01-1.0)",
                ),
                ParamSpec(
                    name="color",
                    label="颜色",
                    type="enum",
                    options=["gray", "red", "blue", "black"],
                    default="gray",
                    description="水印颜色",
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
        text = params.get("text", "")
        if not text:
            raise ToolError(ErrorCode.INVALID_PARAMS, "text is required")
        return {
            "text": text,
            "font_size": int(params.get("font_size", 48)),
            "rotation": int(params.get("rotation", 45)),
            "opacity": float(params.get("opacity", 0.15)),
            "color": params.get("color", "gray"),
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
        output_path = workdir / localized_output_name(inputs[0], "已加文字水印")

        color_map = {
            "gray": (0.5, 0.5, 0.5),
            "red": (0.8, 0.0, 0.0),
            "blue": (0.0, 0.0, 0.8),
            "black": (0.0, 0.0, 0.0),
        }
        rgb = color_map.get(params["color"], (0.5, 0.5, 0.5))

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], total))

            for i in range(total):
                if i not in target_pages:
                    continue
                page = pdf.pages[i]
                mbox = page.mediabox
                page_w = float(mbox[2] - mbox[0])
                page_h = float(mbox[3] - mbox[1])

                overlay_pdf = _make_text_overlay(
                    text=params["text"],
                    page_w=page_w,
                    page_h=page_h,
                    font_size=params["font_size"],
                    rotation=params["rotation"],
                    opacity=params["opacity"],
                    rgb=rgb,
                )
                with pikepdf.open(overlay_pdf) as wm:
                    page.add_overlay(wm.pages[0])

                if reporter:
                    reporter(int((i + 1) / total * 100))

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"watermarked_pages": len(target_pages)},
            log=f"Added text watermark to {len(target_pages)} pages",
        )


def _make_text_overlay(
    text: str,
    page_w: float,
    page_h: float,
    font_size: int,
    rotation: int,
    opacity: float,
    rgb: tuple[float, float, float],
) -> io.BytesIO:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.saveState()
    c.setFillColorRGB(*rgb)
    c.setFillAlpha(opacity)

    # Detect CJK characters to choose font
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    font_name = "STSong-Light" if has_cjk else "Helvetica"
    c.setFont(font_name, font_size)

    # Place watermark text centered on page, rotated
    c.translate(page_w / 2, page_h / 2)
    c.rotate(rotation)
    c.drawCentredString(0, 0, text)
    c.restoreState()
    c.showPage()
    c.save()
    buf.seek(0)
    return buf
