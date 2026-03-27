"""Guard tests for LibreOffice-backed conversions and fallback transparency."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_agent.tools._builtins.office_to_pdf import OfficeToPdfTool
from pdf_agent.tools._builtins.pdf_to_office import PdfToExcelTool, PdfToPptTool
from pdf_agent.tools._builtins.pdf_to_word import PdfToWordTool
from pdf_agent.tools.libreoffice import build_libreoffice_command


def test_build_libreoffice_command_uses_isolated_profile_uri(tmp_path: Path):
    command = build_libreoffice_command(
        "soffice",
        convert_to="pdf",
        input_path=tmp_path / "input.docx",
        outdir=tmp_path / "out",
        profile_dir=tmp_path / "lo-profile",
    )

    assert command[0] == "soffice"
    assert command[1].startswith("-env:UserInstallation=file://")
    assert command[2:5] == ["--headless", "--convert-to", "pdf"]


def test_pdf_to_word_fallback_reports_engine_and_reason(
    rendered_text_pdf: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pdf_agent.tools._builtins.pdf_to_word.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(
        "pdf_agent.tools._builtins.pdf_to_word.run_libreoffice_conversion",
        lambda *args, **kwargs: (False, "LibreOffice crashed"),
    )

    result = PdfToWordTool().run([rendered_text_pdf], {}, workdir)

    assert result.output_files[0].exists()
    assert result.meta["engine"] == "python-docx"
    assert result.meta["fallback_used"] is True
    assert "LibreOffice crashed" in result.meta["fallback_reason"]
    assert "fallback reason" in result.log


def test_pdf_to_excel_fallback_reports_engine_and_reason(
    rendered_text_pdf: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pdf_agent.tools._builtins.pdf_to_office.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(
        "pdf_agent.tools._builtins.pdf_to_office.run_libreoffice_conversion",
        lambda *args, **kwargs: (False, "LibreOffice failed"),
    )

    result = PdfToExcelTool().run([rendered_text_pdf], {}, workdir)

    assert result.output_files[0].exists()
    assert result.meta["engine"] == "openpyxl"
    assert result.meta["fallback_used"] is True
    assert "LibreOffice failed" in result.meta["fallback_reason"]


def test_pdf_to_ppt_fallback_reports_engine_and_reason(
    rendered_text_pdf: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pdf_agent.tools._builtins.pdf_to_office.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(
        "pdf_agent.tools._builtins.pdf_to_office.run_libreoffice_conversion",
        lambda *args, **kwargs: (False, "LibreOffice failed"),
    )

    result = PdfToPptTool().run([rendered_text_pdf], {}, workdir)

    assert result.output_files[0].exists()
    assert result.meta["engine"] == "python-pptx"
    assert result.meta["fallback_used"] is True
    assert "LibreOffice failed" in result.meta["fallback_reason"]


def test_office_to_pdf_fallback_reports_engine_and_reason(
    sample_docx: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pdf_agent.tools._builtins.office_to_pdf.shutil.which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(
        "pdf_agent.tools._builtins.office_to_pdf.run_libreoffice_conversion",
        lambda *args, **kwargs: (False, "LibreOffice profile lock"),
    )

    result = OfficeToPdfTool().run([sample_docx], {}, workdir)

    assert result.output_files[0].exists()
    assert result.meta["engine"] == "python-fallback"
    assert result.meta["fallback_used"] is True
    assert "profile lock" in result.meta["fallback_reason"]
