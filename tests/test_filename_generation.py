from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pdf_agent.tools._builtins.pdf_to_office import PdfToExcelTool, PdfToPptTool
from pdf_agent.tools._builtins.pdf_to_text import PdfToTextTool
from pdf_agent.tools._builtins.pdf_to_word import PdfToWordTool
from pdf_agent.tools.filenames import canonical_source_stem, localized_output_name, localized_sequence_name


def test_canonical_source_stem_strips_chained_generated_suffixes():
    source = Path("GA算法说明_按范围拆分_0001_已合并_已加文字水印_已加文字水印.pdf")

    assert canonical_source_stem(source) == "GA算法说明"
    assert localized_output_name(source, "已压缩") == "GA算法说明_已压缩.pdf"


def test_localized_sequence_name_preserves_original_underscores():
    source = Path("项目_A_v2_最终稿.pdf")

    assert canonical_source_stem(source) == "项目_A_v2_最终稿"
    assert localized_sequence_name(source, "按范围拆分", 2) == "项目_A_v2_最终稿_按范围拆分_0002.pdf"


def test_pdf_to_text_uses_canonical_localized_name(sample_pdf: Path, tmp_path: Path, workdir: Path):
    source = tmp_path / "GA算法说明_按范围拆分_0001_已合并.pdf"
    source.write_bytes(sample_pdf.read_bytes())

    result = PdfToTextTool().run([source], {"page_range": "1"}, workdir)

    assert result.output_files[0].name == "GA算法说明_提取文本.txt"


@pytest.mark.parametrize(
    ("tool_cls", "expected_name"),
    [
        (PdfToWordTool, "GA算法说明_转Word.docx"),
        (PdfToExcelTool, "GA算法说明_转Excel.xlsx"),
        (PdfToPptTool, "GA算法说明_转PPT.pptx"),
    ],
)
def test_conversion_tools_use_canonical_localized_names(
    tool_cls,
    expected_name: str,
    sample_pdf: Path,
    tmp_path: Path,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "GA算法说明_按范围拆分_0002_已合并.pdf"
    source.write_bytes(sample_pdf.read_bytes())

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    def _stub_convert(_input_path: Path, output_path: Path) -> None:
        output_path.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(tool_cls, "_fallback_convert", staticmethod(_stub_convert))

    result = tool_cls().run([source], {}, workdir)

    assert result.output_files[0].name == expected_name
