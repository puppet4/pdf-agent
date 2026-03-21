"""OCR tool - make scanned PDFs searchable using ocrmypdf."""
from __future__ import annotations

import shutil
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class OcrTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="ocr",
            label="OCR 识别",
            category="convert",
            description="对扫描版 PDF 进行 OCR，生成可搜索文本层",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="language",
                    label="语言",
                    type="string",
                    default="eng",
                    description="OCR 语言代码，如 eng, chi_sim, chi_sim+eng",
                ),
                ParamSpec(
                    name="skip_text",
                    label="跳过已有文本",
                    type="bool",
                    default=True,
                    description="是否跳过已包含文本层的页面",
                ),
                ParamSpec(
                    name="deskew",
                    label="自动纠偏",
                    type="bool",
                    default=False,
                    description="是否自动校正页面倾斜",
                ),
                ParamSpec(
                    name="page_range",
                    label="OCR 页范围",
                    type="page_range",
                    default="all",
                    description="仅对指定页做 OCR，all 表示全部",
                ),
                ParamSpec(
                    name="output_mode",
                    label="输出模式",
                    type="enum",
                    options=["pdf", "txt", "json"],
                    default="pdf",
                    description="pdf=可搜索 PDF, txt=旁路文本, json=结构化文本摘要",
                ),
            ],
            engine="ocrmypdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {
            "language": params.get("language", "eng"),
            "skip_text": _to_bool(params.get("skip_text", True)),
            "deskew": _to_bool(params.get("deskew", False)),
            "page_range": params.get("page_range", "all"),
            "output_mode": params.get("output_mode", "pdf"),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)

        ocrmypdf_bin = shutil.which("ocrmypdf")
        if not ocrmypdf_bin:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "ocrmypdf is not installed")

        output_path = workdir / "ocr_output.pdf"
        src_path = inputs[0]
        sidecar_path = workdir / "ocr_output.txt"

        cmd = [
            ocrmypdf_bin,
            "--language", params["language"],
            "--output-type", "pdf",
            "--sidecar", str(sidecar_path),
        ]
        if params["skip_text"]:
            cmd.append("--skip-text")
        if params["deskew"]:
            cmd.append("--deskew")
        if params["page_range"] and params["page_range"] != "all":
            cmd.extend(["--pages", params["page_range"]])
        cmd.extend([str(src_path), str(output_path)])

        run_command(cmd, timeout=settings.external_cmd_timeout_sec)

        output_files = [output_path]
        if params["output_mode"] == "txt":
            output_files = [sidecar_path]
        elif params["output_mode"] == "json":
            json_path = workdir / "ocr_output.json"
            json_path.write_text(
                __import__("json").dumps(
                    {
                        "language": params["language"],
                        "page_range": params["page_range"],
                        "text": sidecar_path.read_text(encoding="utf-8") if sidecar_path.exists() else "",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            output_files = [json_path]

        return ToolResult(
            output_files=output_files,
            meta={"language": params["language"], "page_range": params["page_range"], "output_mode": params["output_mode"]},
            log=f"OCR completed with language={params['language']} output={params['output_mode']}",
        )


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
