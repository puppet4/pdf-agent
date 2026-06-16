"""自动检测页面内容朝向，并将其旋转为正向。"""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

import pikepdf
from PIL import Image

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


OSD_RENDER_DPI = 200
DEFAULT_MIN_CONFIDENCE = 10
FALLBACK_OCR_PSM = "6"
FALLBACK_OCR_MIN_ALNUM_CHARS = 12
RIGHT_ANGLES = (0, 90, 180, 270)


def _is_low_text_osd_error(exc: ToolError) -> bool:
    detail = (exc.message or "").lower()
    return exc.code == ErrorCode.ENGINE_EXEC_FAILED and "too few characters" in detail


def _detect_rotation(image_path: Path) -> tuple[int, float | None]:
    """使用 tesseract OSD 检测页面需要旋转的角度及置信度。"""
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


def _nearest_right_angle(angle: float) -> int:
    normalized = angle % 360
    return min(RIGHT_ANGLES, key=lambda candidate: abs(((normalized - candidate + 180) % 360) - 180))


def _iter_pdf_chars(node, lt_char_type, lt_container_type):
    if isinstance(node, lt_char_type):
        yield node
        return
    if isinstance(node, lt_container_type):
        for child in node:
            yield from _iter_pdf_chars(child, lt_char_type, lt_container_type)


def _detect_rotation_from_pdf_text(pdf_path: Path, page_idx: int) -> tuple[int, float | None]:
    """当 OSD 无法识别稀疏页面时，回退到 PDF 文本层朝向判断。"""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTChar, LTContainer
    except ImportError:
        return 0, None

    angle_counts = {angle: 0 for angle in RIGHT_ANGLES}

    try:
        for page_layout in extract_pages(str(pdf_path), page_numbers=[page_idx]):
            for char in _iter_pdf_chars(page_layout, LTChar, LTContainer):
                text = char.get_text()
                alnum_chars = sum(1 for ch in text if ch.isalnum())
                if alnum_chars == 0:
                    continue
                a, b, _c, _d, _e, _f = char.matrix
                angle = _nearest_right_angle(math.degrees(math.atan2(b, a)))
                angle_counts[angle] += alnum_chars
    except Exception:
        return 0, None

    dominant_angle, dominant_count = max(angle_counts.items(), key=lambda item: item[1])
    if dominant_count == 0:
        return 0, None
    return dominant_angle, float(dominant_count)


def _ocr_rotation_score(image_path: Path, angle: int) -> tuple[float, int] | None:
    """当 OSD 不可靠时，用常规 OCR 为候选旋转角打分。"""
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return None

    with tempfile.TemporaryDirectory() as td:
        rotated_path = Path(td) / "rotated.png"
        with Image.open(image_path) as opened_image:
            rotated = opened_image.rotate(-angle, expand=True, fillcolor="white")
            rotated.save(rotated_path)

        result = run_command(
            [tesseract, str(rotated_path), "stdout", "--psm", FALLBACK_OCR_PSM, "tsv"],
            check=False,
            timeout=30,
        )

    if result.returncode != 0:
        return None

    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return None

    headers = lines[0].split("\t")
    try:
        conf_idx = headers.index("conf")
        text_idx = headers.index("text")
    except ValueError:
        return None

    confidences: list[float] = []
    alnum_chars = 0
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) <= max(conf_idx, text_idx):
            continue
        text = parts[text_idx].strip()
        if not text:
            continue
        try:
            confidence = float(parts[conf_idx].strip())
        except ValueError:
            continue
        if confidence < 0:
            continue
        confidences.append(confidence)
        alnum_chars += sum(1 for ch in text if ch.isalnum())

    if not confidences or alnum_chars < FALLBACK_OCR_MIN_ALNUM_CHARS:
        return None

    return (sum(confidences) / len(confidences), alnum_chars)


def _detect_rotation_with_ocr_fallback(image_path: Path) -> tuple[int, float | None]:
    """通过比较四个旋转方向的 OCR 置信度来做兜底朝向检测。"""
    best_score: tuple[int, float, int] | None = None

    for angle in (0, 90, 180, 270):
        score = _ocr_rotation_score(image_path, angle)
        if score is None:
            continue
        confidence, alnum_chars = score
        candidate = (alnum_chars, confidence, angle)
        if best_score is None or candidate > best_score:
            best_score = candidate

    if best_score is None:
        return 0, None

    _alnum_chars, confidence, angle = best_score
    return angle, confidence


def _render_page_png(pdf_path: Path, page_idx: int, tmpdir: Path) -> Path | None:
    """把单页 PDF 渲染成 PNG，供 OSD 分析使用。"""
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
                            angle, confidence = _detect_rotation_from_pdf_text(inputs[0], i)
                            if confidence is None:
                                angle, confidence = _detect_rotation_with_ocr_fallback(img)
                            if confidence is None:
                                skipped_pages.append(i + 1)
                                continue
                        else:
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
