"""Images to PDF tool - combine multiple images into a single PDF."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ProgressReporter, ToolResult


class ImagesToPdfTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="images_to_pdf",
            label="图片转 PDF",
            category="convert",
            description="将多张图片合成为一个 PDF 文件",
            inputs=ToolInputSpec(
                min=1,
                max=100,
                accept=["image/png", "image/jpeg", "image/webp", "image/tiff", "image/bmp"],
            ),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(
                    name="page_size",
                    label="页面尺寸",
                    type="enum",
                    options=["fit", "A4", "Letter"],
                    default="fit",
                    description="fit=按图片原始尺寸, A4=A4 纸张, Letter=Letter 纸张",
                ),
            ],
            engine="Pillow",
        )

    def validate(self, params: dict) -> dict:
        page_size = params.get("page_size", "fit")
        if page_size not in ("fit", "A4", "Letter"):
            raise ToolError(ErrorCode.INVALID_PARAMS, f"Invalid page_size: {page_size}")
        return {"page_size": page_size}

    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        params = self.validate(params)
        if not inputs:
            raise ToolError(ErrorCode.INVALID_INPUT_FILE, "At least one image is required")

        output_path = workdir / "images.pdf"
        page_size = params["page_size"]

        # Fixed page dimensions in points (72 dpi)
        page_sizes = {
            "A4": (595.28, 841.89),
            "Letter": (612, 792),
        }

        images: list[Image.Image] = []
        for i, img_path in enumerate(inputs):
            try:
                img = Image.open(img_path)
            except Exception as exc:
                raise ToolError(ErrorCode.INVALID_INPUT_FILE, f"Cannot open image {img_path.name}: {exc}")

            # Convert to RGB if necessary (e.g., RGBA, P mode)
            if img.mode in ("RGBA", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if page_size != "fit":
                target_w, target_h = page_sizes[page_size]
                # Scale image to fit within page, maintaining aspect ratio
                # Convert points to pixels at 72 dpi (1:1)
                scale = min(target_w / img.width, target_h / img.height)
                if scale < 1:
                    new_w = int(img.width * scale)
                    new_h = int(img.height * scale)
                    img = img.resize((new_w, new_h), Image.LANCZOS)

            images.append(img)
            if reporter:
                reporter(int((i + 1) / len(inputs) * 50))

        # Save as PDF
        first = images[0]
        rest = images[1:] if len(images) > 1 else []
        first.save(output_path, "PDF", resolution=72.0, save_all=True, append_images=rest)

        if reporter:
            reporter(100)

        return ToolResult(
            output_files=[output_path],
            meta={"image_count": len(images)},
            log=f"Combined {len(images)} images into PDF",
        )
