"""Tests for heuristic intent normalization."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from pdf_agent.agent.intent_hints import (
    _build_split_hints,
    _detect_preferred_tool,
    _extract_explicit_hint_block,
    _infer_page_range,
    _infer_rotation_angle,
    _infer_total_pages,
    _normalize_single_page_token,
    _parse_int,
    build_intent_hints,
)
from pdf_agent.agent.prompt import build_system_prompt, prepare_messages_for_model
from pdf_agent.agent.state import FileInfo


def _pdf_input(page_count: int) -> list[FileInfo]:
    return [{
        "file_id": "1",
        "path": "/tmp/sample.pdf",
        "orig_name": "sample.pdf",
        "mime_type": "application/pdf",
        "page_count": page_count,
        "source": "upload",
    }]


def test_build_intent_hints_normalizes_split_groups_from_colloquial_input():
    hints = build_intent_hints("帮我分成二个，1页、23页一个", _pdf_input(3))

    assert hints is not None
    assert "- preferred_tool: split" in hints
    assert "- mode: range" in hints
    assert "- page_groups: 1|2,3" in hints


def test_build_intent_hints_resolves_last_page_and_rotation():
    hints = build_intent_hints("把前两页顺时针旋转90度，最后一页不用动", _pdf_input(5))

    assert hints is not None
    assert "- preferred_tool: rotate" in hints
    assert "- page_range: 1-2" in hints
    assert "- angle: 90" in hints


def test_prepare_messages_for_model_injects_hidden_normalized_hints():
    messages = [
        HumanMessage(
            content="帮我删掉最后一页",
            additional_kwargs={"normalized_intent_hints": "- preferred_tool: delete\n- page_range: 5"},
        )
    ]

    prepared = prepare_messages_for_model(messages)

    assert len(prepared) == 1
    assert prepared[0].content.endswith("[Normalized intent hints]\n- preferred_tool: delete\n- page_range: 5")


def test_build_intent_hints_warns_against_watermark_stacking_on_watermarked_input():
    selected_inputs = [{
        "file_id": "artifact:step_2/GA算法说明_已加文字水印.pdf",
        "path": "/tmp/conversation/step_2/GA算法说明_已加文字水印.pdf",
        "orig_name": "GA算法说明_已加文字水印.pdf",
        "mime_type": "application/pdf",
        "page_count": 3,
        "source": "artifact",
    }]

    hints = build_intent_hints("换成红色的水印，内容为机密文件", selected_inputs)

    assert hints is not None
    assert "- preferred_tool: watermark_text" in hints
    assert "- watermark_replacement_requested: true" in hints
    assert "do not stack a new watermark onto it" in hints


def test_explicit_hint_block_and_blank_messages_short_circuit():
    assert build_intent_hints("   ", _pdf_input(1)) is None
    assert _extract_explicit_hint_block("") is None
    assert _extract_explicit_hint_block("hello") is None
    assert _extract_explicit_hint_block("[Normalized intent hints]\nplain text only") is None
    assert _extract_explicit_hint_block("[Normalized intent hints]\n- preferred_tool: rotate\n- angle: 90") == (
        "- preferred_tool: rotate\n- angle: 90"
    )
    assert build_intent_hints("[Normalized intent hints]\n- preferred_tool: delete\nplain text") == (
        "- preferred_tool: delete"
    )


def test_intent_tool_detection_and_total_page_inference_edges():
    assert _infer_total_pages([]) is None
    assert _infer_total_pages(_pdf_input(0)) is None
    assert _infer_total_pages(_pdf_input(4)) == 4
    assert _infer_total_pages(_pdf_input(4) + _pdf_input(5)) is None

    expectations = {
        "合并这些文件": "merge",
        "帮我压缩一下": "compress",
        "加页码": "add_page_numbers",
        "加页眉": "header_footer",
        "删除第1页": "delete",
        "提取第2页": "extract",
        "扫描件转文字": "ocr",
        "看下元数据": "metadata_info",
        "清除元数据": "remove_metadata",
        "设置作者": "set_metadata",
        "转成word": "pdf_to_word",
        "转excel": "pdf_to_excel",
        "转ppt": "pdf_to_ppt",
        "转txt": "pdf_to_text",
        "转markdown": "pdf_to_markdown",
        "转html": "pdf_to_html",
        "转图片": "pdf_to_images",
    }
    for text, tool in expectations.items():
        assert _detect_preferred_tool(text) == tool
    assert _detect_preferred_tool("只是聊天") is None


def test_split_and_page_range_hints_cover_colloquial_edges():
    assert _build_split_hints("按书签拆分", 9) == ["- preferred_tool: split", "- mode: bookmark"]
    assert _build_split_hints("每一页一个拆开", 9) == ["- preferred_tool: split", "- mode: each_page"]
    assert _build_split_hints("每3页一组拆分", 9) == ["- preferred_tool: split", "- mode: chunk", "- chunk_size: 3"]
    assert _build_split_hints("把第1到2页拆出来", 9) == [
        "- preferred_tool: split",
        "- mode: range",
        "- page_range: 1-2",
    ]
    assert _build_split_hints("不要拆", 9) == []

    assert _infer_page_range("全部页面", 5) == "all"
    assert _infer_page_range("奇数页", 5) == "odd"
    assert _infer_page_range("偶数页", 5) == "even"
    assert _infer_page_range("封面", 5) == "1"
    assert _infer_page_range("前一页", 5) == "1"
    assert _infer_page_range("前三页", 5) == "1-3"
    assert _infer_page_range("后两页", 5) == "4-5"
    assert _infer_page_range("后一页", 5) == "5"
    assert _infer_page_range("最后一页", 5) == "5"
    assert _infer_page_range("最后一页", None) is None
    assert _infer_page_range("第2至4页", 5) == "2-4"
    assert _infer_page_range("第1、三页", 5) == "1,3"
    assert _infer_page_range("23页", 5, allow_compact_digits=True) == "2,3"
    assert _infer_page_range("23页", None, allow_compact_digits=True) == "23"
    assert _normalize_single_page_token("一-三", 5, allow_compact_digits=False) == "1-3"
    assert _normalize_single_page_token("一-bad", 5, allow_compact_digits=False) is None
    assert _normalize_single_page_token("bad-range", 5, allow_compact_digits=False) is None


def test_rotation_watermark_and_parse_int_edges():
    assert _infer_rotation_angle("倒过来") == 180
    assert _infer_rotation_angle("转270度") == 270
    assert _infer_rotation_angle("逆时针90度") == 270
    assert _infer_rotation_angle("右转90度") == 90
    assert _infer_rotation_angle("旋转一下") is None

    assert build_intent_hints("换成红色水印", _pdf_input(2)) == "- preferred_tool: watermark_text"
    assert "- page_range: even" in (build_intent_hints("给偶数页加水印", _pdf_input(4)) or "")
    assert build_intent_hints("删除最后一页", _pdf_input(4)) == "- preferred_tool: delete\n- page_range: 4"

    assert _parse_int("") is None
    assert _parse_int("十") == 10
    assert _parse_int("十二") == 12
    assert _parse_int("二十") == 20
    assert _parse_int("二十五") == 25
    assert _parse_int("百") is None
    assert _parse_int("二百") is None


def test_build_system_prompt_lists_files_and_empty_active_state():
    prompt = build_system_prompt(_pdf_input(2), ["/tmp/sample.pdf"])
    assert "Files in this conversation" in prompt
    assert "sample.pdf" in prompt
    assert "Current active files" in prompt

    empty_prompt = build_system_prompt([], [])
    assert "No active files yet" in empty_prompt


def test_prepare_messages_for_model_preserves_non_human_and_ignores_empty_selected_items():
    messages = [
        HumanMessage(
            content=["not", "string"],
            additional_kwargs={
                "selected_inputs": [{"name": ""}, "bad"],
                "normalized_intent_hints": "  ",
            },
        )
    ]

    prepared = prepare_messages_for_model(messages)

    assert prepared[0].content == ["not", "string"]

    converted = prepare_messages_for_model([
        HumanMessage(
            content=["not", "string"],
            additional_kwargs={"normalized_intent_hints": "- preferred_tool: rotate"},
        )
    ])
    assert "['not', 'string']" in converted[0].content
    assert "- preferred_tool: rotate" in converted[0].content
