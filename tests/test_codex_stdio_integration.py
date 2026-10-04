"""Executor plus real local app-server transport, without model inference."""

import asyncio
import sys
from pathlib import Path

import pytest

from dennice.core.config import PermissionMode
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.codex_appserver import CodexAppServerExecutor


FIXTURE = Path(__file__).parent / "fixtures" / "codex_appserver.py"


@pytest.fixture
def peer(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec
    processes = []
    log_path = tmp_path / "rpc.log"
    monkeypatch.setenv("DENNICE_CODEX_FIXTURE_LOG", str(log_path))

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        process = await original(sys.executable, "-I", str(FIXTURE), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    return log_path, processes


def request():
    return ExecutionRequest(
        task=Task(prompt="Fixture turn", metadata={"session_id": "fixture-session"}),
        system_instructions="Fixture policy",
    )


def test_real_stdio_codex_auth_approval_and_resume(peer, tmp_path):
    log_path, processes = peer

    async def journey():
        decisions = iter([True, False])
        audit = []

        async def approve(*_):
            assert audit[-1][0] == EventKind.APPROVAL_REQUESTED
            return next(decisions)

        async def emit(kind, payload):
            audit.append((kind, payload))

        executor = CodexAppServerExecutor(permission_mode=PermissionMode.WORKSPACE_WRITE)
        executor.configure_runtime(root=tmp_path, store_path=tmp_path / "runs", approve=approve, emit=emit)
        first = [event async for event in executor.execute("first", request())]
        second = [event async for event in executor.execute("second", request())]
        for events in (first, second):
            assert any(event.kind == EventKind.PROVIDER_EVENT and
                       event.payload.get("auth_method") == "chatgpt" for event in events)
            assert any(event.kind == EventKind.MODEL_STREAM and
                       event.payload["text"] == "fixture answer" for event in events)
        assert [kind for kind, _ in audit] == [
            EventKind.APPROVAL_REQUESTED, EventKind.APPROVAL_RESOLVED,
            EventKind.APPROVAL_REQUESTED, EventKind.APPROVAL_RESOLVED,
        ]

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == [
        "initialize", "account/read", "thread/start", "turn/start", "approval:accept",
        "initialize", "account/read", "thread/resume", "turn/start", "approval:decline",
    ]
    assert len(processes) == 2 and all(process.returncode is not None for process in processes)


def test_real_stdio_codex_auth_mismatch_stops_before_thread(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CODEX_FIXTURE_AUTH", "apiKey")

    async def journey():
        executor = CodexAppServerExecutor()
        executor.configure_runtime(root=tmp_path)
        with pytest.raises(RuntimeError, match="Codex authentication is apiKey"):
            _ = [event async for event in executor.execute("mismatch", request())]

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == ["initialize", "account/read"]
    assert len(processes) == 1 and processes[0].returncode is not None


def test_real_stdio_codex_close_interrupts_owned_turn(peer, tmp_path, monkeypatch):
    log_path, processes = peer
    monkeypatch.setenv("DENNICE_CODEX_FIXTURE_MODE", "hang")

    async def journey():
        executor = CodexAppServerExecutor()
        executor.configure_runtime(root=tmp_path, store_path=tmp_path / "runs")
        events = executor.execute("interrupted", request())
        async with asyncio.timeout(8):
            while (event := await anext(events)).kind != EventKind.MODEL_STREAM:
                pass
            assert event.payload["text"] == "partial"
            await events.aclose()

    asyncio.run(journey())
    assert log_path.read_text().splitlines() == [
        "initialize", "account/read", "thread/start", "turn/start", "turn/interrupt",
    ]
    assert len(processes) == 1 and processes[0].returncode is not None
