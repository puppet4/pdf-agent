"""Page border tool — add a decorative border/background to PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class PageBorderTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="page_border",
            label="添加页面边框",
            category="annotation",
            description="在 PDF 页面周围添加装饰性边框或背景色",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="border_width", label="边框宽度(pt)", type="int", default=3, min=1, max=20),
                ParamSpec(name="border_color", label="边框颜色(hex)", type="string", default="#000000",
                          description="十六进制颜色，如 #FF0000 表示红色"),
                ParamSpec(name="margin", label="边距(pt)", type="int", default=10, min=0, max=50,
                          description="边框距页面边缘的距离"),
                ParamSpec(name="bg_color", label="背景色(hex)", type="string", default="",
                          description="页面背景色，留空则不填充背景"),
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all"),
            ],
            engine="pikepdf+reportlab",
        )

    def validate(self, params: dict) -> dict:
        def valid_hex(h: str) -> bool:
            if not h:
                return True
            h = h.lstrip("#")
            return len(h) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in h)

        border_color = params.get("border_color", "#000000") or "#000000"
        bg_color = params.get("bg_color", "") or ""
        if not valid_hex(border_color):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid border_color: {border_color}")
        if bg_color and not valid_hex(bg_color):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid bg_color: {bg_color}")
        return {
            "border_width": max(1, min(20, int(params.get("border_width", 3)))),
            "border_color": border_color if border_color.startswith("#") else "#" + border_color,
            "margin": max(0, min(50, int(params.get("margin", 10)))),
            "bg_color": ("#" + bg_color.lstrip("#")) if bg_color else "",
            "page_range": params.get("page_range", "all"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "bordered.pdf"

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)

            for idx in pages:
                page = pdf.pages[idx]
                mbox = page.mediabox
                pw = float(mbox[2]) - float(mbox[0])
                ph = float(mbox[3]) - float(mbox[1])

                overlay_buf = io.BytesIO()
                c = canvas.Canvas(overlay_buf, pagesize=(pw, ph))

                m = params["margin"]
                bw = params["border_width"]

                # Background
                if params["bg_color"]:
                    c.setFillColor(HexColor(params["bg_color"]))
                    c.rect(0, 0, pw, ph, fill=1, stroke=0)

                # Border rectangle
                c.setStrokeColor(HexColor(params["border_color"]))
                c.setLineWidth(bw)
                c.rect(m + bw/2, m + bw/2, pw - 2*m - bw, ph - 2*m - bw, fill=0, stroke=1)

                c.save()
                overlay_buf.seek(0)

                overlay_pdf = pikepdf.Pdf.open(overlay_buf)
                pikepdf.Page(page).add_overlay(overlay_pdf.pages[0])

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"pages": len(pages), "border_color": params["border_color"], "border_width": params["border_width"]},
            log=f"Added border to {len(pages)} page(s)",
        )
