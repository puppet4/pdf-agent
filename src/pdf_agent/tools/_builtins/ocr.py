"""OCR tool - make scanned PDFs searchable using ocrmypdf."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, ToolError
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
            ],
            engine="ocrmypdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {
            "language": params.get("language", "eng"),
            "skip_text": _to_bool(params.get("skip_text", True)),
            "deskew": _to_bool(params.get("deskew", False)),
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

        cmd = [
            ocrmypdf_bin,
            "--language", params["language"],
            "--output-type", "pdf",
        ]
        if params["skip_text"]:
            cmd.append("--skip-text")
        if params["deskew"]:
            cmd.append("--deskew")
        cmd.extend([str(src_path), str(output_path)])

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=settings.external_cmd_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, "OCR processing timed out")
        except subprocess.CalledProcessError as exc:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"ocrmypdf failed: {exc.stderr.decode(errors='replace')}")

        return ToolResult(
            output_files=[output_path],
            meta={"language": params["language"]},
            log=f"OCR completed with language={params['language']}",
        )


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
