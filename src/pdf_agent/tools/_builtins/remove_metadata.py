"""移除 PDF 中的全部元数据，以提升隐私性。"""
from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Name

from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class RemoveMetadataTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="remove_metadata",
            label="清除元数据",
            category="metadata",
            description="删除 PDF 中的所有元数据（作者、标题、创建时间等），保护隐私",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        output_path = workdir / localized_output_name(inputs[0], "已移除元数据")
        removed_fields: list[str] = []

        with pikepdf.open(inputs[0]) as pdf:
            # 清空文档信息字典
            if pdf.docinfo:
                removed_fields = [str(k) for k in pdf.docinfo.keys()]
                for key in list(pdf.docinfo.keys()):
                    del pdf.docinfo[key]

            # 删除 XMP 元数据流
            if Name("/Metadata") in pdf.Root:
                del pdf.Root["/Metadata"]

            # 从 trailer 中移除创建/修改时间字段
            for date_key in ["/CreationDate", "/ModDate"]:
                if date_key in pdf.trailer:
                    del pdf.trailer[date_key]

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"removed_fields": removed_fields},
            log=f"Removed {len(removed_fields)} metadata field(s): {', '.join(removed_fields) or 'none'}",
        )
