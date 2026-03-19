"""Set Metadata tool — write PDF metadata fields."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class SetMetadataTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="set_metadata",
            label="设置元数据",
            category="metadata",
            description="修改 PDF 的标题、作者、主题、关键词等元数据字段",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="title", label="标题", type="string", required=False, description="文档标题"),
                ParamSpec(name="author", label="作者", type="string", required=False, description="作者姓名"),
                ParamSpec(name="subject", label="主题", type="string", required=False, description="文档主题"),
                ParamSpec(name="keywords", label="关键词", type="string", required=False, description="逗号分隔的关键词"),
                ParamSpec(name="creator", label="创建程序", type="string", required=False, description="创建该文档的程序"),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {k: v for k, v in params.items() if v}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "metadata_updated.pdf"

        field_map = {
            "title": "/Title",
            "author": "/Author",
            "subject": "/Subject",
            "keywords": "/Keywords",
            "creator": "/Creator",
        }

        with pikepdf.open(inputs[0]) as pdf:
            with pdf.open_metadata() as meta:
                for param_key, pdf_key in field_map.items():
                    if param_key in params:
                        meta[pdf_key] = params[param_key]
            pdf.save(output_path)

        updated = [k for k in field_map if k in params]
        return ToolResult(
            output_files=[output_path],
            meta={k: params[k] for k in updated},
            log=f"Updated metadata fields: {', '.join(updated)}",
        )
