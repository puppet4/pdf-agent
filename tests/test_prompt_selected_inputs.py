from langchain_core.messages import HumanMessage

from pdf_agent.agent.prompt import prepare_messages_for_model


def test_prepare_messages_for_model_injects_selected_inputs_and_hints():
    messages = [
        HumanMessage(
            content="添加水印，水印内容为测试水印",
            additional_kwargs={
                "selected_inputs": [
                    {
                        "name": "GA算法说明_按范围拆分_0001_已合并.pdf",
                        "source": "artifact",
                        "type": "application/pdf",
                    }
                ],
                "normalized_intent_hints": "watermark_text text=测试水印 page_range=1",
            },
        )
    ]

    prepared = prepare_messages_for_model(messages)

    assert len(prepared) == 1
    content = prepared[0].content
    assert "添加水印，水印内容为测试水印" in content
    assert "[Selected input files for this turn]" in content
    assert "GA算法说明_按范围拆分_0001_已合并.pdf" in content
    assert "Do not ask the user to re-upload or re-select them" in content
    assert "[Normalized intent hints]" in content
    assert "watermark_text text=测试水印 page_range=1" in content
