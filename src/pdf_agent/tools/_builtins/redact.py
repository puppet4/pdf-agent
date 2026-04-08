"""Redact text or regions — overlay opaque rectangles then rasterize to remove underlying text."""
from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pikepdf
from reportlab.lib.colors import black, white
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class RedactTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="redact",
            label="涂黑脱敏",
            category="security",
            description="按文本关键词或矩形区域做脱敏处理。启用 Ghostscript 时会彻底移除底层文字；否则仅做视觉遮挡并返回显著警告。",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all"),
                ParamSpec(name="text_query", label="文本关键词", type="string", default="", description="逗号分隔，按词匹配"),
                ParamSpec(name="regions_json", label="区域 JSON", type="string", default="[]", description='如 [{"page":1,"x":10,"y":10,"width":100,"height":30}]'),
                ParamSpec(name="fill_color", label="填充颜色", type="enum", options=["black", "white"], default="black"),
            ],
            engine="pikepdf+pdftotext+reportlab+ghostscript",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        try:
            regions = json.loads(params.get("regions_json", "[]") or "[]")
        except json.JSONDecodeError as exc:
            raise ToolError(ErrorCode.INVALID_PARAMS, "regions_json must be valid JSON") from exc
        return {
            "page_range": params.get("page_range", "all"),
            "text_query": str(params.get("text_query", "")),
            "regions": regions if isinstance(regions, list) else [],
            "fill_color": params.get("fill_color", "black"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已脱敏")

        with pikepdf.open(inputs[0]) as pdf:
            page_count = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], page_count))
            boxes = _normalize_regions(params["regions"])
            if params["text_query"].strip():
                boxes.extend(_find_text_boxes(inputs[0], params["text_query"]))
            if not boxes:
                raise ToolError(ErrorCode.INVALID_PARAMS, "No redact targets found")

            # Step 1: overlay opaque rectangles
            redacted_page_indices: set[int] = set()
            color = black if params["fill_color"] == "black" else white
            for page_index, page in enumerate(pdf.pages, start=1):
                if (page_index - 1) not in target_pages:
                    continue
                page_boxes = [box for box in boxes if box["page"] == page_index]
                if not page_boxes:
                    continue
                redacted_page_indices.add(page_index - 1)
                mbox = page.mediabox
                width = float(mbox[2] - mbox[0])
                height = float(mbox[3] - mbox[1])
                overlay_path = workdir / f"redact_overlay_{page_index:04d}.pdf"
                c = canvas.Canvas(str(overlay_path), pagesize=(width, height))
                c.setFillColor(color)
                c.setStrokeColor(color)
                for box in page_boxes:
                    c.rect(box["x"], box["y"], box["width"], box["height"], fill=1, stroke=0)
                c.save()
                with pikepdf.open(overlay_path) as overlay_pdf:
                    pikepdf.Page(page).add_overlay(overlay_pdf.pages[0])
                overlay_path.unlink(missing_ok=True)
                if reporter:
                    reporter(int(page_index / page_count * 50))

            # Save intermediate PDF with overlays applied
            intermediate_path = workdir / "redact_intermediate.pdf"
            pdf.save(intermediate_path)

        # Step 2: rasterize redacted pages to remove underlying text
        content_removed = False
        warning = ""
        gs_bin = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("ghostscript")
        if gs_bin and redacted_page_indices:
            try:
                _rasterize_pages(
                    gs_bin=gs_bin,
                    input_path=intermediate_path,
                    output_path=output_path,
                    redacted_pages=redacted_page_indices,
                    total_pages=page_count,
                    workdir=workdir,
                    reporter=reporter,
                )
                content_removed = True
            except Exception as exc:
                # Fall back to overlay-only if rasterization fails
                shutil.copy2(intermediate_path, output_path)
                warning = f"Ghostscript rasterization failed: {exc}. Underlying text may remain searchable."
        else:
            shutil.copy2(intermediate_path, output_path)
            warning = "Ghostscript not found. Underlying text was not removed; this output is visual redaction only."

        intermediate_path.unlink(missing_ok=True)

        log = f"Applied {len(boxes)} redaction box(es)"
        if content_removed:
            log += " — underlying text removed via rasterization"
        elif warning:
            log += f" — WARNING: {warning}"

        meta = {
            "redaction_count": len(boxes),
            "content_removed": content_removed,
            "redaction_mode": "full" if content_removed else "visual_only",
        }
        if warning:
            meta["warning"] = warning
        return ToolResult(output_files=[output_path], meta=meta, log=log)


