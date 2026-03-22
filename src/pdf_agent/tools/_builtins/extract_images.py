"""Extract embedded raster images from a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class ExtractImagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="extract_images",
            label="提取图片",
            category="extract",
            description="提取 PDF 中嵌入的图片资源",
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
            total = len(pdf.pages)
            for page_index, page in enumerate(pdf.pages, start=1):
                for image_index, (_, image_obj) in enumerate(page.images.items(), start=1):
                    pdf_image = pikepdf.PdfImage(image_obj)
                    prefix = workdir / f"page_{page_index:04d}_image_{image_index:03d}"
                    extracted = Path(pdf_image.extract_to(fileprefix=str(prefix)))
                    if not extracted.is_absolute():
                        extracted = Path(f"{prefix}{extracted}")
                    output_files.append(extracted)
                if reporter:
                    reporter(int(page_index / total * 100))
        if not output_files:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "No embedded images found in PDF")
        return ToolResult(
            output_files=output_files,
            meta={"image_count": len(output_files)},
            log=f"Extracted {len(output_files)} embedded image(s)",
        )
