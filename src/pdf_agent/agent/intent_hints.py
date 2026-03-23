"""Heuristic intent normalization for colloquial user requests."""
from __future__ import annotations

import re

from pdf_agent.agent.state import FileInfo

_CN_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_PAGE_RANGE_TOOLS = {
    "rotate",
    "extract",
    "delete",
    "watermark_text",
    "watermark_image",
    "stamp",
    "header_footer",
    "add_page_numbers",
    "barcode",
    "qr_code",
    "ocr",
    "crop",
    "resize",
    "deskew",
    "redact",
}
_WATERMARKED_NAME_RE = re.compile(r"_(已加文字水印|已加图片水印)(?:\.[^.]+)?$", re.IGNORECASE)
_EXPLICIT_HINTS_RE = re.compile(r"\[Normalized intent hints\]\s*(?P<body>(?:\n?- .+)+)", re.IGNORECASE)


def build_intent_hints(message: str, selected_inputs: list[FileInfo] | None = None) -> str | None:
    """Build structured hints for natural-language PDF operations."""
    explicit_hints = _extract_explicit_hint_block(message)
    if explicit_hints:
        return explicit_hints

    text = " ".join((message or "").strip().split())
    if not text:
        return None

    total_pages = _infer_total_pages(selected_inputs or [])
    hints: list[str] = []

    split_hints = _build_split_hints(text, total_pages)
    if split_hints:
        hints.extend(split_hints)

    preferred_tool = _detect_preferred_tool(text)
    if preferred_tool and not any(line == f"- preferred_tool: {preferred_tool}" for line in hints):
        hints.append(f"- preferred_tool: {preferred_tool}")

    if preferred_tool in _PAGE_RANGE_TOOLS:
        page_range = _infer_page_range(text, total_pages)
        if page_range:
            hints.append(f"- page_range: {page_range}")

    if preferred_tool == "rotate":
        angle = _infer_rotation_angle(text)
        if angle is not None:
            hints.append(f"- angle: {angle}")

    watermark_safety_hints = _build_watermark_safety_hints(text, selected_inputs or [], preferred_tool)
    if watermark_safety_hints:
        hints.extend(watermark_safety_hints)

    deduped = list(dict.fromkeys(hints))
    return "\n".join(deduped) if deduped else None


def _extract_explicit_hint_block(message: str) -> str | None:
    if not message:
        return None
    match = _EXPLICIT_HINTS_RE.search(message)
    if not match:
        return None
    body = match.group("body")
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("- ")]
    if not lines:
        return None
    return "\n".join(lines)


def _infer_total_pages(selected_inputs: list[FileInfo]) -> int | None:
    pdfs = [item for item in selected_inputs if item.get("mime_type") == "application/pdf" and item.get("page_count")]
    if len(pdfs) == 1:
        pages = pdfs[0].get("page_count")
        return int(pages) if isinstance(pages, int) and pages > 0 else None
    return None


def _detect_preferred_tool(text: str) -> str | None:
    ordered_rules = [
        ("split", ["拆分", "拆开", "分成", "拆成", "分拆", "拆为"]),
        ("merge", ["合并", "拼接", "合成一个"]),
        ("compress", ["压缩", "缩小", "减小体积", "变小一点"]),
        ("rotate", ["旋转", "转正", "顺时针", "逆时针", "左转", "右转", "横过来", "倒过来"]),
        ("add_page_numbers", ["页码"]),
        ("header_footer", ["页眉", "页脚"]),
        ("watermark_text", ["水印"]),
        ("delete", ["删除", "删掉", "去掉"]),
        ("extract", ["提取", "抽取"]),
        ("ocr", ["ocr", "文字识别", "识别文字", "扫描件转文字"]),
        ("metadata_info", ["元数据", "基本信息", "文档信息"]),
        ("remove_metadata", ["删除元数据", "清除元数据"]),
        ("set_metadata", ["修改元数据", "设置作者", "设置标题"]),
        ("pdf_to_word", ["转word", "转成word", "导出word"]),
        ("pdf_to_excel", ["转excel", "转成excel", "导出excel"]),
        ("pdf_to_ppt", ["转ppt", "转成ppt", "导出ppt"]),
        ("pdf_to_text", ["提取文字", "导出文字", "转txt", "转文本"]),
        ("pdf_to_markdown", ["转markdown", "导出markdown"]),
        ("pdf_to_html", ["转html", "导出html"]),
        ("pdf_to_images", ["转图片", "导出图片"]),
    ]
    lowered = text.lower()
    for tool, keywords in ordered_rules:
        if any(keyword in lowered for keyword in keywords):
            return tool
    return None


