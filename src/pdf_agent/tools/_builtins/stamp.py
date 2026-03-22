"""Stamp tool — add a stamp/seal image to PDF pages."""
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


class StampTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="stamp",
            label="图章/盖章",
            category="annotation",
            description="在 PDF 页面指定位置添加图章图片（PNG/JPG）",
            inputs=ToolInputSpec(min=2, max=2, accept=["application/pdf", "image/png", "image/jpeg"]),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要添加图章的页面范围",
                ),
                ParamSpec(
                    name="position",
                    label="位置",
                    type="enum",
                    options=["center", "top-left", "top-right", "bottom-left", "bottom-right"],
                    default="bottom-right",
                    description="图章放置位置",
                ),
                ParamSpec(
                    name="opacity",
                    label="透明度",
                    type="float",
                    default=0.7,
                    min=0.1,
                    max=1.0,
                    description="图章透明度（0.1=最透明, 1.0=不透明）",
                ),
                ParamSpec(
                    name="scale",
                    label="缩放比例",
                    type="float",
                    default=0.15,
                    min=0.05,
                    max=0.5,
                    description="图章相对页面宽度的比例",
                ),
            ],
            engine="pikepdf+reportlab",
        )

    def validate(self, params: dict) -> dict:
        return {
            "page_range": params.get("page_range", "all"),
            "position": params.get("position", "bottom-right"),
            "opacity": max(0.1, min(1.0, float(params.get("opacity", 0.7)))),
            "scale": max(0.05, min(0.5, float(params.get("scale", 0.15)))),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)

        # Identify PDF and image inputs
        pdf_path = img_path = None
        for p in inputs:
            suffix = p.suffix.lower()
            if suffix == ".pdf":
                pdf_path = p
            elif suffix in (".png", ".jpg", ".jpeg"):
                img_path = p

        if not pdf_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "No PDF file provided")
        if not img_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "No stamp image provided (PNG or JPG)")

        output_path = workdir / localized_output_name(pdf_path, "已盖章")

        with pikepdf.open(pdf_path) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)

            # Load stamp image and apply opacity
            stamp_img = Image.open(img_path).convert("RGBA")
            r, g, b, a = stamp_img.split()
            a = a.point(lambda x: int(x * params["opacity"]))
            stamp_img = Image.merge("RGBA", (r, g, b, a))

            for idx in pages:
                page = pdf.pages[idx]
                # Get page dimensions
                mediabox = page.mediabox
                pw = float(mediabox[2]) - float(mediabox[0])
                ph = float(mediabox[3]) - float(mediabox[1])

                # Compute stamp dimensions
                sw = pw * params["scale"]
                sh = sw * stamp_img.height / stamp_img.width

                # Compute position
                margin = 20
                pos = params["position"]
                if pos == "center":
                    x, y = (pw - sw) / 2, (ph - sh) / 2
                elif pos == "top-left":
                    x, y = margin, ph - sh - margin
                elif pos == "top-right":
                    x, y = pw - sw - margin, ph - sh - margin
                elif pos == "bottom-left":
                    x, y = margin, margin
                else:  # bottom-right
                    x, y = pw - sw - margin, margin

                # Build overlay PDF
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(pw, ph))
                # Save stamp image to temp bytes
                img_buf = io.BytesIO()
                stamp_img.save(img_buf, format="PNG")
                img_buf.seek(0)
                c.drawImage(
                    ImageReader(img_buf), x, y, width=sw, height=sh, mask="auto"
                )
                c.save()
                buf.seek(0)

                # Merge overlay onto page
                overlay_pdf = pikepdf.Pdf.open(buf)
                overlay_page = overlay_pdf.pages[0]
                pikepdf.Page(page).add_overlay(overlay_page)

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"stamped_pages": len(pages), "position": params["position"]},
            log=f"Added stamp to {len(pages)} page(s) at {params['position']}",
        )
