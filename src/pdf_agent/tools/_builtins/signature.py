"""Digital signature tool — visible stamp and optional PKCS#12 cryptographic signing."""
from __future__ import annotations

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
            description="支持可见签名图片叠加，以及使用 P12/X.509 进行加密数字签名。",
            inputs=ToolInputSpec(
                min=1,
                max=3,
                accept=["application/pdf", "image/png", "image/jpeg", "application/x-pkcs12"],
            ),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="mode", label="签名模式", type="enum", options=["visible", "digital"], default="visible"),
                ParamSpec(name="page", label="签名页码", type="int", default=1, min=1,
                          description="放置签名的页码（1-based）"),
                ParamSpec(name="position", label="位置", type="enum",
                          options=["bottom-right", "bottom-left", "top-right", "top-left", "center"],
                          default="bottom-right"),
                ParamSpec(name="width_pt", label="签名宽度(pt)", type="int", default=120, min=40, max=300,
                          description="签名图片显示宽度（点数）"),
                ParamSpec(name="opacity", label="透明度", type="float", default=0.85, min=0.1, max=1.0),
                ParamSpec(name="p12_password", label="P12 密码", type="string", default=""),
                ParamSpec(name="field_name", label="签名域名", type="string", default="Signature1"),
                ParamSpec(name="reason", label="签名原因", type="string", default=""),
                ParamSpec(name="location", label="签名地点", type="string", default=""),
            ],
            engine="pikepdf+reportlab+pyhanko",
        )

    def validate(self, params: dict) -> dict:
        return {
            "mode": params.get("mode", "visible"),
            "page": max(1, int(params.get("page", 1))),
            "position": params.get("position", "bottom-right"),
            "width_pt": max(40, min(300, int(params.get("width_pt", 120)))),
            "opacity": max(0.1, min(1.0, float(params.get("opacity", 0.85)))),
            "p12_password": str(params.get("p12_password", "")),
            "field_name": str(params.get("field_name", "Signature1")),
            "reason": str(params.get("reason", "")),
            "location": str(params.get("location", "")),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "signed.pdf"

        # Identify PDF and signature image
        pdf_path = sig_path = cert_path = None
        for p in inputs:
            if p.suffix.lower() == ".pdf":
                pdf_path = p
            elif p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                sig_path = p
            elif p.suffix.lower() in (".p12", ".pfx"):
                cert_path = p
        if not pdf_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "No PDF file provided")

        if params["mode"] == "digital" or cert_path is not None:
            pre_signed_pdf = pdf_path
            if sig_path is not None:
                pre_signed_pdf = workdir / "visible_signature_stage.pdf"
                self._apply_visible_signature(pdf_path, sig_path, pre_signed_pdf, params)
            if cert_path is None:
                raise ToolError(ErrorCode.INVALID_PARAMS, "Digital signature requires a .p12/.pfx certificate")
            self._apply_digital_signature(pre_signed_pdf, cert_path, output_path, params)
            return ToolResult(
                output_files=[output_path],
                meta={"mode": "digital", "field_name": params["field_name"]},
                log="Applied digital signature using PKCS#12 certificate",
            )

        if not sig_path:
            raise ToolError(ErrorCode.INVALID_PARAMS, "Visible signature requires an image input")
        self._apply_visible_signature(pdf_path, sig_path, output_path, params)
        return ToolResult(
            output_files=[output_path],
            meta={"page": params["page"], "position": params["position"], "mode": "visible"},
            log=f"Added signature to page {params['page']} at {params['position']}",
        )

    def _apply_visible_signature(self, pdf_path: Path, sig_path: Path, output_path: Path, params: dict) -> None:
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

    def _apply_digital_signature(self, input_pdf: Path, cert_path: Path, output_path: Path, params: dict) -> None:
        try:
            from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
            from pyhanko.sign import signers
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pyhanko is required for digital signatures") from exc

        password = params["p12_password"].encode("utf-8") if params["p12_password"] else None
        signer = signers.SimpleSigner.load_pkcs12(cert_path, passphrase=password)
        meta = signers.PdfSignatureMetadata(
            field_name=params["field_name"],
            reason=params["reason"] or None,
            location=params["location"] or None,
        )
        pdf_signer = signers.PdfSigner(meta, signer=signer)
        with input_pdf.open("rb") as input_stream, output_path.open("wb") as output_stream:
            writer = IncrementalPdfFileWriter(input_stream)
            pdf_signer.sign_pdf(writer, output=output_stream)
