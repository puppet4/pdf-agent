"""Remove blank pages tool — detect and remove near-blank pages from a PDF."""
from __future__ import annotations

from pathlib import Path

import pikepdf
from PIL import Image

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult
from pdf_agent.tools.filenames import localized_output_name


def _is_blank(page: pikepdf.Page, threshold: float = 0.99) -> bool:
    """Check if a page is blank by rendering a tiny thumbnail and checking whiteness."""
    try:
        import shutil
        import tempfile
        from pdf_agent.external_commands import run_command
        gs = shutil.which("gs")
        if not gs:
            return False
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "page.png"
            # Write single page to temp PDF then render
            with pikepdf.Pdf.new() as tmp:
                tmp.pages.append(page)
                tmp_pdf = Path(td) / "tmp.pdf"
                tmp.save(tmp_pdf)
            result = run_command(
                [gs, "-sDEVICE=pngmono", "-r20", "-dNOPAUSE", "-dBATCH", "-dQUIET",
                 f"-sOutputFile={out}", str(tmp_pdf)],
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            if not out.exists():
                return False
            img = Image.open(out).convert("L")
            histogram = img.histogram()
            pixel_count = sum(histogram)
            if pixel_count == 0:
                return False
            white = sum(histogram[201:])
            return (white / pixel_count) >= threshold
    except Exception:
        return False


class RemoveBlankPagesTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="remove_blank_pages",
            label="删除空白页",
            category="page_ops",
            description="自动检测并删除 PDF 中的空白页",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="threshold",
                    label="空白阈值",
                    type="float",
                    default=0.99,
                    min=0.8,
                    max=1.0,
                    description="页面白色像素占比达到此值时视为空白页（0.99 = 99%）",
                ),
            ],
            engine="pikepdf+ghostscript",
            async_hint=True,
        )

    def validate(self, params: dict) -> dict:
        threshold = float(params.get("threshold", 0.99))
        return {"threshold": max(0.8, min(1.0, threshold))}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        output_path = workdir / localized_output_name(inputs[0], "已删除空白页")

        with pikepdf.open(inputs[0]) as pdf:
            total = len(pdf.pages)
            blank_indices = []
            for i, page in enumerate(pdf.pages):
                if reporter:
                    reporter(int(i / total * 80), f"Checking page {i+1}/{total}")
                if _is_blank(page, params["threshold"]):
                    blank_indices.append(i)

            if len(blank_indices) >= total:
                raise ToolError(ErrorCode.INVALID_PARAMS, "Cannot remove all pages — no non-blank pages found")

            # Remove in reverse order to preserve indices
            for idx in sorted(blank_indices, reverse=True):
                del pdf.pages[idx]

            if reporter:
                reporter(95, "Saving...")
            pdf.save(output_path)

        if reporter:
            reporter(100, "Done")

        return ToolResult(
            output_files=[output_path],
            meta={"original_pages": total, "removed_pages": len(blank_indices), "remaining_pages": total - len(blank_indices)},
            log=f"Removed {len(blank_indices)} blank page(s) out of {total}",
        )
