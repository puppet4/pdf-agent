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
    from pdf_agent.tools._builtins.barcode import BarcodeTool
    from pdf_agent.tools._builtins.linearize import LinearizeTool
    from pdf_agent.tools._builtins.page_border import PageBorderTool
    from pdf_agent.tools._builtins.validate import ValidateTool
    from pdf_agent.tools._builtins.auto_rotate import AutoRotateTool
    from pdf_agent.tools._builtins.pages_to_zip import PagesToZipTool
    from pdf_agent.tools._builtins.add_blank_pages import AddBlankPagesTool
    from pdf_agent.tools._builtins.booklet import BookletTool
    from pdf_agent.tools._builtins.office_to_pdf import OfficeToPdfTool
    from pdf_agent.tools._builtins.extract_images import ExtractImagesTool
    from pdf_agent.tools._builtins.extract_attachments import ExtractAttachmentsTool
    from pdf_agent.tools._builtins.redact import RedactTool

    return [
        MergeTool(), SplitTool(), RotateTool(), AutoRotateTool(), AddBlankPagesTool(), BookletTool(),
        MetadataInfoTool(), SetMetadataTool(), RemoveMetadataTool(),
        ExtractTool(), DeleteTool(), ReorderTool(), ReversePagesTool(),
        EncryptTool(), DecryptTool(),
        WatermarkTextTool(), WatermarkImageTool(), StampTool(),
        HeaderFooterTool(), AddPageNumbersTool(), ImagesToPdfTool(),
        BarcodeTool(), PageBorderTool(),
        CompressTool(), RepairTool(), LinearizeTool(), OcrTool(),
        PdfToImagesTool(), PagesToZipTool(), PdfToWordTool(), PdfToExcelTool(), PdfToPptTool(), OfficeToPdfTool(),
        PdfToTextTool(), PdfToHtmlTool(), PdfToMarkdownTool(), PdfATool(),
        CropTool(), ResizeTool(), FlattenTool(),
        RemoveBlankPagesTool(), NUpTool(), QrCodeTool(), CompareTool(), TilePagesTool(),
        FormFillTool(), SignatureInfoTool(), SignatureTool(), DeskewTool(),
        ValidateTool(), ExtractImagesTool(), ExtractAttachmentsTool(), RedactTool(),
    ]
