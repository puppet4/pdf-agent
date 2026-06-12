"""Contracts for tracked external command execution and cancellation."""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from pdf_agent import external_commands
from pdf_agent.core import ErrorCode, ToolError


class _FakeProcess:
    def __init__(
        self,
        cmd: list[str],
        *,
        returncode: int | None = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        timeout: bool = False,
    ) -> None:
        self.cmd = cmd
        self.pid = 4242
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.communicate_timeout: int | None = None

    def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
        self.communicate_timeout = timeout
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout)
        return self.stdout, self.stderr


def _clear_process_registry() -> None:
    external_commands._conversation_processes.clear()  # noqa: SLF001


@pytest.fixture(autouse=True)
def clear_process_registry() -> None:
    _clear_process_registry()
    yield
    _clear_process_registry()


def test_run_command_raises_timeout_terminates_and_cleans_up(monkeypatch: pytest.MonkeyPatch):
    process = _FakeProcess(["slow-tool"], timeout=True)
    terminated: list[_FakeProcess] = []

    monkeypatch.setattr(external_commands.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(external_commands, "_terminate_process", lambda proc: terminated.append(proc))

    with pytest.raises(ToolError) as exc_info:
        external_commands.run_command(["slow-tool"], conversation_run_id="run-1", timeout=2)

    assert exc_info.value.code == ErrorCode.ENGINE_EXEC_TIMEOUT
    assert "slow-tool" in exc_info.value.message
    assert process.communicate_timeout == 2
    assert terminated == [process]
    assert not external_commands._conversation_processes.get("run-1")  # noqa: SLF001


def test_run_command_raises_failed_with_stderr_detail(monkeypatch: pytest.MonkeyPatch):
    process = _FakeProcess(["bad-tool"], returncode=7, stderr=b"conversion failed")
    monkeypatch.setattr(external_commands.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ToolError) as exc_info:
        external_commands.run_command(["bad-tool"])

    assert exc_info.value.code == ErrorCode.ENGINE_EXEC_FAILED
    assert exc_info.value.message == "conversion failed"


def test_run_command_check_false_returns_completed_process(monkeypatch: pytest.MonkeyPatch):
    process = _FakeProcess(["bad-tool"], returncode=7, stdout=b"out", stderr=b"err")
    monkeypatch.setattr(external_commands.subprocess, "Popen", lambda *args, **kwargs: process)

    result = external_commands.run_command(["bad-tool"], check=False)

    assert result.args == ["bad-tool"]
    assert result.returncode == 7
    assert result.stdout == b"out"
    assert result.stderr == b"err"


def test_bound_conversation_context_tracks_nested_command(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    class _TrackingProcess(_FakeProcess):
        def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
            calls.append({"timeout": timeout, "tracked": self in external_commands._conversation_processes["ctx-run"]})  # noqa: SLF001
            return super().communicate(timeout=timeout)

    process = _TrackingProcess(["tool"])
    monkeypatch.setattr(external_commands.subprocess, "Popen", lambda *args, **kwargs: process)

    with external_commands.bind_conversation_run_context("ctx-run"):
        result = external_commands.run_command(["tool"], timeout=3)

    assert result.returncode == 0
    assert calls == [{"timeout": 3, "tracked": True}]
    assert not external_commands._conversation_processes.get("ctx-run")  # noqa: SLF001


def test_cancel_conversation_processes_pops_registry_and_terminates_all(monkeypatch: pytest.MonkeyPatch):
    process_a = _FakeProcess(["a"], returncode=None)
    process_b = _FakeProcess(["b"], returncode=None)
    external_commands._conversation_processes["cancel-run"].update({process_a, process_b})  # noqa: SLF001
    terminated: list[_FakeProcess] = []
    monkeypatch.setattr(external_commands, "_terminate_process", lambda proc: terminated.append(proc))

    count = external_commands.cancel_conversation_processes("cancel-run")

    assert count == 2
    assert set(terminated) == {process_a, process_b}
    assert not external_commands._conversation_processes.get("cancel-run")  # noqa: SLF001


def test_terminate_process_returns_when_process_already_exited():
    process = _FakeProcess(["done"], returncode=0)
    process.poll = lambda: 0

    external_commands._terminate_process(process)  # noqa: SLF001

    assert process.returncode == 0


def test_terminate_process_uses_process_group_then_waits(monkeypatch: pytest.MonkeyPatch):
    process = _FakeProcess(["running"], returncode=None)
    killed: list[tuple[int, int]] = []
    waited: list[int] = []

    process.poll = lambda: None
    process.wait = lambda timeout: waited.append(timeout) or 0
    monkeypatch.setattr(external_commands.os, "name", "posix")
    monkeypatch.setattr(external_commands.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    external_commands._terminate_process(process)  # noqa: SLF001

    assert killed == [(process.pid, external_commands.signal.SIGTERM)]
    assert waited == [3]


def test_terminate_process_force_kill_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch):
    process = _FakeProcess(["stubborn"], returncode=None)
    signals: list[int] = []

    process.poll = lambda: None

    def fail_wait(timeout):
        raise RuntimeError("still running")

    def fail_killpg(pid, sig):
        signals.append(sig)
        raise RuntimeError("cannot signal")

    process.wait = fail_wait
    monkeypatch.setattr(external_commands.os, "name", "posix")
    monkeypatch.setattr(external_commands.os, "killpg", fail_killpg)

    external_commands._terminate_process(process)  # noqa: SLF001

    assert signals == [external_commands.signal.SIGTERM, external_commands.signal.SIGKILL]


def test_terminate_process_uses_windows_terminate_then_kill(monkeypatch: pytest.MonkeyPatch):
    graceful = _FakeProcess(["win"], returncode=None)
    graceful.poll = lambda: None
    graceful_calls: list[str] = []
    graceful.terminate = lambda: graceful_calls.append("terminate")
    graceful.wait = lambda timeout: graceful_calls.append(f"wait:{timeout}") or 0

    monkeypatch.setattr(external_commands.os, "name", "nt")
    external_commands._terminate_process(graceful)  # noqa: SLF001

    assert graceful_calls == ["terminate", "wait:3"]

    stubborn = _FakeProcess(["win-stubborn"], returncode=None)
    stubborn.poll = lambda: None
    stubborn_calls: list[str] = []
    stubborn.terminate = lambda: stubborn_calls.append("terminate")
    stubborn.kill = lambda: stubborn_calls.append("kill")

    def fail_wait(timeout):
        stubborn_calls.append(f"wait:{timeout}")
        raise RuntimeError("still running")

    stubborn.wait = fail_wait

    external_commands._terminate_process(stubborn)  # noqa: SLF001

    assert stubborn_calls == ["terminate", "wait:3", "kill"]
