"""PDF validate tool — check PDF for errors and compliance issues using qpdf."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class ValidateTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="validate",
            label="PDF 合规校验",
            category="analysis",
            description="使用 qpdf 检查 PDF 文件是否有错误、损坏或不符合规范的地方",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="json"),
            params=[],
            engine="qpdf",
        )

    def validate(self, params: dict) -> dict:
        return {}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        qpdf = shutil.which("qpdf")
        if not qpdf:
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "qpdf is not installed")

        issues = []
        is_valid = True

        # Run qpdf check
        try:
            result = subprocess.run(
                [qpdf, "--check", str(inputs[0])],
                capture_output=True, timeout=60, text=True,
            )
            stdout = result.stdout + result.stderr
            if result.returncode != 0:
                is_valid = False
            for line in stdout.splitlines():
                line = line.strip()
                if line and not line.startswith("PDF Version") and "checking" not in line.lower():
                    issues.append(line)
        except subprocess.TimeoutExpired:
            raise ToolError(ErrorCode.ENGINE_EXEC_TIMEOUT, "qpdf validation timed out")

        # Also check linearization
        try:
            lin_result = subprocess.run(
                [qpdf, "--check-linearization", str(inputs[0])],
                capture_output=True, timeout=30, text=True,
            )
            is_linearized = lin_result.returncode == 0
        except Exception:
            is_linearized = False

        report = {
            "is_valid": is_valid,
            "is_linearized": is_linearized,
            "issue_count": len(issues),
            "issues": issues,
            "file_size": inputs[0].stat().st_size,
        }

        output_path = workdir / "validation_report.json"
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

        status = "valid" if is_valid else f"{len(issues)} issue(s) found"
        return ToolResult(
            output_files=[output_path],
            meta=report,
            log=f"PDF validation: {status}. Linearized: {is_linearized}. Issues: {issues[:3]}",
        )
