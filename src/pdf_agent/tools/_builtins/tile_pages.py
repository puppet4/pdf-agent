"""Tile pages tool — combine multiple PDF pages side-by-side or top-to-bottom."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class TilePagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="tile_pages",
            label="页面拼接",
            category="page_ops",
            description="将多个 PDF 的页面横向或纵向拼接成一个宽版/长版页面",
            inputs=ToolInputSpec(min=2, max=10, accept=["application/pdf"]),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="direction",
                    label="拼接方向",
                    type="enum",
                    options=["horizontal", "vertical"],
                    default="horizontal",
                    description="horizontal=横向并排, vertical=纵向叠加",
                ),
            ],
            engine="pikepdf+reportlab",
        )

    def validate(self, params: dict) -> dict:
        direction = params.get("direction", "horizontal")
        if direction not in ("horizontal", "vertical"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid direction: {direction}")
        return {"direction": direction}

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "tiled.pdf"
        direction = params["direction"]

        # Render each PDF's first page to get dimensions
        page_dims = []
        for pdf_path in inputs:
            with pikepdf.open(pdf_path) as pdf:
                mbox = pdf.pages[0].mediabox
                w = float(mbox[2]) - float(mbox[0])
                h = float(mbox[3]) - float(mbox[1])
                page_dims.append((w, h))

        if direction == "horizontal":
            total_w = sum(d[0] for d in page_dims)
            total_h = max(d[1] for d in page_dims)
        else:
            total_w = max(d[0] for d in page_dims)
            total_h = sum(d[1] for d in page_dims)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(total_w, total_h))

        x_offset = 0.0
        y_offset = total_h
        for i, pdf_path in enumerate(inputs):
            if reporter:
                reporter(int(i / len(inputs) * 90), f"Processing {pdf_path.name}")
            pw, ph = page_dims[i]

            # Render page to temp PDF bytes
            tmp_buf = io.BytesIO()
            with pikepdf.Pdf.new() as tmp:
                with pikepdf.open(pdf_path) as src:
                    tmp.pages.append(src.pages[0])
                tmp.save(tmp_buf)
            tmp_buf.seek(0)

            if direction == "horizontal":
                y = (total_h - ph) / 2
                c.drawImage(tmp_buf, x_offset, y, width=pw, height=ph)
                x_offset += pw
            else:
                y_offset -= ph
                x = (total_w - pw) / 2
                c.drawImage(tmp_buf, x, y_offset, width=pw, height=ph)

        c.save()
        buf.seek(0)
        output_path.write_bytes(buf.read())

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"inputs": len(inputs), "direction": direction, "size": f"{total_w:.0f}x{total_h:.0f}pt"},
            log=f"Tiled {len(inputs)} PDFs {direction}ly into {total_w:.0f}x{total_h:.0f}pt page",
        )