def _rasterize_pages(
    *,
    gs_bin: str,
    input_path: Path,
    output_path: Path,
    redacted_pages: set[int],
    total_pages: int,
    workdir: Path,
    reporter: ProgressReporter | None = None,
) -> None:
    """Rasterize only the redacted pages and reassemble with untouched pages."""
    with tempfile.TemporaryDirectory(dir=workdir) as tmpdir:
        tmp = Path(tmpdir)

        # Rasterize each redacted page individually at 200 DPI
        rasterized_pdfs: dict[int, Path] = {}
        for page_idx in sorted(redacted_pages):
            page_num = page_idx + 1  # gs uses 1-based
            img_prefix = tmp / f"page_{page_num:04d}"
            run_command(
                [
                    gs_bin, "-dNOPAUSE", "-dBATCH", "-dSAFER",
                    "-sDEVICE=png16m", "-r200",
                    f"-dFirstPage={page_num}", f"-dLastPage={page_num}",
                    f"-sOutputFile={img_prefix}.png",
                    str(input_path),
                ],
                check=True,
                timeout=60,
            )
            # Convert rasterized image back to PDF page
            raster_pdf = tmp / f"page_{page_num:04d}.pdf"
            run_command(
                [
                    gs_bin, "-dNOPAUSE", "-dBATCH", "-dSAFER",
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.5",
                    "-dPDFFitPage",
                    f"-sOutputFile={raster_pdf}",
                    f"{img_prefix}.png",
                ],
                check=True,
                timeout=60,
            )
            rasterized_pdfs[page_idx] = raster_pdf
            if reporter:
                reporter(50 + int((page_idx + 1) / total_pages * 40))

        # Reassemble into a temp file, then swap into place once writes are complete.
        staged_output_path = tmp / "redact_reassembled.pdf"
        with pikepdf.open(input_path) as pdf:
            for page_idx, raster_path in rasterized_pdfs.items():
                with pikepdf.open(raster_path) as raster_pdf:
                    pdf.pages[page_idx] = raster_pdf.pages[0]
            pdf.save(staged_output_path)

        shutil.move(str(staged_output_path), str(output_path))

        if reporter:
            reporter(100)


def _normalize_regions(regions: list[dict]) -> list[dict[str, float | int]]:
    normalized = []
    for region in regions:
        try:
            normalized.append(
                {
                    "page": int(region["page"]),
                    "x": float(region["x"]),
                    "y": float(region["y"]),
                    "width": float(region["width"]),
                    "height": float(region["height"]),
                }
            )
        except Exception:
            continue
    return normalized


def _find_text_boxes(pdf_path: Path, text_query: str) -> list[dict[str, float | int]]:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise ToolError(ErrorCode.ENGINE_NOT_INSTALLED, "pdftotext is required for text-based redaction")

    wanted = {token.strip() for token in text_query.split(",") if token.strip()}
    if not wanted:
        return []
    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = Path(tmpdir) / "bbox.xml"
        cmd = [pdftotext, "-bbox-layout", str(pdf_path), str(xml_path)]
        result = run_command(cmd, check=False)
        if result.returncode != 0 or not xml_path.exists():
            detail = result.stderr.decode("utf-8", errors="replace").strip() or "pdftotext failed"
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, detail)

        tree = ET.parse(xml_path)
        root = tree.getroot()
        boxes: list[dict[str, float | int]] = []
        for page_number, page in enumerate(root.findall(".//{*}page"), start=1):
            page_height = float(page.attrib.get("height", "0") or 0)
            for word in page.findall(".//{*}word"):
                text = (word.text or "").strip()
                if text not in wanted:
                    continue
                x_min = float(word.attrib.get("xMin", "0"))
                y_min = float(word.attrib.get("yMin", "0"))
                x_max = float(word.attrib.get("xMax", "0"))
                y_max = float(word.attrib.get("yMax", "0"))
                boxes.append(
                    {
                        "page": page_number,
                        "x": x_min,
                        "y": page_height - y_max,
                        "width": max(0.0, x_max - x_min),
                        "height": max(0.0, y_max - y_min),
                    }
                )
        return boxes
