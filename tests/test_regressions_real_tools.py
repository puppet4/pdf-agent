"""Regression tests for previously fixed real-world tool bugs."""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from pdf_agent.api.agent import _count_artifacts, _list_artifacts, _resolve_message_named_artifact_paths
from pdf_agent.core import ErrorCode, ToolError
from pdf_agent.tools._builtins import auto_rotate as auto_rotate_module
from pdf_agent.tools._builtins.auto_rotate import AutoRotateTool
from pdf_agent.tools._builtins.barcode import BarcodeTool
from pdf_agent.tools._builtins.deskew import DeskewTool
from pdf_agent.tools._builtins.extract_images import ExtractImagesTool
from pdf_agent.tools._builtins.header_footer import HeaderFooterTool
from pdf_agent.tools._builtins.qr_code import QrCodeTool
from pdf_agent.tools._builtins.rotate import RotateTool
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


def test_auto_rotate_skips_pages_with_too_few_characters(
    sample_pdf: Path,
    sample_images: list[Path],
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls = {"count": 0}

    monkeypatch.setattr(auto_rotate_module.shutil, "which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(auto_rotate_module, "_render_page_png", lambda *_args, **_kwargs: sample_images[0])

    def fake_detect_rotation(_image_path: Path) -> tuple[int, float | None]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ToolError(ErrorCode.ENGINE_EXEC_FAILED, "Too few characters. Skipping this page")
        return 90, 8.0

    monkeypatch.setattr(auto_rotate_module, "_detect_rotation", fake_detect_rotation)

    result = AutoRotateTool().run(
        [sample_pdf],
        {"min_confidence": 2},
        _tool_dir(workdir, "auto_rotate_low_text"),
    )

    assert result.output_files[0].exists()
    assert result.meta["total_pages"] == 5
    assert result.meta["rotations"]
    assert result.meta["rotations"][0]["page"] == 2


def test_auto_rotate_respects_high_min_confidence_threshold(
    sample_pdf: Path,
    sample_images: list[Path],
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auto_rotate_module.shutil, "which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(auto_rotate_module, "_render_page_png", lambda *_args, **_kwargs: sample_images[0])
    monkeypatch.setattr(auto_rotate_module, "_detect_rotation", lambda _image_path: (90, 15.0))

    result = AutoRotateTool().run(
        [sample_pdf],
        {"min_confidence": 20},
        _tool_dir(workdir, "auto_rotate_high_threshold"),
    )

    assert result.output_files[0].exists()
    assert result.meta["rotations"] == []


def test_conversation_artifacts_ignore_hidden_tool_profile_files(tmp_path: Path):
    conversation_dir = tmp_path / "conversation"
    step_dir = conversation_dir / "step_1"
    step_dir.mkdir(parents=True)

    expected_output = step_dir / "result.docx"
    expected_output.write_bytes(b"docx")

    hidden_profile_file = step_dir / ".libreoffice-profile" / "user" / "registrymodifications.xcu"
    hidden_profile_file.parent.mkdir(parents=True)
    hidden_profile_file.write_text("<xml />", encoding="utf-8")

    visible_nested_temp = step_dir / ".libreoffice-profile" / "user" / "buildid"
    visible_nested_temp.write_text("temp", encoding="utf-8")

    artifacts = _list_artifacts(conversation_dir, "conv-1")

    assert [item["filename"] for item in artifacts] == ["result.docx"]
    assert _count_artifacts(conversation_dir) == 1


def test_rotate_deduplicates_repeated_pages(sample_pdf: Path, workdir: Path):
    result = RotateTool().run(
        [sample_pdf],
        {"angle": 90, "page_range": "1,1"},
        _tool_dir(workdir, "rotate_dedup"),
    )

    assert result.meta["rotated_pages"] == 1
    with pikepdf.open(result.output_files[0]) as pdf:
        assert int(pdf.pages[0].obj.get("/Rotate", 0)) == 90



def test_split_manifest_reports_pdf_outputs():
    assert SplitTool().manifest().outputs.type == "pdf"


def test_deskew_reports_migration_to_ocr_tool(sample_pdf: Path, workdir: Path):
    with pytest.raises(ToolError, match="ocr"):
        DeskewTool().run(
            [sample_pdf],
            {"page_range": "1", "min_angle": 0.5},
            _tool_dir(workdir, "deskew_ignore_orientation"),
        )


def test_header_footer_uses_cjk_font_for_chinese_text(sample_pdf: Path, workdir: Path):
    result = HeaderFooterTool().run(
        [sample_pdf],
        {"header": "中文页眉", "footer": "第 {page} / {total} 页", "page_range": "1"},
        _tool_dir(workdir, "header_footer_cjk"),
    )

    output_bytes = result.output_files[0].read_bytes()
    assert b"STSong-Light" in output_bytes


def test_message_named_artifact_paths_prefer_explicitly_referenced_history_file(tmp_path: Path):
    conversation_dir = tmp_path / "conv-1"
    (conversation_dir / "step_1").mkdir(parents=True)
    (conversation_dir / "step_2").mkdir(parents=True)

    old_artifact = conversation_dir / "step_1" / "report_已自动旋转.pdf"
    newer_artifact = conversation_dir / "step_2" / "report_已自动旋转.pdf"
    other_artifact = conversation_dir / "step_2" / "report_已加页眉页脚.pdf"
    old_artifact.write_bytes(b"old")
    newer_artifact.write_bytes(b"new")
    other_artifact.write_bytes(b"other")

    message = "用 report_已自动旋转.pdf 重新加页眉页脚，不要页码"
    resolved = _resolve_message_named_artifact_paths(conversation_dir, message)

    assert resolved == ["step_2/report_已自动旋转.pdf"]
