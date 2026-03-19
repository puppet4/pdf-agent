"""PDF compare tool — highlight differences between two PDFs."""
from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from PIL import Image, ImageChops, ImageDraw

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


def _render_page_png(pdf_path: Path, page_idx: int, dpi: int = 72) -> Image.Image | None:
    """Render a single PDF page to PIL Image using pdftoppm."""
    import shutil
    import subprocess
    import tempfile
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return None
    with tempfile.TemporaryDirectory() as td:
        out_stem = Path(td) / "page"
        subprocess.run(
            [pdftoppm, "-r", str(dpi), "-png", "-f", str(page_idx + 1), "-l", str(page_idx + 1),
             str(pdf_path), str(out_stem)],
            capture_output=True, timeout=30,
        )
        candidates = list(Path(td).glob("*.png"))
        if not candidates:
            return None
        return Image.open(candidates[0]).convert("RGB").copy()


class CompareTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="compare",
            label="PDF 对比",
            category="analysis",
            description="逐页对比两个 PDF 文件，生成差异高亮报告",
            inputs=ToolInputSpec(min=2, max=2),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="highlight_color", label="高亮颜色", type="enum",
                          options=["red", "yellow", "blue"], default="red",
                          description="差异区域高亮颜色"),
                ParamSpec(name="sensitivity", label="灵敏度", type="int", default=10, min=1, max=50,
                          description="差异检测灵敏度（越小越敏感）"),
            ],
            engine="poppler+pillow",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        return {
            "highlight_color": params.get("highlight_color", "red"),
            "sensitivity": max(1, min(50, int(params.get("sensitivity", 10)))),
        }

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter: ProgressReporter | None = None) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / "diff.pdf"

        color_map = {"red": (255, 0, 0, 120), "yellow": (255, 255, 0, 120), "blue": (0, 100, 255, 120)}
        highlight_rgba = color_map[params["highlight_color"]]

        with pikepdf.open(inputs[0]) as pdf1, pikepdf.open(inputs[1]) as pdf2:
            pages1 = len(pdf1.pages)
            pages2 = len(pdf2.pages)

        max_pages = max(pages1, pages2)
        diff_pages = []
        diff_count = 0

        for i in range(max_pages):
            if reporter:
                reporter(int(i / max_pages * 90), f"Comparing page {i+1}/{max_pages}")

            img1 = _render_page_png(inputs[0], i) if i < pages1 else None
            img2 = _render_page_png(inputs[1], i) if i < pages2 else None

            if img1 is None and img2 is None:
                continue

            if img1 is None or img2 is None:
                # One PDF has this page, other doesn't — fully different
                base = img1 or img2
                overlay = Image.new("RGBA", base.size, highlight_rgba)
                base_rgba = base.convert("RGBA")
                diff_img = Image.alpha_composite(base_rgba, overlay).convert("RGB")
                diff_pages.append(diff_img)
                diff_count += 1
                continue

            # Resize to same size
            if img1.size != img2.size:
                img2 = img2.resize(img1.size, Image.LANCZOS)

            diff = ImageChops.difference(img1, img2)
            threshold = params["sensitivity"]

            # Create highlight mask
            diff_gray = diff.convert("L")
            mask = diff_gray.point(lambda p: 255 if p > threshold else 0)

            # Apply highlight on img1
            highlight = Image.new("RGBA", img1.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(highlight)
            # Fill highlighted regions
            pixels = list(mask.getdata())
            w, h = img1.size
            for y in range(h):
                for x in range(w):
                    if pixels[y * w + x] > 0:
                        draw.point((x, y), fill=highlight_rgba)

            result = Image.alpha_composite(img1.convert("RGBA"), highlight).convert("RGB")
            diff_pages.append(result)

            if any(p > 0 for p in mask.getdata()):
                diff_count += 1

        if not diff_pages:
            raise ToolError(ErrorCode.OUTPUT_GENERATION_FAILED, "Could not render pages for comparison (pdftoppm required)")

        # Build output PDF
        buf = io.BytesIO()
        first = diff_pages[0]
        rest = diff_pages[1:]
        first.save(buf, format="PDF", save_all=True, append_images=rest)
        output_path.write_bytes(buf.getvalue())

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"pages_compared": max_pages, "pages_with_diff": diff_count},
            log=f"Compared {max_pages} pages, found differences on {diff_count} page(s)",
        )
