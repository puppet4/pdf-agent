"""Tests for heuristic intent normalization."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from pdf_agent.agent.intent_hints import build_intent_hints
from pdf_agent.agent.prompt import prepare_messages_for_model
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
