"""Auto-deskew tool — detect and correct skewed pages using Tesseract OSD."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


def _detect_skew(image_path: Path) -> float | None:
    """Use tesseract OSD to detect page skew angle (degrees)."""
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return None
    try:
        result = subprocess.run(
            [tesseract, str(image_path), "stdout", "--psm", "0", "-l", "osd"],
            capture_output=True, timeout=30, text=True,
        )
        for line in result.stdout.splitlines():
            if "Rotate:" in line:
                angle = float(line.split(":")[1].strip())
                return angle
    except Exception:
        pass
    return None


def _render_page_to_image(pdf_path: Path, page_idx: int, tmpdir: Path, dpi: int = 150) -> Path | None:
    """Render a single PDF page to PNG using pdftoppm."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    out_stem = tmpdir / f"page_{page_idx}"
    subprocess.run(
        [pdftoppm, "-r", str(dpi), "-png", "-f", str(page_idx + 1), "-l", str(page_idx + 1),
         str(pdf_path), str(out_stem)],
        capture_output=True, timeout=30,
    )
    candidates = list(tmpdir.glob(f"page_{page_idx}*.png"))
    return candidates[0] if candidates else None


class DeskewTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="deskew",
            label="自动纠偏",
            category="page_ops",
            description="检测并自动校正 PDF 页面的倾斜方向（需要 Tesseract 和 poppler）",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要纠偏的页面范围",
                ),
                ParamSpec(
                    name="min_angle",
                    label="最小校正角度(度)",
                    type="float",
                    default=0.5,
                    min=0.1,
                    max=45.0,
                    description="小于该角度的偏斜忽略不处理",
                ),
            ],
            engine="tesseract+pikepdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {
            "page_range": params.get("page_range", "all"),
            "min_angle": max(0.1, float(params.get("min_angle", 0.5))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        if not shutil.which("tesseract"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Tesseract is not installed")
        if not shutil.which("pdftoppm"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm (poppler-utils) is not installed")

        params = self.validate(params)
        output_path = workdir / "deskewed.pdf"
        corrections: list[dict] = []

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)

            with pikepdf.open(inputs[0]) as pdf:
                total = len(pdf.pages)
                target_pages = set(parse_page_range(params["page_range"], total))

                for i in range(total):
                    if i not in target_pages:
                        continue
                    if reporter:
                        reporter(int(i / total * 85), f"Analyzing page {i+1}/{total}")

                    img_path = _render_page_to_image(inputs[0], i, tmpdir)
                    if not img_path:
                        continue

                    angle = _detect_skew(img_path)
                    if angle is None or abs(angle) < params["min_angle"]:
                        continue

                    # Apply rotation to PDF page
                    pdf.pages[i].rotate(int(round(angle)), relative=True)
                    corrections.append({"page": i + 1, "angle": angle})

                if reporter:
                    reporter(95, "Saving...")
                pdf.save(output_path)

        if reporter:
            reporter(100, "Done")

        summary = f"Corrected {len(corrections)} page(s)" if corrections else "No significant skew detected"
        return ToolResult(
            output_files=[output_path],
            meta={"corrections": corrections, "pages_checked": len(target_pages)},
            log=f"{summary}. {corrections}",
        )
