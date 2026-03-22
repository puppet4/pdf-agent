"""Guard tests for important tool error contracts."""
from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.tools._builtins.delete import DeleteTool
from pdf_agent.tools._builtins.extract_attachments import ExtractAttachmentsTool
from pdf_agent.tools._builtins.extract_images import ExtractImagesTool
from pdf_agent.tools._builtins.form_fill import FormFillTool
from pdf_agent.tools._builtins.pdf_to_text import PdfToTextTool
from pdf_agent.tools._builtins.remove_blank_pages import RemoveBlankPagesTool
from pdf_agent.tools._builtins.signature import SignatureTool
from pdf_agent.tools._builtins.split import SplitTool
from pdf_agent.tools._builtins.stamp import StampTool


def _tool_dir(workdir: Path, name: str) -> Path:
    path = workdir / name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def all_blank_pdf(tmp_path: Path) -> Path:
    target = tmp_path / "all_blank.pdf"
    c = canvas.Canvas(str(target), pagesize=(300, 300))
    c.showPage()
    c.showPage()
    c.save()
    return target


def test_extract_images_raises_when_pdf_has_no_embedded_images(rendered_text_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        ExtractImagesTool().run([rendered_text_pdf], {}, _tool_dir(workdir, "extract_images_error"))

    assert exc_info.value.code == ErrorCode.OUTPUT_GENERATION_FAILED
    assert "No embedded images" in str(exc_info.value)


def test_extract_attachments_raises_when_pdf_has_no_attachments(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        ExtractAttachmentsTool().run([sample_pdf], {}, _tool_dir(workdir, "extract_attachments_error"))

    assert exc_info.value.code == ErrorCode.OUTPUT_GENERATION_FAILED
    assert "No embedded attachments" in str(exc_info.value)


def test_pdf_to_text_raises_when_pdf_has_no_extractable_text(scanned_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        PdfToTextTool().run([scanned_pdf], {"page_range": "all"}, _tool_dir(workdir, "pdf_to_text_error"))

    assert exc_info.value.code == ErrorCode.OUTPUT_GENERATION_FAILED
    assert "No extractable text" in str(exc_info.value)


def test_stamp_requires_image_input(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        StampTool().run([sample_pdf], {"page_range": "1"}, _tool_dir(workdir, "stamp_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "No stamp image provided" in str(exc_info.value)


def test_signature_visible_mode_requires_image(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        SignatureTool().run([sample_pdf], {"mode": "visible"}, _tool_dir(workdir, "signature_visible_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "Visible signature requires an image input" in str(exc_info.value)


def test_signature_digital_mode_requires_certificate(sample_pdf: Path, sample_images: list[Path], workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        SignatureTool().run(
            [sample_pdf, sample_images[0]],
            {"mode": "digital", "field_name": "Sig1"},
            _tool_dir(workdir, "signature_digital_error"),
        )

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "Digital signature requires a .p12/.pfx certificate" in str(exc_info.value)


def test_split_bookmark_mode_requires_bookmarks(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        SplitTool().run([sample_pdf], {"mode": "bookmark"}, _tool_dir(workdir, "split_bookmark_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "No usable bookmarks found" in str(exc_info.value)


def test_delete_rejects_removing_all_pages(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        DeleteTool().run([sample_pdf], {"page_range": "1-5"}, _tool_dir(workdir, "delete_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "Cannot delete all pages" in str(exc_info.value)


def test_remove_blank_pages_rejects_removing_every_page(all_blank_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        RemoveBlankPagesTool().run([all_blank_pdf], {"threshold": 0.98}, _tool_dir(workdir, "remove_blank_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "Cannot remove all pages" in str(exc_info.value)


def test_form_fill_rejects_invalid_json(form_pdf: Path, workdir: Path):
    with pytest.raises(ToolError) as exc_info:
        FormFillTool().run([form_pdf], {"field_values": "{bad json}", "flatten": False}, _tool_dir(workdir, "form_fill_error"))

    assert exc_info.value.code == ErrorCode.INVALID_PARAMS
    assert "Invalid JSON" in str(exc_info.value)
