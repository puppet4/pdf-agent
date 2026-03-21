"""Redact text or regions by overlaying opaque rectangles."""
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


class RedactTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="redact",
            label="涂黑脱敏",
            category="security",
            description="按文本关键词或矩形区域做黑框遮盖脱敏",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all"),
                ParamSpec(name="text_query", label="文本关键词", type="string", default="", description="逗号分隔，按词匹配"),
                ParamSpec(name="regions_json", label="区域 JSON", type="string", default="[]", description='如 [{"page":1,"x":10,"y":10,"width":100,"height":30}]'),
                ParamSpec(name="fill_color", label="填充颜色", type="enum", options=["black", "white"], default="black"),
            ],
            engine="pikepdf+pdftotext+reportlab",
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
        output_path = workdir / "redacted.pdf"
        with pikepdf.open(inputs[0]) as pdf:
            page_count = len(pdf.pages)
            target_pages = set(parse_page_range(params["page_range"], page_count))
            boxes = _normalize_regions(params["regions"])
            if params["text_query"].strip():
                boxes.extend(_find_text_boxes(inputs[0], params["text_query"]))
            if not boxes:
                raise ToolError(ErrorCode.INVALID_PARAMS, "No redact targets found")

            color = black if params["fill_color"] == "black" else white
            for page_index, page in enumerate(pdf.pages, start=1):
                if (page_index - 1) not in target_pages:
                    continue
                page_boxes = [box for box in boxes if box["page"] == page_index]
                if not page_boxes:
                    continue
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
                if reporter:
                    reporter(int(page_index / page_count * 100))
            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"redaction_count": len(boxes), "content_removed": False},
            log=f"Applied {len(boxes)} visual redaction box(es)",
        )


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
