"""Regression tests for previously fixed real-world tool bugs."""
from __future__ import annotations

from pathlib import Path

import pikepdf

from pdf_agent.tools._builtins.barcode import BarcodeTool
from pdf_agent.tools._builtins.extract_images import ExtractImagesTool
from pdf_agent.tools._builtins.qr_code import QrCodeTool
from pdf_agent.tools._builtins.set_metadata import SetMetadataTool
from pdf_agent.tools._builtins.signature import SignatureTool
from pdf_agent.tools._builtins.split import SplitTool
from pdf_agent.tools._builtins.stamp import StampTool


def _tool_dir(workdir: Path, name: str) -> Path:
    path = workdir / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_set_metadata_persists_docinfo_fields(sample_pdf: Path, workdir: Path):
    result = SetMetadataTool().run(
        [sample_pdf],
        {"title": "测试标题", "author": "测试作者", "keywords": "a,b"},
        _tool_dir(workdir, "set_metadata"),
    )

    with pikepdf.open(result.output_files[0]) as pdf:
        assert pdf.docinfo[pikepdf.Name("/Title")] == "测试标题"
        assert pdf.docinfo[pikepdf.Name("/Author")] == "测试作者"
        assert pdf.docinfo[pikepdf.Name("/Keywords")] == "a,b"


def test_extract_images_returns_existing_absolute_paths(image_pdf: Path, workdir: Path):
    result = ExtractImagesTool().run([image_pdf], {}, _tool_dir(workdir, "extract_images"))

    assert result.meta["image_count"] == len(result.output_files) >= 1
    assert all(path.is_absolute() for path in result.output_files)
    assert all(path.exists() for path in result.output_files)


def test_image_overlay_tools_preserve_page_count_and_use_localized_names(
    sample_pdf: Path,
    sample_images: list[Path],
    workdir: Path,
):
    stamp_result = StampTool().run(
        [sample_pdf, sample_images[0]],
        {"page_range": "1-2", "position": "bottom-right", "opacity": 0.6, "scale": 0.2},
        _tool_dir(workdir, "stamp"),
    )
    barcode_result = BarcodeTool().run(
        [sample_pdf],
        {"content": "ABC123", "barcode_type": "code128", "page_range": "1-2"},
        _tool_dir(workdir, "barcode"),
    )
    qr_result = QrCodeTool().run(
        [sample_pdf],
        {"content": "https://example.com", "page_range": "1-2", "size": 64},
        _tool_dir(workdir, "qr"),
    )
    signature_result = SignatureTool().run(
        [sample_pdf, sample_images[1]],
        {"mode": "visible", "page": 1, "position": "bottom-right", "width_pt": 96},
        _tool_dir(workdir, "signature"),
    )

    expectations = [
        (stamp_result.output_files[0], "已盖章"),
        (barcode_result.output_files[0], "已加条形码"),
        (qr_result.output_files[0], "已加二维码"),
        (signature_result.output_files[0], "已签名"),
    ]
    for output_path, suffix in expectations:
        assert suffix in output_path.name
        with pikepdf.open(output_path) as pdf:
            assert len(pdf.pages) == 5


def test_split_bookmark_mode_generates_expected_ranges(sample_pdf: Path, workdir: Path):
    bookmarked = workdir / "bookmarked.pdf"
    with pikepdf.open(sample_pdf) as pdf:
        with pdf.open_outline() as outline:
            outline.root.append(pikepdf.OutlineItem("PartA", 0))
            outline.root.append(pikepdf.OutlineItem("PartB", 2))
        pdf.save(bookmarked)

    result = SplitTool().run([bookmarked], {"mode": "bookmark"}, _tool_dir(workdir, "split_bookmark"))

    assert len(result.output_files) == 2
    assert "书签_0001_PartA" in result.output_files[0].name
    assert "书签_0002_PartB" in result.output_files[1].name
    with pikepdf.open(result.output_files[0]) as first:
        assert len(first.pages) == 2
    with pikepdf.open(result.output_files[1]) as second:
        assert len(second.pages) == 3


def test_split_range_mode_supports_multiple_custom_groups(sample_pdf: Path, workdir: Path):
    result = SplitTool().run(
        [sample_pdf],
        {"mode": "range", "page_groups": "1|2-5"},
        _tool_dir(workdir, "split_groups"),
    )

    assert len(result.output_files) == 2
    with pikepdf.open(result.output_files[0]) as first:
        assert len(first.pages) == 1
    with pikepdf.open(result.output_files[1]) as second:
        assert len(second.pages) == 4
