"""Built-in tools package."""
from __future__ import annotations

from pdf_agent.tools.base import BaseTool


def get_builtin_tools() -> list[BaseTool]:
    """Return all built-in tool instances."""
    from pdf_agent.tools._builtins.merge import MergeTool
    from pdf_agent.tools._builtins.split import SplitTool
    from pdf_agent.tools._builtins.rotate import RotateTool
    from pdf_agent.tools._builtins.metadata_info import MetadataInfoTool
    from pdf_agent.tools._builtins.extract import ExtractTool
    from pdf_agent.tools._builtins.delete import DeleteTool
    from pdf_agent.tools._builtins.reorder import ReorderTool
    from pdf_agent.tools._builtins.encrypt import EncryptTool
    from pdf_agent.tools._builtins.decrypt import DecryptTool
    from pdf_agent.tools._builtins.watermark_text import WatermarkTextTool
    from pdf_agent.tools._builtins.watermark_image import WatermarkImageTool
    from pdf_agent.tools._builtins.add_page_numbers import AddPageNumbersTool
    from pdf_agent.tools._builtins.images_to_pdf import ImagesToPdfTool
    from pdf_agent.tools._builtins.compress import CompressTool
    from pdf_agent.tools._builtins.ocr import OcrTool
    from pdf_agent.tools._builtins.pdf_to_images import PdfToImagesTool
    from pdf_agent.tools._builtins.crop import CropTool
    from pdf_agent.tools._builtins.resize import ResizeTool
    from pdf_agent.tools._builtins.pdf_to_text import PdfToTextTool
    from pdf_agent.tools._builtins.flatten import FlattenTool

    return [
        MergeTool(),
        SplitTool(),
        RotateTool(),
        MetadataInfoTool(),
        ExtractTool(),
        DeleteTool(),
        ReorderTool(),
        EncryptTool(),
        DecryptTool(),
        WatermarkTextTool(),
        WatermarkImageTool(),
        AddPageNumbersTool(),
        ImagesToPdfTool(),
        CompressTool(),
        OcrTool(),
        PdfToImagesTool(),
        CropTool(),
        ResizeTool(),
        PdfToTextTool(),
        FlattenTool(),
    ]
