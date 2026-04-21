"""Encrypt tool - encrypt a PDF with password protection."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name
from pdf_agent.tools._builtins._utils import to_bool as _to_bool


class EncryptTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="encrypt",
            label="加密 PDF",
            category="security",
            description="使用密码加密 PDF，可设置用户密码、所有者密码和权限",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="user_password",
                    label="用户密码",
                    type="string",
                    default="",
                    description="打开 PDF 所需的密码（留空则无需密码即可打开，但受权限限制）",
                ),
                ParamSpec(
                    name="owner_password",
                    label="所有者密码",
                    type="string",
                    required=True,
                    description="所有者密码，用于修改权限设置",
                ),
                ParamSpec(
                    name="allow_print",
                    label="允许打印",
                    type="bool",
                    default=True,
                    description="是否允许打印",
                ),
                ParamSpec(
                    name="allow_modify",
                    label="允许修改",
                    type="bool",
                    default=False,
                    description="是否允许修改文档内容",
                ),
                ParamSpec(
                    name="allow_extract",
                    label="允许提取",
                    type="bool",
                    default=False,
                    description="是否允许提取文本和图形",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        owner_password = params.get("owner_password", "")
        if not owner_password:
            raise ToolError(ErrorCode.INVALID_PARAMS, "owner_password is required")
        return {
            "user_password": params.get("user_password", ""),
            "owner_password": owner_password,
            "allow_print": _to_bool(params.get("allow_print", True)),
            "allow_modify": _to_bool(params.get("allow_modify", False)),
            "allow_extract": _to_bool(params.get("allow_extract", False)),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已加密")

        permissions = pikepdf.Permissions(
            print_lowres=params["allow_print"],
            print_highres=params["allow_print"],
            modify_form=params["allow_modify"],
            modify_annotation=params["allow_modify"],
            modify_assembly=params["allow_modify"],
            modify_other=params["allow_modify"],
            extract=params["allow_extract"],
            accessibility=True,
        )

        with pikepdf.open(inputs[0]) as pdf:
            pdf.save(
                output_path,
                encryption=pikepdf.Encryption(
                    user=params["user_password"],
                    owner=params["owner_password"],
                    R=6,
                    allow=permissions,
                ),
            )

        return ToolResult(
            output_files=[output_path],
            meta={"has_user_password": bool(params["user_password"])},
            log="PDF encrypted successfully",
        )
