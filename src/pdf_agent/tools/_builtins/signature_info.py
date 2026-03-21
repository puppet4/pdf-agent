"""Signature info tool — detect and verify digital signatures in a PDF when possible."""
from __future__ import annotations

import json
from pathlib import Path

import pikepdf
from pikepdf import Name

from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class SignatureInfoTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="signature_info",
            label="数字签名信息",
            category="analysis",
            description="检测 PDF 中的数字签名字段，报告签名者、日期等基本信息",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="json"),
            params=[],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        signatures: list[dict] = []

        with pikepdf.open(inputs[0]) as pdf:
            # Check AcroForm for signature fields
            if Name("/AcroForm") in pdf.Root:
                acroform = pdf.Root["/AcroForm"]
                fields = acroform.get("/Fields", [])
                for field_ref in fields:
                    try:
                        field = field_ref
                        ft = str(field.get("/FT", ""))
                        if ft == "/Sig":
                            sig_info: dict = {"field": str(field.get("/T", "unknown")).strip("()")}
                            value = field.get("/V")
                            if value and hasattr(value, "items"):
                                for k, v in value.items():
                                    key_str = str(k).lstrip("/")
                                    try:
                                        sig_info[key_str] = str(v).strip("()")
                                    except Exception:
                                        pass
                            signatures.append(sig_info)
                    except Exception:
                        pass

        verification = _verify_signatures(inputs[0])
        has_signatures = len(signatures) > 0
        summary = f"Found {len(signatures)} digital signature(s)" if has_signatures else "No digital signatures found"

        return ToolResult(
            output_files=[],
            meta={
                "has_signatures": has_signatures,
                "signature_count": len(signatures),
                "signatures": signatures,
                "verification": verification,
            },
            log=f"{summary}. Details: {json.dumps({'signatures': signatures, 'verification': verification}, ensure_ascii=False, default=str)}",
        )


def _verify_signatures(pdf_path: Path) -> list[dict]:
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import ValidationContext, validate_pdf_signature
    except Exception:
        return []

    results: list[dict] = []
    try:
        with pdf_path.open("rb") as fh:
            reader = PdfFileReader(fh)
            for embedded_sig in getattr(reader, "embedded_signatures", []):
                status = validate_pdf_signature(embedded_sig, ValidationContext(allow_fetching=False))
                results.append(
                    {
                        "field_name": getattr(embedded_sig, "field_name", ""),
                        "intact": bool(getattr(status, "intact", False)),
                        "trusted": bool(getattr(status, "trusted", False)),
                        "bottom_line": str(getattr(status, "bottom_line", "")),
                    }
                )
    except Exception:
        return []
    return results
