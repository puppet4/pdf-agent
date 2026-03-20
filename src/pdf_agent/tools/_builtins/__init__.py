"""Built-in tools package."""
from __future__ import annotations

from pdf_agent.tools.base import BaseTool


def get_builtin_tools() -> list[BaseTool]:
    """Return all built-in tool instances."""
    from pdf_agent.tools._builtins.merge import MergeTool
    from pdf_agent.tools._builtins.split import SplitTool
    from pdf_agent.tools._builtins.rotate import RotateTool
    from pdf_agent.tools._builtins.metadata_info import MetadataInfoTool
    from pdf_agent.tools._builtins.set_metadata import SetMetadataTool
    from pdf_agent.tools._builtins.extract import ExtractTool
    from pdf_agent.tools._builtins.delete import DeleteTool
    from pdf_agent.tools._builtins.reorder import ReorderTool
    from pdf_agent.tools._builtins.reverse_pages import ReversePagesTool
    from pdf_agent.tools._builtins.encrypt import EncryptTool
    from pdf_agent.tools._builtins.decrypt import DecryptTool
    from pdf_agent.tools._builtins.watermark_text import WatermarkTextTool
    from pdf_agent.tools._builtins.watermark_image import WatermarkImageTool
    from pdf_agent.tools._builtins.stamp import StampTool
    from pdf_agent.tools._builtins.add_page_numbers import AddPageNumbersTool
    from pdf_agent.tools._builtins.images_to_pdf import ImagesToPdfTool
    from pdf_agent.tools._builtins.compress import CompressTool
    from pdf_agent.tools._builtins.repair import RepairTool
    from pdf_agent.tools._builtins.ocr import OcrTool
    from pdf_agent.tools._builtins.pdf_to_images import PdfToImagesTool
    from pdf_agent.tools._builtins.pdf_to_word import PdfToWordTool
    from pdf_agent.tools._builtins.pdf_to_office import PdfToExcelTool, PdfToPptTool
    from pdf_agent.tools._builtins.pdf_to_text import PdfToTextTool
    from pdf_agent.tools._builtins.pdf_to_html import PdfToHtmlTool
    from pdf_agent.tools._builtins.crop import CropTool
    from pdf_agent.tools._builtins.resize import ResizeTool
    from pdf_agent.tools._builtins.flatten import FlattenTool
    from pdf_agent.tools._builtins.remove_blank_pages import RemoveBlankPagesTool
    from pdf_agent.tools._builtins.nup import NUpTool
    from pdf_agent.tools._builtins.qr_code import QrCodeTool
    from pdf_agent.tools._builtins.compare import CompareTool
    from pdf_agent.tools._builtins.tile_pages import TilePagesTool
    from pdf_agent.tools._builtins.form_fill import FormFillTool
    from pdf_agent.tools._builtins.signature_info import SignatureInfoTool
    from pdf_agent.tools._builtins.signature import SignatureTool
    from pdf_agent.tools._builtins.deskew import DeskewTool
    from pdf_agent.tools._builtins.pdf_to_markdown import PdfToMarkdownTool
    from pdf_agent.tools._builtins.header_footer import HeaderFooterTool
    from pdf_agent.tools._builtins.remove_metadata import RemoveMetadataTool
    from pdf_agent.tools._builtins.pdf_to_pdfa import PdfATool

    return [
        MergeTool(), SplitTool(), RotateTool(),
        MetadataInfoTool(), SetMetadataTool(), RemoveMetadataTool(),
        ExtractTool(), DeleteTool(), ReorderTool(), ReversePagesTool(),
        EncryptTool(), DecryptTool(),
        WatermarkTextTool(), WatermarkImageTool(), StampTool(),
        HeaderFooterTool(), AddPageNumbersTool(), ImagesToPdfTool(),
        CompressTool(), RepairTool(), OcrTool(),
        PdfToImagesTool(), PdfToWordTool(), PdfToExcelTool(), PdfToPptTool(),
        PdfToTextTool(), PdfToHtmlTool(), PdfToMarkdownTool(), PdfATool(),
        CropTool(), ResizeTool(), FlattenTool(),
        RemoveBlankPagesTool(), NUpTool(), QrCodeTool(), CompareTool(), TilePagesTool(),
        FormFillTool(), SignatureInfoTool(), SignatureTool(), DeskewTool(),
    ]
