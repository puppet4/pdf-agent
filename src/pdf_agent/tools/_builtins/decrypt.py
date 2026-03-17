"""Decrypt tool - remove password protection from a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class DecryptTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="decrypt",
            label="解密 PDF",
            category="security",
            description="使用密码解密 PDF，移除密码保护",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="password",
                    label="密码",
                    type="string",
                    required=True,
                    description="PDF 的用户密码或所有者密码",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        password = params.get("password", "")
        if not password:
            raise ToolError(ErrorCode.INVALID_PARAMS, "password is required")
        return {"password": password}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "decrypted.pdf"

        try:
            with pikepdf.open(inputs[0], password=params["password"]) as pdf:
                pdf.save(output_path)
        except pikepdf.PasswordError:
            raise ToolError(ErrorCode.INVALID_PARAMS, "Incorrect password")

        return ToolResult(
            output_files=[output_path],
            meta={},
            log="PDF decrypted successfully",
        )
