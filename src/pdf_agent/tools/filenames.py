"""Helpers for user-facing artifact filenames."""
from __future__ import annotations

import re
from pathlib import Path

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')
_WHITESPACE_RE = re.compile(r"\s+")
_GENERATED_SUFFIX_PATTERNS = (
    re.compile(r"_按范围拆分(?:_\d{4})?$"),
    re.compile(r"_分块_\d{4}$"),
    re.compile(r"_第\d{4}页$"),
    re.compile(r"_已填写表单_中间文件$"),
    re.compile(r"_归档版_PDFA-[A-Za-z0-9]+$"),
    re.compile(r"_(?:图片合成|多页合一|小册子版|差异对比|快速查看版|转PDF|转Word|转Excel|转PPT|转HTML|转Markdown|提取文本|页面图片包)$"),
    re.compile(
        r"_(?:已OCR|已修复|已倒序页面|已删除空白页|已删除页面|已加二维码|已加图片水印|已加密|"
        r"已加文字水印|已加条形码|已加页眉页脚|已加页码|已加页面边框|已压缩|已合并|已填写表单|"
        r"已扁平化|已拼接页面|已提取页面|已插入空白页|已旋转|已更新元数据|已校正倾斜|已盖章|"
        r"已移除元数据|已签名|已脱敏|已自动旋转|已裁剪|已解密|已调整尺寸|已重排页面)$"
    ),
)


def sanitize_filename_part(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", value).strip()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip(" .")
    return cleaned or "文件"


def canonical_source_stem(source: Path) -> str:
    stem = sanitize_filename_part(source.stem)
    previous = None
    while stem and stem != previous:
        previous = stem
        for pattern in _GENERATED_SUFFIX_PATTERNS:
            candidate = pattern.sub("", stem)
            if candidate != stem:
                stem = candidate.strip(" _.")
                break
    return stem or "文件"


def localized_output_name(source: Path, suffix: str, ext: str = ".pdf") -> str:
    stem = canonical_source_stem(source)
    return f"{stem}_{sanitize_filename_part(suffix)}{ext}"


def localized_sequence_name(source: Path, suffix: str, index: int, ext: str = ".pdf") -> str:
    stem = canonical_source_stem(source)
    return f"{stem}_{sanitize_filename_part(suffix)}_{index:04d}{ext}"
