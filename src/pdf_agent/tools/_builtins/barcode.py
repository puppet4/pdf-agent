"""Barcode tool — insert a 1D barcode onto PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class BarcodeTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="barcode",
            label="插入条形码",
            category="annotation",
            description="在 PDF 页面指定位置插入条形码（需要 python-barcode 库）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="content", label="条形码内容", type="string", required=True,
                          description="条形码编码的文字或数字"),
                ParamSpec(name="barcode_type", label="类型", type="enum",
                          options=["code128", "ean13", "ean8", "upca", "isbn13"],
                          default="code128", description="条形码格式"),
                ParamSpec(name="position", label="位置", type="enum",
                          options=["top-left", "top-right", "bottom-left", "bottom-right"],
                          default="bottom-right"),
                ParamSpec(name="width_pt", label="宽度(pt)", type="int", default=150, min=60, max=400),
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all"),
            ],
            engine="reportlab+python-barcode",
        )

    def validate(self, params: dict) -> dict:
        if not params.get("content"):
            raise ToolError(ErrorCode.INVALID_PARAMS, "Barcode content cannot be empty")
        return {
            "content": params["content"],
            "barcode_type": params.get("barcode_type", "code128"),
            "position": params.get("position", "bottom-right"),
            "width_pt": max(60, min(400, int(params.get("width_pt", 150)))),
            "page_range": params.get("page_range", "all"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        try:
            import barcode
            from barcode.writer import ImageWriter
        except ImportError:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "python-barcode not installed. Run: pip install python-barcode[images]")

        params = self.validate(params)
        output_path = workdir / "barcode_inserted.pdf"
        margin = 15

        # Generate barcode image
        try:
            bc_class = barcode.get_barcode_class(params["barcode_type"])
            bc = bc_class(params["content"], writer=ImageWriter())
            bc_buf = io.BytesIO()
            bc.write(bc_buf, options={"module_height": 10, "quiet_zone": 2, "text_distance": 3})
            bc_buf.seek(0)
        except Exception as e:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Barcode generation failed: {e}")

        from PIL import Image
        bc_img = Image.open(bc_buf)
        sw = params["width_pt"]
        sh = sw * bc_img.height / bc_img.width

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)

            for idx in pages:
                page = pdf.pages[idx]
                mbox = page.mediabox
                pw = float(mbox[2]) - float(mbox[0])
                ph = float(mbox[3]) - float(mbox[1])

                pos = params["position"]
                x = pw - sw - margin if "right" in pos else margin
                y = margin if "bottom" in pos else ph - sh - margin

                overlay_buf = io.BytesIO()
                c = canvas.Canvas(overlay_buf, pagesize=(pw, ph))
                bc_buf.seek(0)
                c.drawImage(bc_buf, x, y, width=sw, height=sh, mask="auto")
                c.save()
                overlay_buf.seek(0)

                overlay_pdf = pikepdf.Pdf.open(overlay_buf)
                pikepdf.Page(page).add_overlay(overlay_pdf.pages[0])

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"type": params["barcode_type"], "content": params["content"], "pages": len(pages)},
            log=f"Inserted {params['barcode_type']} barcode on {len(pages)} page(s)",
        )
