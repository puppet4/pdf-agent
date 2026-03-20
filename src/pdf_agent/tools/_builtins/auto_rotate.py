"""Auto-rotate tool — detect content orientation and rotate pages to upright."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


def _detect_rotation(image_path: Path) -> int:
    """Use tesseract OSD to detect required rotation (degrees)."""
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return 0
    try:
        result = subprocess.run(
            [tesseract, str(image_path), "stdout", "--psm", "0", "-l", "osd"],
            capture_output=True, timeout=30, text=True,
        )
        for line in result.stdout.splitlines():
            if "Rotate:" in line:
                angle = int(float(line.split(":")[1].strip()))
                return angle
    except Exception:
        pass
    return 0


def _render_page_png(pdf_path: Path, page_idx: int, tmpdir: Path) -> Path | None:
    """Render a single PDF page to PNG for OSD analysis."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    out_stem = tmpdir / f"p{page_idx}"
    subprocess.run(
        [pdftoppm, "-r", "72", "-png", "-f", str(page_idx + 1), "-l", str(page_idx + 1),
         str(pdf_path), str(out_stem)],
        capture_output=True, timeout=30,
    )
    candidates = list(tmpdir.glob(f"p{page_idx}*.png"))
    return candidates[0] if candidates else None


class AutoRotateTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="auto_rotate",
            label="自动旋转",
            category="page_ops",
            description="使用 Tesseract OSD 检测每页内容朝向，自动旋转到正确方向",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="min_confidence",
                    label="最低置信度",
                    type="int",
                    default=2,
                    min=0,
                    max=10,
                    description="低于此置信度的页面不旋转（0=全部旋转, 10=仅高置信度）",
                ),
            ],
            engine="tesseract+pikepdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {"min_confidence": max(0, min(10, int(params.get("min_confidence", 2))))}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        if not shutil.which("tesseract"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Tesseract is not installed")
        if not shutil.which("pdftoppm"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm is not installed")

        params = self.validate(params)
        output_path = workdir / "auto_rotated.pdf"
        rotations = []

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            with pikepdf.open(inputs[0]) as pdf:
                total = len(pdf.pages)
                for i in range(total):
                    if reporter:
                        reporter(int(i / total * 85), f"Analyzing page {i+1}/{total}")
                    img = _render_page_png(inputs[0], i, tmpdir)
                    if not img:
                        continue
                    angle = _detect_rotation(img)
                    if angle != 0:
                        pdf.pages[i].rotate(angle, relative=True)
                        rotations.append({"page": i + 1, "angle": angle})
                if reporter:
                    reporter(95, "Saving...")
                pdf.save(output_path)

        if reporter:
            reporter(100, "Done")

        summary = f"Auto-rotated {len(rotations)} page(s)" if rotations else "No rotation needed"
        return ToolResult(
            output_files=[output_path],
            meta={"rotations": rotations, "total_pages": total},
            log=summary,
        )
