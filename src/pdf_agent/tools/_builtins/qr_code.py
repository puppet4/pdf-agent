"""QR code tool — insert a QR code onto PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class QrCodeTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="qr_code",
            label="插入二维码",
            category="annotation",
            description="在 PDF 页面指定位置插入二维码（需要 qrcode 库）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="content", label="二维码内容", type="string", required=True,
                          description="二维码编码的文字或 URL"),
                ParamSpec(name="position", label="位置", type="enum",
                          options=["top-left", "top-right", "bottom-left", "bottom-right"],
                          default="bottom-right", description="二维码放置位置"),
                ParamSpec(name="size", label="尺寸(pt)", type="int", default=80, min=30, max=200,
                          description="二维码边长（点数）"),
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all",
                          description="要插入二维码的页面范围"),
            ],
            engine="reportlab+qrcode",
        )

    def validate(self, params: dict) -> dict:
        if not params.get("content"):
            raise ToolError(ErrorCode.INVALID_PARAMS, "QR code content cannot be empty")
        return {
            "content": params["content"],
            "position": params.get("position", "bottom-right"),
            "size": int(params.get("size", 80)),
            "page_range": params.get("page_range", "all"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        try:
            import qrcode
        except ImportError:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "qrcode package not installed. Run: pip install qrcode[pil]")

        params = self.validate(params)
        output_path = workdir / "qr_inserted.pdf"
        size = params["size"]
        margin = 15

        # Generate QR code image
        qr = qrcode.QRCode(box_size=3, border=2)
        qr.add_data(params["content"])
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_buf = io.BytesIO()
        qr_img.save(qr_buf, format="PNG")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)

            for idx in pages:
                page = pdf.pages[idx]
                mbox = page.mediabox
                pw = float(mbox[2]) - float(mbox[0])
                ph = float(mbox[3]) - float(mbox[1])

                pos = params["position"]
                x = pw - size - margin if "right" in pos else margin
                y = margin if "bottom" in pos else ph - size - margin

                # Build overlay
                overlay_buf = io.BytesIO()
                c = canvas.Canvas(overlay_buf, pagesize=(pw, ph))
                qr_buf.seek(0)
                c.drawImage(qr_buf, x, y, width=size, height=size, mask="auto")
                c.save()
                overlay_buf.seek(0)

                overlay_pdf = pikepdf.Pdf.open(overlay_buf)
                pikepdf.Page(page).add_overlay(overlay_pdf.pages[0])

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"content": params["content"], "pages": len(pages), "position": params["position"]},
            log=f"Inserted QR code on {len(pages)} page(s) at {params['position']}",
        )
