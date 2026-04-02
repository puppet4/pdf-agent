"""Auto-rotate tool — detect content orientation and rotate pages to upright."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pikepdf

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


OSD_RENDER_DPI = 200
DEFAULT_MIN_CONFIDENCE = 10


def _is_low_text_osd_error(exc: ToolError) -> bool:
    detail = (exc.message or "").lower()
    return exc.code == ErrorCode.ENGINE_EXEC_FAILED and "too few characters" in detail


def _detect_rotation(image_path: Path) -> tuple[int, float | None]:
    """Use tesseract OSD to detect required rotation (degrees, confidence)."""
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return 0, None
    try:
        result = run_command(
            [tesseract, str(image_path), "stdout", "--psm", "0", "-l", "osd"],
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip() or "Tesseract OSD failed"
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, detail)
        angle = 0
        confidence = None
        stdout = result.stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if "Rotate:" in line:
                angle = int(float(line.split(":")[1].strip()))
            elif "Orientation confidence:" in line:
                confidence = float(line.split(":", 1)[1].strip())
        return angle, confidence
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, f"Failed to detect orientation: {exc}") from exc


def _render_page_png(pdf_path: Path, page_idx: int, tmpdir: Path) -> Path | None:
    """Render a single PDF page to PNG for OSD analysis."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    out_stem = tmpdir / f"p{page_idx}"
    result = run_command(
        [pdftoppm, "-r", str(OSD_RENDER_DPI), "-png", "-f", str(page_idx + 1), "-l", str(page_idx + 1),
         str(pdf_path), str(out_stem)],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="ignore").strip() or "pdftoppm failed"
        raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, detail)
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
                    default=DEFAULT_MIN_CONFIDENCE,
                    min=0,
                    description="低于此置信度的页面不旋转。默认更保守，避免误转正常页面。",
                ),
            ],
            engine="tesseract+pikepdf",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {"min_confidence": max(0, int(params.get("min_confidence", DEFAULT_MIN_CONFIDENCE)))}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        if not shutil.which("tesseract"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "Tesseract is not installed")
        if not shutil.which("pdftoppm"):
            raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftoppm is not installed")

        params = self.validate(params)
        workdir.mkdir(parents=True, exist_ok=True)
        output_path = workdir / localized_output_name(inputs[0], "已自动旋转")
        rotations = []

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            with pikepdf.open(inputs[0]) as pdf:
                total = len(pdf.pages)
                skipped_pages = []
                for i in range(total):
                    if reporter:
                        reporter(int(i / total * 85), f"Analyzing page {i+1}/{total}")
                    img = _render_page_png(inputs[0], i, tmpdir)
                    if not img:
                        raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, f"Failed to render page {i + 1} for analysis")
                    try:
                        angle, confidence = _detect_rotation(img)
                    except ToolError as exc:
                        if _is_low_text_osd_error(exc):
                            skipped_pages.append(i + 1)
                            continue
                        raise
                    if confidence is None or confidence < params["min_confidence"]:
                        continue
                    if angle != 0:
                        pdf.pages[i].rotate(angle, relative=True)
                        rotations.append({"page": i + 1, "angle": angle, "confidence": confidence})
                if reporter:
                    reporter(95, "Saving...")
                pdf.save(output_path)

        if reporter:
            reporter(100, "Done")

        summary = f"Auto-rotated {len(rotations)} page(s)" if rotations else "No rotation needed"
        return ToolResult(
            output_files=[output_path],
            meta={"rotations": rotations, "total_pages": total, "skipped_pages": skipped_pages},
            log=summary,
        )
