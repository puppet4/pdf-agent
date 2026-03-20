"""Digital signature tool — add a visible signature image to a PDF using pikepdf."""
from __future__ import annotations

# NOTE: Adding cryptographic digital signatures (X.509/PKCS#7) requires
# pyhanko or endesive. This tool adds a visual signature annotation
# without cryptographic validation. For cryptographic signing, install
# pyhanko and configure a certificate.
import io
from pathlib import Path

import pikepdf
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class SignatureTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="signature",
            label="添加签名",
            category="annotation",
            description="在 PDF 指定页面添加可见签名图片（PNG/JPG）。如需加密数字签名，请使用 pyhanko 集成。",
            inputs=ToolInputSpec(min=2, max=2, accept=["application/pdf", "image/png", "image/jpeg"]),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="page", label="签名页码", type="int", default=1, min=1,
                          description="放置签名的页码（1-based）"),
                ParamSpec(name="position", label="位置", type="enum",
                          options=["bottom-right", "bottom-left", "top-right", "top-left", "center"],
                          default="bottom-right"),
                ParamSpec(name="width_pt", label="签名宽度(pt)", type="int", default=120, min=40, max=300,
                          description="签名图片显示宽度（点数）"),
                ParamSpec(name="opacity", label="透明度", type="float", default=0.85, min=0.1, max=1.0),
            ],
            engine="pikepdf+reportlab",
        )

    def validate(self, params: dict) -> dict:
        return {
            "page": max(1, int(params.get("page", 1))),
            "position": params.get("position", "bottom-right"),
            "width_pt": max(40, min(300, int(params.get("width_pt", 120)))),
            "opacity": max(0.1, min(1.0, float(params.get("opacity", 0.85)))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "signed.pdf"

        # Identify PDF and signature image
        pdf_path = sig_path = None
        for p in inputs:
            if p.suffix.lower() == ".pdf":
                pdf_path = p
            elif p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                sig_path = p
        if not pdf_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "No PDF file provided")
        if not sig_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "No signature image provided")

        with pikepdf.open(pdf_path) as pdf:
            total = len(pdf.pages)
            page_idx = min(params["page"] - 1, total - 1)
            page = pdf.pages[page_idx]
            mbox = page.mediabox
            pw = float(mbox[2]) - float(mbox[0])
            ph = float(mbox[3]) - float(mbox[1])

            # Load signature with opacity
            sig_img = Image.open(sig_path).convert("RGBA")
            r, g, b, a = sig_img.split()
            a = a.point(lambda x: int(x * params["opacity"]))
            sig_img = Image.merge("RGBA", (r, g, b, a))
            sig_buf = io.BytesIO()
            sig_img.save(sig_buf, format="PNG")

            sw = params["width_pt"]
            sh = sw * sig_img.height / sig_img.width
            margin = 15
            pos = params["position"]
            x = pw - sw - margin if "right" in pos else margin
            y = margin if "bottom" in pos else ph - sh - margin
            if pos == "center":
                x, y = (pw - sw) / 2, (ph - sh) / 2

            overlay_buf = io.BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(pw, ph))
            sig_buf.seek(0)
            c.drawImage(sig_buf, x, y, width=sw, height=sh, mask="auto")
            c.save()
            overlay_buf.seek(0)

            overlay = pikepdf.Pdf.open(overlay_buf)
            pikepdf.Page(page).add_overlay(overlay.pages[0])
            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"page": params["page"], "position": params["position"], "size": f"{sw:.0f}x{sh:.0f}pt"},
            log=f"Added signature to page {params['page']} at {params['position']}",
        )