def _build_split_hints(text: str, total_pages: int | None) -> list[str]:
    if not any(keyword in text for keyword in ("拆分", "拆开", "分成", "拆成", "分拆", "拆为")):
        return []

    hints = ["- preferred_tool: split"]
    if "按书签" in text:
        hints.append("- mode: bookmark")
        return hints

    chunk_match = re.search(r"每\s*([0-9一二两三四五六七八九十百]+)\s*页\s*(一个|一份|一组)", text)
    if chunk_match:
        chunk_size = _parse_int(chunk_match.group(1))
        if chunk_size == 1:
            hints.append("- mode: each_page")
        elif chunk_size and chunk_size > 1:
            hints.extend(["- mode: chunk", f"- chunk_size: {chunk_size}"])
        return hints

    if "每页一个" in text:
        hints.append("- mode: each_page")
        return hints

    raw_groups = re.findall(r"([第前后末最尾全奇偶一二两三四五六七八九十百0-9,，、到至\-~和]+)页", text)
    normalized_groups = [
        _infer_page_range(raw_group, total_pages, allow_compact_digits=True)
        for raw_group in raw_groups
    ]
    normalized_groups = [group for group in normalized_groups if group]
    if len(normalized_groups) >= 2:
        hints.extend(["- mode: range", f"- page_groups: {'|'.join(normalized_groups)}"])
        return hints

    page_range = _infer_page_range(text, total_pages, allow_compact_digits=True)
    if page_range:
        hints.extend(["- mode: range", f"- page_range: {page_range}"])
    return hints


def _infer_page_range(text: str, total_pages: int | None, *, allow_compact_digits: bool = False) -> str | None:
    compact = text.replace(" ", "")
    lowered = compact.lower()

    if any(token in lowered for token in ("all", "全部", "所有页", "整本", "整个文档", "全都", "每页", "全部页面")):
        return "all"
    if "奇数页" in lowered:
        return "odd"
    if "偶数页" in lowered:
        return "even"
    if any(token in lowered for token in ("第一页", "首页", "封面")):
        return "1"

    front_match = re.search(r"前([0-9一二两三四五六七八九十百]+)页", compact)
    if front_match:
        count = _parse_int(front_match.group(1))
        if count and count >= 1:
            return f"1-{count}" if count > 1 else "1"

    if total_pages:
        back_match = re.search(r"后([0-9一二两三四五六七八九十百]+)页", compact)
        if back_match:
            count = _parse_int(back_match.group(1))
            if count and count >= 1:
                start = max(1, total_pages - count + 1)
                return f"{start}-{total_pages}" if start != total_pages else str(total_pages)
        if any(token in lowered for token in ("最后一页", "末页", "尾页")):
            return str(total_pages)

    range_match = re.search(
        r"第?([0-9一二两三四五六七八九十百]+)\s*[到至\-~]\s*([0-9一二两三四五六七八九十百]+)页?",
        compact,
    )
    if range_match:
        start = _parse_int(range_match.group(1))
        end = _parse_int(range_match.group(2))
        if start and end:
            return f"{start}-{end}"

    parts = re.split(r"[，,、和]", compact.replace("第", "").replace("页", ""))
    normalized_parts: list[str] = []
    for part in parts:
        token = part.strip()
        if not token:
            continue
        normalized = _normalize_single_page_token(token, total_pages, allow_compact_digits=allow_compact_digits)
        if normalized:
            normalized_parts.append(normalized)
    if normalized_parts:
        return ",".join(normalized_parts)
    return None


def _normalize_single_page_token(token: str, total_pages: int | None, *, allow_compact_digits: bool) -> str | None:
    if token in {"all", "odd", "even"}:
        return token
    if any(sep in token for sep in ("-", "~", "到", "至")):
        range_match = re.fullmatch(r"([0-9一二两三四五六七八九十百]+)\s*[-~到至]\s*([0-9一二两三四五六七八九十百]+)", token)
        if not range_match:
            return None
        start = _parse_int(range_match.group(1))
        end = _parse_int(range_match.group(2))
        if start and end:
            return f"{start}-{end}"
        return None

    if token.isdigit():
        if allow_compact_digits and total_pages and len(token) > 1 and int(token) > total_pages and all(ch != "0" for ch in token):
            return ",".join(token)
        return token

    value = _parse_int(token)
    return str(value) if value is not None else None


def _infer_rotation_angle(text: str) -> int | None:
    compact = text.replace(" ", "")
    if "180" in compact or "倒过来" in compact or "颠倒" in compact:
        return 180
    if "270" in compact:
        return 270
    if "90" in compact:
        if any(token in compact for token in ("逆时针", "左转", "向左转")):
            return 270
        return 90
    return None


def _build_watermark_safety_hints(
    text: str,
    selected_inputs: list[FileInfo],
    preferred_tool: str | None,
) -> list[str]:
    if preferred_tool not in {"watermark_text", "watermark_image"}:
        return []

    normalized = text.replace(" ", "")
    if not any(token in normalized for token in ("换成", "改成", "重做", "重新", "替换", "不要之前", "去掉之前")):
        return []

    selected_names = [
        str(item.get("orig_name") or "")
        for item in selected_inputs
        if isinstance(item, dict)
    ]
    if not any(_WATERMARKED_NAME_RE.search(name) for name in selected_names):
        return []

    return [
        "- watermark_replacement_requested: true",
        "- watermark_safety: selected input already appears watermarked; do not stack a new watermark onto it",
        "- watermark_safety: explain that existing exported watermarks cannot be automatically removed; ask the user to select a clean pre-watermark source file before re-adding a new watermark",
    ]


def _parse_int(text: str) -> int | None:
    token = text.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_NUMBERS.get(left, 1 if left == "" else None)
        ones = _CN_NUMBERS.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(token) == 1:
        return _CN_NUMBERS.get(token)
    return None
