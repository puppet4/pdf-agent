"""Metadata info tool - read PDF metadata and statistics."""
from __future__ import annotations

import json
from pathlib import Path

import pikepdf

from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec


class MetadataInfoTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="metadata_info",
            label="PDF 信息",
            category="metadata",
            description="查看 PDF 元数据与统计信息（页数、尺寸、是否加密等）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="json"),
            params=[],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        with pikepdf.open(inputs[0]) as pdf:
            info = {}
            if pdf.docinfo:
                for key, val in pdf.docinfo.items():
                    info[str(key)] = str(val)

            pages_info = []
            for i, page in enumerate(pdf.pages):
                mbox = page.mediabox
                pages_info.append({
                    "index": i + 1,
                    "width": float(mbox[2] - mbox[0]),
                    "height": float(mbox[3] - mbox[1]),
                })

            result_data = {
                "page_count": len(pdf.pages),
                "pdf_version": str(pdf.pdf_version),
                "is_encrypted": pdf.is_encrypted,
                "docinfo": info,
                "pages": pages_info,
            }

        output_path = workdir / "metadata.json"
        output_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2))

        return ToolResult(
            output_files=[output_path],
            meta=result_data,
            log=f"Extracted metadata: {result_data['page_count']} pages",
        )
