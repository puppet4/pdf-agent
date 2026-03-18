"""Integration tests for built-in tools."""
from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

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


# ────────────────────── Batch 1: Pure pikepdf ──────────────────────


class TestExtractTool:
    def test_extract_pages(self, sample_pdf: Path, workdir: Path):
        tool = ExtractTool()
        result = tool.run([sample_pdf], {"page_range": "1-3"}, workdir)
        assert len(result.output_files) == 1
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 3

    def test_extract_single_page(self, sample_pdf: Path, workdir: Path):
        tool = ExtractTool()
        result = tool.run([sample_pdf], {"page_range": "2"}, workdir)
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 1

    def test_extract_missing_range(self, sample_pdf: Path, workdir: Path):
        tool = ExtractTool()
        with pytest.raises(Exception, match="page_range is required"):
            tool.run([sample_pdf], {}, workdir)


class TestDeleteTool:
    def test_delete_pages(self, sample_pdf: Path, workdir: Path):
        tool = DeleteTool()
        result = tool.run([sample_pdf], {"page_range": "1,3"}, workdir)
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 3

    def test_delete_all_pages_fails(self, sample_pdf: Path, workdir: Path):
        tool = DeleteTool()
        with pytest.raises(Exception, match="Cannot delete all pages"):
            tool.run([sample_pdf], {"page_range": "all"}, workdir)


class TestReorderTool:
    def test_reorder(self, sample_pdf: Path, workdir: Path):
        tool = ReorderTool()
        result = tool.run([sample_pdf], {"order": "5,4,3,2,1"}, workdir)
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 5

    def test_reorder_subset(self, sample_pdf: Path, workdir: Path):
        tool = ReorderTool()
        result = tool.run([sample_pdf], {"order": "3,1"}, workdir)
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 2

    def test_reorder_out_of_range(self, sample_pdf: Path, workdir: Path):
        tool = ReorderTool()
        with pytest.raises(Exception, match="out of range"):
            tool.run([sample_pdf], {"order": "1,6"}, workdir)


class TestEncryptTool:
    def test_encrypt(self, sample_pdf: Path, workdir: Path):
        tool = EncryptTool()
        result = tool.run([sample_pdf], {"owner_password": "secret", "user_password": "open"}, workdir)
        assert result.output_files[0].exists()
        # Encrypted PDF should require password
        with pytest.raises(pikepdf.PasswordError):
            pikepdf.open(result.output_files[0])
        # Should open with correct password
        with pikepdf.open(result.output_files[0], password="open") as pdf:
            assert len(pdf.pages) == 5

    def test_encrypt_no_owner_password(self, sample_pdf: Path, workdir: Path):
        tool = EncryptTool()
        with pytest.raises(Exception, match="owner_password is required"):
            tool.run([sample_pdf], {}, workdir)


class TestDecryptTool:
    def test_decrypt(self, encrypted_pdf: Path, workdir: Path):
        tool = DecryptTool()
        result = tool.run([encrypted_pdf], {"password": "userpass"}, workdir)
        # Decrypted PDF should open without password
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 5

    def test_decrypt_wrong_password(self, encrypted_pdf: Path, workdir: Path):
        tool = DecryptTool()
        with pytest.raises(Exception, match="Incorrect password"):
            tool.run([encrypted_pdf], {"password": "wrongpass"}, workdir)


# ────────────────────── Batch 2: reportlab + Pillow ──────────────────────


class TestWatermarkTextTool:
    def test_watermark(self, sample_pdf: Path, workdir: Path):
        tool = WatermarkTextTool()
        result = tool.run([sample_pdf], {"text": "CONFIDENTIAL"}, workdir)
        assert result.output_files[0].exists()
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 5

    def test_watermark_specific_pages(self, sample_pdf: Path, workdir: Path):
        tool = WatermarkTextTool()
        result = tool.run([sample_pdf], {"text": "DRAFT", "page_range": "1,3"}, workdir)
        assert result.meta["watermarked_pages"] == 2

    def test_watermark_missing_text(self, sample_pdf: Path, workdir: Path):
        tool = WatermarkTextTool()
        with pytest.raises(Exception, match="text is required"):
            tool.run([sample_pdf], {}, workdir)


class TestAddPageNumbersTool:
    def test_add_numbers(self, sample_pdf: Path, workdir: Path):
        tool = AddPageNumbersTool()
        result = tool.run([sample_pdf], {}, workdir)
        assert result.output_files[0].exists()
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 5
        assert result.meta["numbered_pages"] == 5

    def test_add_numbers_custom_format(self, sample_pdf: Path, workdir: Path):
        tool = AddPageNumbersTool()
        result = tool.run(
            [sample_pdf],
            {"format": "Page {n}", "position": "top_right", "start_num": "10"},
            workdir,
        )
        assert result.meta["numbered_pages"] == 5


class TestImagesToPdfTool:
    def test_images_to_pdf(self, sample_images: list[Path], workdir: Path):
        tool = ImagesToPdfTool()
        result = tool.run(sample_images, {"page_size": "fit"}, workdir)
        assert result.output_files[0].exists()
        assert result.meta["image_count"] == 3

    def test_images_to_pdf_a4(self, sample_images: list[Path], workdir: Path):
        tool = ImagesToPdfTool()
        result = tool.run(sample_images, {"page_size": "A4"}, workdir)
        assert result.output_files[0].exists()


