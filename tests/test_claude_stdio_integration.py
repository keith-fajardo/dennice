"""Claude executor against a real local CLI-shaped subprocess, without inference."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from dennice.core.config import PermissionMode
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.claude import ClaudeExecutor
from dennice.runs.provider_sessions import ProviderSessionStore


FIXTURE = Path(__file__).parent / "fixtures" / "claude_cli.py"


@pytest.fixture
def peer(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec
    processes = []
    log_path = tmp_path / "cli.log"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_LOG", str(log_path))

    async def launch(*command, **options):
        if command and command[0] == "taskkill":
            return await original(*command, **options)
        joined = " ".join(command)
        operation = "auth" if "auth status" in joined else "turn"
        resume = "resume" if "--resume" in joined else "new"
        process = await original(sys.executable, "-I", str(FIXTURE), operation, resume, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    return log_path, processes


def request():
    return ExecutionRequest(task=Task(prompt="Fixture turn", metadata={"session_id": "fixture-session"}),
                            system_instructions="Fixture policy")


def test_real_stdio_claude_auth_completion_and_resume(peer, tmp_path):
    log_path, processes = peer

    async def journey():
        executor = ClaudeExecutor()
        executor.configure_runtime(root=tmp_path, store_path=tmp_path / "runs")
        first = [event async for event in executor.execute("first", request())]
        second = [event async for event in executor.execute("second", request())]
        for events, resumed in ((first, False), (second, True)):
            assert any(event.kind == EventKind.PROVIDER_EVENT and
                       event.payload.get("auth_method") == "subscription" for event in events)
            assert any(event.kind == EventKind.PROVIDER_SESSION and
                       event.payload["resumed"] is resumed for event in events)
            assert [event.payload["text"] for event in events
                    if event.kind == EventKind.MODEL_STREAM] == ["Fixture answer"]
        state = ProviderSessionStore(tmp_path / "runs").get("fixture-session", "claude", str(tmp_path))
        assert state["status"] == "completed"

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth", "turn:new", "auth", "turn:resume"]
    assert len(processes) == 4 and all(process.returncode is not None for process in processes)


def test_real_stdio_claude_missing_terminal_result_is_not_complete(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_MODE", "missing_result")

    async def journey():
        executor = ClaudeExecutor()
        executor.configure_runtime(root=tmp_path, store_path=tmp_path / "runs")
        with pytest.raises(RuntimeError, match="without a terminal result"):
            _ = [event async for event in executor.execute("incomplete", request())]
        state = ProviderSessionStore(tmp_path / "runs").get("fixture-session", "claude", str(tmp_path))
        assert state["status"] == "interrupted"
        with pytest.raises(RuntimeError, match="prior native Claude turn"):
            _ = [event async for event in executor.execute("retry", request())]

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth", "turn:new", "auth"]
    assert len(processes) == 3 and all(process.returncode is not None for process in processes)


def test_real_stdio_claude_rejects_unsigned_status_before_turn(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_SIGNED_IN", "false")

    async def journey():
        executor = ClaudeExecutor()
        executor.configure_runtime(root=tmp_path)
        with pytest.raises(RuntimeError, match="not signed in"):
            _ = [event async for event in executor.execute("unsigned", request())]

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth"]
    assert len(processes) == 1 and processes[0].returncode is not None


def test_real_stdio_claude_failure_does_not_publish_stderr(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_MODE", "fail")

    async def journey():
        executor = ClaudeExecutor()
        executor.configure_runtime(root=tmp_path)
        with pytest.raises(RuntimeError, match="exited with status 7") as error:
            _ = [event async for event in executor.execute("failed", request())]
        assert "private-fixture-token" not in str(error.value)

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth", "turn:new"]
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)


def test_real_stdio_claude_structured_error_is_not_published(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_MODE", "structured_fail")

    async def journey():
        executor = ClaudeExecutor()
        executor.configure_runtime(root=tmp_path)
        events = []
        with pytest.raises(RuntimeError, match="Claude execution failed") as error:
            async for event in executor.execute("failed", request()):
                events.append(event)
        assert "private-fixture-token" not in str(error.value)
        assert "private-fixture-token" not in str(events)

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth", "turn:new"]
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)


@pytest.mark.parametrize("permission", [PermissionMode.PLAN, PermissionMode.WORKSPACE_WRITE])
def test_real_stdio_claude_close_stops_owned_process(peer, tmp_path, monkeypatch, permission):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CLAUDE_FIXTURE_MODE", "hang")
    gate_closed = []
    if permission == PermissionMode.WORKSPACE_WRITE:
        class Gate:
            def __init__(self, *args, **kwargs):
                self.session_id = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                assert processes[-1].returncode is not None
                gate_closed.append(True)

            def settings(self):
                return {"hooks": {}}

            def environment(self):
                return os.environ.copy()

        monkeypatch.setattr("dennice.executors.claude.NativeToolGate", Gate)

    async def journey():
        executor = ClaudeExecutor(permission_mode=permission)
        executor.configure_runtime(root=tmp_path, store_path=tmp_path / "runs")
        events = executor.execute("interrupted", request())
        async with asyncio.timeout(8):
            while (event := await anext(events)).kind != EventKind.MODEL_STREAM:
                pass
            assert event.payload["text"] == "Fixture answer"
            await events.aclose()
            assert processes[-1].returncode is not None
        state = ProviderSessionStore(tmp_path / "runs").get("fixture-session", "claude", str(tmp_path))
        assert state["status"] == "interrupted"

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["auth", "turn:new"]
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)
    if permission == PermissionMode.WORKSPACE_WRITE:
        assert gate_closed == [True]
