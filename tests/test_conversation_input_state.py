from pathlib import Path

from pdf_agent.api.agent import _build_message_input_state


def test_build_message_input_state_preserves_current_files_when_no_new_selection():
    state = _build_message_input_state(
        message="给第一页加水印",
        human_message_kwargs={},
        conversation_workdir=Path("/tmp/conversation"),
        conversation_id="conversation-1",
        selected_inputs=[],
    )

    assert "files" not in state
    assert "current_files" not in state
    assert state["configurable"] == {"thread_id": "conversation-1"}


def test_build_message_input_state_overrides_current_files_when_selection_exists():
    selected_inputs = [
        {
            "file_id": "artifact:step_1/output.pdf",
            "path": "/tmp/conversation/step_1/output.pdf",
            "orig_name": "output.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "source": "artifact",
        }
    ]

    state = _build_message_input_state(
        message="给第一页加水印",
        human_message_kwargs={},
        conversation_workdir=Path("/tmp/conversation"),
        conversation_id="conversation-1",
        selected_inputs=selected_inputs,
    )

    assert state["files"] == selected_inputs
    assert state["current_files"] == ["/tmp/conversation/step_1/output.pdf"]