# ────────────────────── Batch 3: Subprocess tools ──────────────────────


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")
class TestCompressTool:
    def test_compress(self, sample_pdf: Path, workdir: Path):
        tool = CompressTool()
        result = tool.run([sample_pdf], {"level": "medium"}, workdir)
        assert result.output_files[0].exists()
        assert "reduction_percent" in result.meta


@pytest.mark.skipif(shutil.which("ocrmypdf") is None, reason="ocrmypdf not installed")
class TestOcrTool:
    def test_ocr(self, sample_pdf: Path, workdir: Path):
        tool = OcrTool()
        result = tool.run([sample_pdf], {"language": "eng"}, workdir)
        assert result.output_files[0].exists()


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm not installed")
class TestPdfToImagesTool:
    def test_convert_all_pages(self, sample_pdf: Path, workdir: Path):
        tool = PdfToImagesTool()
        result = tool.run([sample_pdf], {"format": "png", "dpi": "72"}, workdir)
        assert len(result.output_files) == 5
        assert all(f.suffix == ".png" for f in result.output_files)

    def test_convert_specific_pages(self, sample_pdf: Path, workdir: Path):
        tool = PdfToImagesTool()
        result = tool.run([sample_pdf], {"format": "png", "dpi": "72", "page_range": "1,3"}, workdir)
        assert len(result.output_files) == 2


# ────────────────────── Phase 2: Page editing tools ──────────────────────


class TestCropTool:
    def test_crop(self, sample_pdf: Path, workdir: Path):
        tool = CropTool()
        result = tool.run([sample_pdf], {"top": "50", "bottom": "50", "left": "30", "right": "30"}, workdir)
        assert result.output_files[0].exists()
        with pikepdf.open(result.output_files[0]) as pdf:
            assert len(pdf.pages) == 5
            mbox = pdf.pages[0].mediabox
            # Original was 612x792, after crop should be 552x692
            assert abs(float(mbox[2] - mbox[0]) - 552) < 1
            assert abs(float(mbox[3] - mbox[1]) - 692) < 1

    def test_crop_specific_pages(self, sample_pdf: Path, workdir: Path):
        tool = CropTool()
        result = tool.run([sample_pdf], {"top": "50", "page_range": "1,3"}, workdir)
        assert result.meta["cropped_pages"] == 2

    def test_crop_too_large(self, sample_pdf: Path, workdir: Path):
        tool = CropTool()
        with pytest.raises(Exception, match="too large"):
            tool.run([sample_pdf], {"left": "400", "right": "400"}, workdir)


class TestResizeTool:
    def test_resize_to_a5(self, sample_pdf: Path, workdir: Path):
        tool = ResizeTool()
        result = tool.run([sample_pdf], {"target_size": "A5"}, workdir)
        assert result.output_files[0].exists()
        assert result.meta["resized_pages"] == 5

    def test_resize_specific_pages(self, sample_pdf: Path, workdir: Path):
        tool = ResizeTool()
        result = tool.run([sample_pdf], {"target_size": "Letter", "page_range": "1-3"}, workdir)
        assert result.meta["resized_pages"] == 3


class TestWatermarkImageTool:
    def test_watermark_image(self, sample_pdf: Path, sample_images: list[Path], workdir: Path):
        tool = WatermarkImageTool()
        result = tool.run([sample_pdf, sample_images[0]], {"opacity": "0.5", "scale": "0.3"}, workdir)
        assert result.output_files[0].exists()
        assert result.meta["watermarked_pages"] == 5

    def test_watermark_image_specific_pages(self, sample_pdf: Path, sample_images: list[Path], workdir: Path):
        tool = WatermarkImageTool()
        result = tool.run(
            [sample_pdf, sample_images[0]],
            {"page_range": "1,3", "position": "bottom_right"},
            workdir,
        )
        assert result.meta["watermarked_pages"] == 2


# ────────────────────── Phase 2: Conversion tools ──────────────────────


class TestPdfToTextTool:
    def test_extract_text(self, sample_pdf: Path, workdir: Path):
        tool = PdfToTextTool()
        result = tool.run([sample_pdf], {}, workdir)
        assert result.output_files[0].exists()
        text = result.output_files[0].read_text(encoding="utf-8")
        assert "Page 1" in text or "--- Page 1 ---" in text
        assert result.meta["pages_extracted"] == 5

    def test_extract_text_specific_pages(self, sample_pdf: Path, workdir: Path):
        tool = PdfToTextTool()
        result = tool.run([sample_pdf], {"page_range": "1,3"}, workdir)
        assert result.meta["pages_extracted"] == 2


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")
class TestFlattenTool:
    def test_flatten(self, sample_pdf: Path, workdir: Path):
        tool = FlattenTool()
        result = tool.run([sample_pdf], {}, workdir)
        assert result.output_files[0].exists()


# ────────────────────── Registration ──────────────────────


class TestRegistration:
    def test_all_tools_registered(self):
        from pdf_agent.tools._builtins import get_builtin_tools
        tools = get_builtin_tools()
        assert len(tools) == 20
        names = {t.name for t in tools}
        expected = {
            "merge", "split", "rotate", "metadata_info",
            "extract", "delete", "reorder", "encrypt", "decrypt",
            "watermark_text", "watermark_image", "add_page_numbers", "images_to_pdf",
            "compress", "ocr", "pdf_to_images",
            "crop", "resize", "pdf_to_text", "flatten",
        }
        assert names == expected

    def test_all_manifests_valid(self):
        from pdf_agent.tools._builtins import get_builtin_tools
        for tool in get_builtin_tools():
            m = tool.manifest()
            assert m.name
            assert m.label
            assert m.category
            assert m.engine
