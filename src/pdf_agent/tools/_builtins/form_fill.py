"""Form fill tool — read AcroForm fields and fill values into a PDF."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pikepdf
from pikepdf import Name, String

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


def _get_form_fields(pdf: pikepdf.Pdf) -> dict[str, str]:
    """Extract AcroForm field names and current values."""
    fields: dict[str, str] = {}
    if Name("/AcroForm") not in pdf.Root:
        return fields
    acroform = pdf.Root["/AcroForm"]
    if Name("/Fields") not in acroform:
        return fields
    for field_ref in acroform["/Fields"]:
        try:
            field = field_ref
            name = str(field.get("/T", "")).strip("()")
            value = field.get("/V", "")
            if isinstance(value, String):
                fields[name] = str(value).strip("()")
            else:
                fields[name] = str(value) if value else ""
        except Exception:
            pass
    return fields


def _flatten_pdf(source_path: Path, output_path: Path) -> None:
    from pdf_agent.tools._builtins.flatten import FlattenTool

    result = FlattenTool().run([source_path], {}, output_path.parent)
    flattened_path = result.output_files[0]
    if flattened_path != output_path:
        shutil.move(flattened_path, output_path)


class FormFillTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="form_fill",
            label="填写 PDF 表单",
            category="forms",
            description="读取 PDF AcroForm 字段并填入指定值。如果不提供 field_values，则列出所有可用字段。",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="field_values",
                    label="字段值 (JSON)",
                    type="string",
                    required=False,
                    description='JSON 对象，键为字段名，值为填写内容。例如：{"name": "张三", "date": "2026-03-19"}',
                ),
                ParamSpec(
                    name="flatten",
                    label="拼合表单",
                    type="bool",
                    default=False,
                    description="填写后是否拼合表单字段（拼合后不可再编辑）",
                ),
            ],
            engine="pikepdf",
        )

    def validate(self, params: dict) -> dict:
        field_values = params.get("field_values")
        if field_values:
            try:
                parsed = json.loads(field_values) if isinstance(field_values, str) else field_values
                if not isinstance(parsed, dict):
                    raise ToolError(ErrorCode.INVALID_PARAMS, "field_values must be a JSON object")
                return {"field_values": parsed, "flatten": bool(params.get("flatten", False))}
            except json.JSONDecodeError as e:
                raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid JSON in field_values: {e}")
        return {"field_values": None, "flatten": False}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        workdir.mkdir(parents=True, exist_ok=True)
        output_path = workdir / "filled.pdf"
        intermediate_path = workdir / "filled_intermediate.pdf"

        with pikepdf.open(inputs[0]) as pdf:
            existing_fields = _get_form_fields(pdf)

            if not params["field_values"]:
                # Just list available fields
                return ToolResult(
                    output_files=[],
                    meta={"fields": existing_fields},
                    log=f"PDF has {len(existing_fields)} form field(s): {json.dumps(existing_fields, ensure_ascii=False)}",
                )

            if not existing_fields:
                raise ToolError(ErrorCode.INVALID_PARAMS, "This PDF has no AcroForm fields to fill")

            requested_fields = set(params["field_values"].keys())
            missing_fields = sorted(requested_fields - set(existing_fields.keys()))
            if missing_fields:
                raise ToolError(
                    ErrorCode.INVALID_PARAMS,
                    f"Unknown form field(s): {', '.join(missing_fields)}",
                )

            # Fill fields
            if Name("/AcroForm") in pdf.Root:
                acroform = pdf.Root["/AcroForm"]
                if Name("/Fields") in acroform:
                    for field_ref in acroform["/Fields"]:
                        field = field_ref
                        name = str(field.get("/T", "")).strip("()")
                        if name not in params["field_values"]:
                            continue
                        try:
                            field["/V"] = String(params["field_values"][name])
                            field["/AP"] = pikepdf.Dictionary()  # reset appearance
                        except Exception as exc:
                            raise ToolError(
                                ErrorCode.OUTPUT_GENERATION_FAILED,
                                f"Failed to fill form field '{name}'",
                            ) from exc

                acroform["/NeedAppearances"] = pikepdf.Boolean(False)

            pdf.save(intermediate_path if params["flatten"] else output_path)

        if params["flatten"]:
            _flatten_pdf(intermediate_path, output_path)
            intermediate_path.unlink(missing_ok=True)

        filled = list(params["field_values"].keys())
        return ToolResult(
            output_files=[output_path],
            meta={"filled_fields": filled, "total_fields": len(existing_fields)},
            log=f"Filled {len(filled)} field(s): {', '.join(filled)}",
        )
