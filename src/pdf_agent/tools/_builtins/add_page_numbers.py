"""Add page numbers tool - add page numbers to PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas

from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


class AddPageNumbersTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="add_page_numbers",
            label="添加页码",
            category="edit",
            description="在 PDF 页面上添加页码",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="position",
                    label="位置",
                    type="enum",
                    options=["bottom_center", "bottom_left", "bottom_right", "top_center", "top_left", "top_right"],
                    default="bottom_center",
                    description="页码位置",
                ),
                ParamSpec(
                    name="start_num",
                    label="起始页码",
                    type="int",
                    default=1,
                    min=0,
                    description="页码起始数字",
                ),
                ParamSpec(
                    name="font_size",
                    label="字号",
                    type="int",
                    default=12,
                    min=6,
                    max=72,
                    description="页码字号",
                ),
                ParamSpec(
                    name="format",
                    label="格式",
                    type="enum",
                    options=["{n}", "- {n} -", "Page {n}", "{n}/{total}"],
                    default="{n}",
                    description="页码显示格式",
                ),
                ParamSpec(
                    name="page_range",
                    label="页范围",
                    type="page_range",
                    default="all",
                    description="要添加页码的页面范围",
                ),
            ],
            engine="reportlab+pikepdf",
        )

    def validate(self, params: dict) -> dict:
        return {
            "position": params.get("position", "bottom_center"),
            "start_num": int(params.get("start_num", 1)),
            "font_size": int(params.get("font_size", 12)),
            "format": params.get("format", "{n}"),
            "page_range": params.get("page_range", "all"),
        }

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已加页码")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            target_pages = parse_page_range(params["page_range"], total)
            target_set = set(target_pages)

            current_num = params["start_num"]
            for i in range(total):
                if i not in target_set:
                    continue

                page = pdf.pages[i]
                mbox = page.mediabox
                page_w = float(mbox[2] - mbox[0])
                page_h = float(mbox[3] - mbox[1])

                text = params["format"].replace("{n}", str(current_num)).replace("{total}", str(total))
                current_num += 1

                overlay_buf = _make_number_overlay(
                    text=text,
                    page_w=page_w,
                    page_h=page_h,
                    position=params["position"],
                    font_size=params["font_size"],
                )
                with pikepdf.open(overlay_buf) as wm:
                    page.add_overlay(wm.pages[0])

                if reporter:
                    reporter(int((i + 1) / total * 100))

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"numbered_pages": len(target_pages)},
            log=f"Added page numbers to {len(target_pages)} pages",
        )


_MARGIN = 30  # points from edge


def _make_number_overlay(
    text: str,
    page_w: float,
    page_h: float,
    position: str,
    font_size: int,
) -> io.BytesIO:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFont("Helvetica", font_size)
    c.setFillColorRGB(0, 0, 0)

    if "bottom" in position:
        y = _MARGIN
    else:
        y = page_h - _MARGIN

    if "center" in position:
        c.drawCentredString(page_w / 2, y, text)
    elif "left" in position:
        c.drawString(_MARGIN, y, text)
    else:  # right
        c.drawRightString(page_w - _MARGIN, y, text)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
