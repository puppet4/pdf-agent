"""Extract embedded file attachments from a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class ExtractAttachmentsTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="extract_attachments",
            label="提取附件",
            category="extract",
            description="提取 PDF embedded files 附件",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="zip"),
            params=[],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        output_files: list[Path] = []
        with pikepdf.open(inputs[0]) as pdf:
            attachments = list(pdf.attachments.items())
            for index, (name, file_spec) in enumerate(attachments, start=1):
                attached_file = file_spec.get_file()
                target = workdir / (Path(name).name or f"attachment_{index}")
                target.write_bytes(attached_file.read_bytes())
                output_files.append(target)
                if reporter:
                    reporter(int(index / max(1, len(attachments)) * 100))
        if not output_files:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "No embedded attachments found in PDF")
        return ToolResult(
            output_files=output_files,
            meta={"attachment_count": len(output_files)},
            log=f"Extracted {len(output_files)} attachment(s)",
        )
