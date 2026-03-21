"""Tests for the thin execution API layer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pikepdf
import pytest
from fastapi.testclient import TestClient


class TestExecutionsApiSurface:
    def test_execution_routes_exist_on_api_router(self):
        from pdf_agent.main import app

        paths = {
            (route.path, tuple(sorted(getattr(route, "methods", set()) or [])))
            for route in app.routes
            if hasattr(route, "path")
        }

        assert any(path == "/api/executions" and "POST" in methods for path, methods in paths)
        assert any(path == "/api/executions/{execution_id}" and "GET" in methods for path, methods in paths)
        assert any(path == "/api/executions/{execution_id}/cancel" and "POST" in methods for path, methods in paths)
        assert any(path == "/api/executions/{execution_id}/events" and "GET" in methods for path, methods in paths)
        assert any(path == "/api/executions/{execution_id}/result" and "GET" in methods for path, methods in paths)

    def test_create_execution_accepts_form_mode_and_returns_metadata(self, monkeypatch):
        from pdf_agent.main import app

        created = {
            "id": str(uuid4()),
            "status": "PENDING",
            "mode": "FORM",
            "instruction": None,
            "progress_int": 0,
            "active_tool": None,
            "error_code": None,
            "error_message": None,
            "result_path": None,
            "result_type": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plan": {"steps": [{"tool": "rotate", "params": {"angle": "90", "page_range": "all"}}]},
            "logs": [],
            "outputs": [],
        }

        async def fake_create_execution(*, mode, instruction, steps, file_ids):
            assert mode == "FORM"
            assert instruction is None
            assert file_ids == ["file-1"]
            assert steps == [{"tool": "rotate", "params": {"angle": "90", "page_range": "all"}}]
            return created

        monkeypatch.setattr("pdf_agent.api.executions.create_execution_record", fake_create_execution)

        client = TestClient(app)
        response = client.post(
            "/api/executions",
            json={
                "mode": "FORM",
                "file_ids": ["file-1"],
                "steps": [{"tool": "rotate", "params": {"angle": "90", "page_range": "all"}}],
            },
        )

        assert response.status_code == 201
        assert response.json()["id"] == created["id"]
        assert response.json()["status"] == "PENDING"

    def test_get_execution_returns_status_progress_and_logs(self, monkeypatch):
        from pdf_agent.main import app

        execution_id = str(uuid4())

        async def fake_get_execution(execution_id_arg: str):
            assert execution_id_arg == execution_id
            return {
                "id": execution_id,
                "status": "RUNNING",
                "mode": "FORM",
                "instruction": None,
                "progress_int": 50,
                "active_tool": "compress",
                "error_code": None,
                "error_message": None,
                "result_path": None,
                "result_type": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "plan": {"steps": []},
                "logs": [
                    {"index": 0, "tool": "rotate", "status": "SUCCESS", "log_text": "done"},
                    {"index": 1, "tool": "compress", "status": "RUNNING", "log_text": "running"},
                ],
                "outputs": [],
            }

        monkeypatch.setattr("pdf_agent.api.executions.get_execution_record", fake_get_execution)

        client = TestClient(app)
        response = client.get(f"/api/executions/{execution_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "RUNNING"
        assert payload["progress_int"] == 50
        assert len(payload["logs"]) == 2

    def test_cancel_execution_returns_canceled_state(self, monkeypatch):
        from pdf_agent.main import app

        execution_id = str(uuid4())

        async def fake_cancel_execution(execution_id_arg: str):
            assert execution_id_arg == execution_id
            return {"id": execution_id, "status": "CANCELED"}

        monkeypatch.setattr("pdf_agent.api.executions.cancel_execution_record", fake_cancel_execution)

        client = TestClient(app)
        response = client.post(f"/api/executions/{execution_id}/cancel")

        assert response.status_code == 200
        assert response.json() == {"id": execution_id, "status": "CANCELED"}

    def test_execution_result_returns_file_response_when_output_exists(self, monkeypatch, tmp_path):
        from pdf_agent.main import app

        output = tmp_path / "result.pdf"
        output.write_bytes(b"%PDF-1.4\n%%EOF")
        execution_id = str(uuid4())

        async def fake_get_execution_result(execution_id_arg: str):
            assert execution_id_arg == execution_id
            return SimpleNamespace(path=output, filename="result.pdf", media_type="application/pdf")

        monkeypatch.setattr("pdf_agent.api.executions.get_execution_result_response", fake_get_execution_result)

        client = TestClient(app)
        response = client.get(f"/api/executions/{execution_id}/result")

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")

    def test_execution_events_stream_progress_and_done_events(self, monkeypatch):
        from pdf_agent.main import app

        execution_id = str(uuid4())

        async def fake_execution_events(execution_id_arg: str):
            assert execution_id_arg == execution_id
            yield "event: progress\ndata: {\"progress_int\": 50}\n\n"
            yield "event: done\ndata: {\"status\": \"SUCCESS\"}\n\n"

        monkeypatch.setattr("pdf_agent.api.executions.stream_execution_events", fake_execution_events)

        client = TestClient(app)
        with client.stream("GET", f"/api/executions/{execution_id}/events") as response:
            body = b"".join(response.iter_bytes()).decode("utf-8")

        assert response.status_code == 200
        assert "event: progress" in body
        assert "event: done" in body


class TestExecutionModels:
    def test_db_models_include_execution_record(self):
        from pdf_agent.db import models

        assert hasattr(models, "ExecutionRecord")

    def test_alembic_baseline_contains_execution_table(self):
        content = open("alembic/versions/0001_initial_schema.py", encoding="utf-8").read()

        assert "'executions'" in content
        assert "'job_steps'" not in content
        assert "'artifacts'" not in content


class TestExecutionServiceHelpers:
    def test_create_execution_record_stores_execution_and_schedules_runner(self, monkeypatch):
        import pdf_agent.api.executions as executions_api

        added = []
        scheduled = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def add(self, item):
                added.append(item)

            async def commit(self):
                return None

            async def refresh(self, item):
                return None

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_validate_plan_inputs", lambda plan: asyncio.sleep(0, result=None))
        monkeypatch.setattr(
            executions_api,
            "enqueue_execution",
            lambda **kwargs: scheduled.append(kwargs) or {"backend": "local", "task_id": "task-1", "queue": "light"},
        )

        created = asyncio.run(
            executions_api.create_execution_record(
                mode="FORM",
                instruction=None,
                steps=[{"tool": "rotate", "params": {"angle": "90"}}],
                file_ids=[],
            )
        )

        assert created["status"] == "PENDING"
        assert len(added) == 1
        assert len(scheduled) == 1

    def test_cancel_execution_record_marks_execution_canceled_and_cancels_running_task(self, monkeypatch):
        import pdf_agent.api.executions as executions_api
        from pdf_agent.db.models import ExecutionRecord

        execution = ExecutionRecord(id=uuid4(), mode="FORM", status="RUNNING", progress_int=25)
        task_canceled = {"value": False}

        class FakeTask:
            def cancel(self):
                task_canceled["value"] = True

        executions_api._execution_tasks[str(execution.id)] = FakeTask()

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def commit(self):
                return None

        async def fake_load_execution(session, execution_id):
            return execution

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)
        monkeypatch.setattr(
            executions_api,
            "cancel_enqueued_execution",
            lambda execution_id: executions_api._execution_tasks.pop(execution_id).cancel() or {"terminated_processes": 0},
        )

        payload = asyncio.run(executions_api.cancel_execution_record(str(execution.id)))

        assert payload["status"] == "CANCELED"
        assert execution.status == "CANCELED"
        assert task_canceled["value"] is True

    def test_cancel_execution_record_keeps_terminal_state_unchanged(self, monkeypatch):
        import pdf_agent.api.executions as executions_api
        from pdf_agent.db.models import ExecutionRecord

        execution = ExecutionRecord(id=uuid4(), mode="FORM", status="SUCCESS", progress_int=100)
        calls = {"cancel": 0, "emit": 0}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def fake_load_execution(session, execution_id):
            return execution

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)
        monkeypatch.setattr(
            executions_api,
            "cancel_enqueued_execution",
            lambda execution_id: calls.__setitem__("cancel", calls["cancel"] + 1) or {},
        )
        monkeypatch.setattr(
            executions_api,
            "_emit_execution_event",
            lambda *args, **kwargs: calls.__setitem__("emit", calls["emit"] + 1),
        )

        payload = asyncio.run(executions_api.cancel_execution_record(str(execution.id)))

        assert payload["status"] == "SUCCESS"
        assert execution.status == "SUCCESS"
        assert calls == {"cancel": 0, "emit": 0}

    def test_cancel_pending_execution_record_discards_queue_slot(self, monkeypatch):
        import pdf_agent.api.executions as executions_api
        from pdf_agent.db.models import ExecutionRecord

        execution = ExecutionRecord(
            id=uuid4(),
            mode="FORM",
            status="PENDING",
            progress_int=0,
            plan_json={"queue_name": "heavy"},
        )
        discarded = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def commit(self):
                return None

        async def fake_load_execution(session, execution_id):
            return execution

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)
        monkeypatch.setattr(executions_api, "discard_queued_execution", lambda queue_name: discarded.append(queue_name))
        monkeypatch.setattr(executions_api, "cancel_enqueued_execution", lambda execution_id: {"terminated_processes": 0})
        monkeypatch.setattr(executions_api, "_emit_execution_event", lambda *args, **kwargs: None)

        payload = asyncio.run(executions_api.cancel_execution_record(str(execution.id)))

        assert payload["status"] == "CANCELED"
        assert execution.status == "CANCELED"
        assert discarded == ["heavy"]

    def test_get_execution_result_response_returns_file_metadata(self, monkeypatch, tmp_path):
        import pdf_agent.api.executions as executions_api
        from pdf_agent.db.models import ExecutionRecord

        output = tmp_path / "result.pdf"
        output.write_bytes(b"%PDF-1.4\n%%EOF")

        execution = ExecutionRecord(id=uuid4(), mode="FORM", status="SUCCESS", result_path=str(output), result_type="pdf")

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def fake_load_execution(session, execution_id):
            return execution

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)

        result = asyncio.run(executions_api.get_execution_result_response(str(execution.id)))

        assert result.filename == "result.pdf"
        assert result.media_type == "application/pdf"


class TestExecutionRuntimeSmoke:
    def test_execution_runtime_completes_real_tool_run(self, monkeypatch, tmp_path, sample_pdf: Path):
        import pdf_agent.api.executions as executions_api
        import pdf_agent.execution_queue as execution_queue
        from pdf_agent.db.models import ExecutionRecord
        from pdf_agent.tools._builtins.rotate import RotateTool
        from pdf_agent.tools.registry import ToolRegistry

        file_id = str(uuid4())
        file_store = {file_id: sample_pdf}
        execution_store: dict[str, ExecutionRecord] = {}

        registry = ToolRegistry()
        registry.register(RotateTool())

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def add(self, item):
                if item.id is None:
                    item.id = uuid4()
                if item.created_at is None:
                    item.created_at = datetime.now(timezone.utc)
                item.updated_at = datetime.now(timezone.utc)
                execution_store[str(item.id)] = item

            async def commit(self):
                for execution in execution_store.values():
                    execution.updated_at = datetime.now(timezone.utc)

            async def refresh(self, item):
                item.updated_at = datetime.now(timezone.utc)

        async def fake_validate_plan_inputs(plan):
            return None

        async def fake_load_execution(session, execution_id):
            return execution_store[execution_id]

        async def fake_resolve_step_inputs(session, input_refs, prev_outputs):
            resolved = []
            for input_ref in input_refs:
                if input_ref.get("type") == "prev":
                    resolved.extend(prev_outputs)
                elif input_ref.get("type") == "file":
                    resolved.append(file_store[str(input_ref["file_id"])])
            return resolved

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_validate_plan_inputs", fake_validate_plan_inputs)
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)
        monkeypatch.setattr(executions_api, "_resolve_step_inputs", fake_resolve_step_inputs)
        monkeypatch.setattr(executions_api, "registry", registry)
        monkeypatch.setattr(executions_api.settings, "data_dir", tmp_path)

        execution_queue._local_tasks.clear()
        execution_queue._celery_task_ids.clear()
        execution_queue._queued_counts.clear()
        execution_queue._queued_counts.update({"light": 0, "heavy": 0})
        executions_api._execution_event_buffers.clear()
        executions_api._execution_event_queues.clear()

        async def scenario():
            created = await executions_api.create_execution_record(
                mode="FORM",
                instruction="rotate sample pdf",
                steps=[{"tool": "rotate", "params": {"angle": "90", "page_range": "all"}}],
                file_ids=[file_id],
            )
            final = await executions_api.wait_for_execution_terminal(created["id"], timeout=5)
            result = await executions_api.get_execution_result_response(created["id"])
            return created, final, result

        created, final, result = asyncio.run(scenario())

        assert created["status"] == "PENDING"
        assert final["status"] == "SUCCESS"
        assert final["progress_int"] == 100
        assert len(final["outputs"]) == 1
        assert Path(result.path).exists()
        with pikepdf.open(result.path) as pdf:
            assert len(pdf.pages) == 5

    def test_execution_runtime_cancels_long_running_process(self, monkeypatch, tmp_path, sample_pdf: Path):
        import pdf_agent.api.executions as executions_api
        import pdf_agent.execution_queue as execution_queue
        import pdf_agent.external_commands as external_commands
        from pdf_agent.db.models import ExecutionRecord
        from pdf_agent.external_commands import run_command
        from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
        from pdf_agent.tools.base import BaseTool, ToolResult
        from pdf_agent.tools.registry import ToolRegistry

        class SleepTool(BaseTool):
            def manifest(self) -> ToolManifest:
                return ToolManifest(
                    name="sleepy",
                    label="Sleepy",
                    category="test",
                    description="Long-running test tool",
                    inputs=ToolInputSpec(min=1, max=1),
                    outputs=ToolOutputSpec(type="pdf"),
                    params=[],
                    async_hint=True,
                )

            def validate(self, params: dict) -> dict:
                return {}

            def run(self, inputs, params, workdir, reporter=None) -> ToolResult:
                if reporter:
                    reporter(5, "starting long-running subprocess")
                run_command([sys.executable, "-c", "import time; time.sleep(30)"])
                return ToolResult(output_files=[inputs[0]], log="unexpected completion")

        file_id = str(uuid4())
        file_store = {file_id: sample_pdf}
        execution_store: dict[str, ExecutionRecord] = {}

        registry = ToolRegistry()
        registry.register(SleepTool())

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def add(self, item):
                if item.id is None:
                    item.id = uuid4()
                if item.created_at is None:
                    item.created_at = datetime.now(timezone.utc)
                item.updated_at = datetime.now(timezone.utc)
                execution_store[str(item.id)] = item

            async def commit(self):
                for execution in execution_store.values():
                    execution.updated_at = datetime.now(timezone.utc)

            async def refresh(self, item):
                item.updated_at = datetime.now(timezone.utc)

        async def fake_validate_plan_inputs(plan):
            return None

        async def fake_load_execution(session, execution_id):
            return execution_store[execution_id]

        async def fake_resolve_step_inputs(session, input_refs, prev_outputs):
            resolved = []
            for input_ref in input_refs:
                if input_ref.get("type") == "prev":
                    resolved.extend(prev_outputs)
                elif input_ref.get("type") == "file":
                    resolved.append(file_store[str(input_ref["file_id"])])
            return resolved

        async def wait_until(predicate, timeout=5):
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if predicate():
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("Timed out waiting for predicate")

        monkeypatch.setattr(executions_api, "async_session_factory", lambda: FakeSession())
        monkeypatch.setattr(executions_api, "_validate_plan_inputs", fake_validate_plan_inputs)
        monkeypatch.setattr(executions_api, "_load_execution", fake_load_execution)
        monkeypatch.setattr(executions_api, "_resolve_step_inputs", fake_resolve_step_inputs)
        monkeypatch.setattr(executions_api, "registry", registry)
        monkeypatch.setattr(executions_api.settings, "data_dir", tmp_path)

        execution_queue._local_tasks.clear()
        execution_queue._celery_task_ids.clear()
        execution_queue._queued_counts.clear()
        execution_queue._queued_counts.update({"light": 0, "heavy": 0})
        executions_api._execution_event_buffers.clear()
        executions_api._execution_event_queues.clear()
        external_commands._job_processes.clear()

        async def scenario():
            created = await executions_api.create_execution_record(
                mode="FORM",
                instruction="cancel sleepy tool",
                steps=[{"tool": "sleepy", "params": {}}],
                file_ids=[file_id],
            )
            execution_id = created["id"]

            await wait_until(lambda: execution_store[execution_id].status == "RUNNING")
            await wait_until(lambda: bool(external_commands._job_processes.get(execution_id)))

            canceled = await executions_api.cancel_execution_record(execution_id)
            final = await executions_api.wait_for_execution_terminal(execution_id, timeout=5)
            return canceled, final

        canceled, final = asyncio.run(scenario())

        assert canceled["status"] == "CANCELED"
        assert canceled["terminated_processes"] >= 1
        assert final["status"] == "CANCELED"
        assert final["error_code"] == "EXECUTION_CANCELED"
        assert not external_commands._job_processes.get(final["id"])
