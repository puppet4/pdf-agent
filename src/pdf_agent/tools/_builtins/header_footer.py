"""Header/Footer tool — add text header and/or footer to PDF pages."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _font_name_for_text(*values: str) -> str:
    combined = "".join(value for value in values if value)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in combined)
    return "STSong-Light" if has_cjk else "Helvetica"


class HeaderFooterTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="header_footer",
            label="添加页眉页脚",
            category="annotation",
            description="在 PDF 页面顶部和/或底部添加自定义文字页眉页脚",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="header", label="页眉文字", type="string", required=False,
                          description="页面顶部显示的文字，留空则不添加"),
                ParamSpec(name="footer", label="页脚文字", type="string", required=False,
                          description="页面底部显示的文字。使用 {page} 插入页码，{total} 插入总页数"),
                ParamSpec(name="font_size", label="字号", type="int", default=10, min=6, max=24,
                          description="文字大小（点数）"),
                ParamSpec(name="margin", label="边距(pt)", type="int", default=20, min=5, max=60,
                          description="文字距页面边缘的距离"),
                ParamSpec(name="page_range", label="页范围", type="page_range", default="all"),
            ],
            engine="pikepdf+reportlab",
        )

    def validate(self, params: dict) -> dict:
        if not params.get("header") and not params.get("footer"):
            raise ToolError(ErrorCode.INVALID_PARAMS, "At least one of header or footer must be set")
        return {
            "header": params.get("header", ""),
            "footer": params.get("footer", ""),
            "font_size": max(6, min(24, int(params.get("font_size", 10)))),
            "margin": max(5, min(60, int(params.get("margin", 20)))),
            "page_range": params.get("page_range", "all"),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已加页眉页脚")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            pages = parse_page_range(params["page_range"], total)

            for idx in pages:
                page = pdf.pages[idx]
                mbox = page.mediabox
                pw = float(mbox[2]) - float(mbox[0])
                ph = float(mbox[3]) - float(mbox[1])

                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(pw, ph))
                header_text = params["header"].replace("{page}", str(idx + 1)).replace("{total}", str(total)) if params["header"] else ""
                footer_text = params["footer"].replace("{page}", str(idx + 1)).replace("{total}", str(total)) if params["footer"] else ""
                c.setFont(_font_name_for_text(header_text, footer_text), params["font_size"])

                if header_text:
                    c.drawCentredString(pw / 2, ph - params["margin"], header_text)

                if footer_text:
                    footer_y = max(2, params["margin"] - params["font_size"])
                    c.drawCentredString(pw / 2, footer_y, footer_text)

                c.showPage()
                c.save()
                buf.seek(0)

                with pikepdf.open(buf) as overlay_pdf:
                    pikepdf.Page(page).add_overlay(overlay_pdf.pages[0])

            pdf.save(output_path)

        return ToolResult(
            output_files=[output_path],
            meta={"pages_modified": len(pages), "header": params["header"], "footer": params["footer"]},
            log=f"Added header/footer to {len(pages)} page(s)",
        )
