import asyncio
from datetime import datetime, timezone

import pytest

from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import EventKind, RunEvent, RunTrace, Task
from dennice.runs.sessions import ChatMessage, ChatSession, SessionStore
from dennice.runs.store import LocalRunStore


def test_checkpoints_are_durable_before_events_are_exposed(tmp_path):
    async def check():
        harness = Harness(DenniceConfig(runs={"path": str(tmp_path)}))
        stream = harness.run_events("hello")
        event = await anext(stream)
        loaded = await LocalRunStore(tmp_path).get(event.run_id)
        assert loaded.events[0] == event
        assert loaded.status == "running"
        await stream.aclose()
        loaded = await LocalRunStore(tmp_path).get(event.run_id)
        assert loaded.status == "interrupted"
        assert loaded.events[-1].kind == EventKind.RUN_INTERRUPTED
    asyncio.run(check())


def test_concurrent_runs_return_their_own_trace_and_snapshot(tmp_path):
    class Executor:
        id, version = "test", "v1"

        async def execute(self, run_id, request):
            await asyncio.sleep(0.01 if request.task.prompt == "first" else 0)
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM,
                           payload={"text": request.task.prompt})

    async def check():
        config = DenniceConfig(runs={"path": str(tmp_path)})
        harness = Harness(config, executor=Executor())
        first, second = await asyncio.gather(harness.run_base("first"), harness.run_base("second"))
        assert first.run_id != second.run_id
        assert first.result.output == "first"
        assert second.result.output == "second"
        stream = harness.run_events(Task(prompt="snapshot", context={"key": "original"}))
        started = await anext(stream)
        config.executor.model = "changed"
        async for _ in stream:
            pass
        trace = await harness.store.get(started.run_id)
        assert trace.config["executor"]["model"] == "v1"
    asyncio.run(check())


def test_cancellation_is_recorded_and_closes_executor(tmp_path):
    class Executor:
        id, version = "test", "v1"
        closed = False

        def __init__(self):
            self.started = asyncio.Event()

        async def execute(self, run_id, request):
            try:
                self.started.set()
                await asyncio.Event().wait()
                yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM)
            finally:
                self.closed = True

    async def check():
        executor = Executor()
        harness = Harness(DenniceConfig(runs={"path": str(tmp_path)}), executor=executor)
        running = asyncio.create_task(harness.run_base("cancel me"))
        await executor.started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        summaries = await harness.store.list_recent()
        trace = await harness.store.get(summaries[0].run_id)
        assert executor.closed
        assert trace.status == "cancelled"
        assert trace.events[-1].kind == EventKind.RUN_CANCELLED
    asyncio.run(check())


def test_timeout_is_recorded_preserves_partial_output_and_closes_executor(tmp_path):
    class Executor:
        id, version = "test", "v1"

        def __init__(self):
            self.started = asyncio.Event()
            self.closed = False

        async def execute(self, run_id, request):
            try:
                self.started.set()
                yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM,
                               payload={"text": "partial"})
                await asyncio.Event().wait()
            finally:
                self.closed = True

    async def check():
        config = DenniceConfig(runs={"path": str(tmp_path)})
        config.budgets.max_seconds = 0.5
        executor = Executor()
        harness = Harness(config, executor=executor)
        trace = await harness.run("timeout me")
        assert executor.started.is_set()
        assert executor.closed
        assert trace.status == "timed_out"
        assert trace.result.output == "partial"
        assert trace.events[-1].kind == EventKind.RUN_FAILED
        assert trace.events[-1].payload["error"] == "Run time budget exhausted."

    asyncio.run(check())


def test_event_journal_rejects_rewrites_transactionally(tmp_path):
    async def check():
        store = LocalRunStore(tmp_path)
        trace = RunTrace(run_id="test", started_at=datetime.now(timezone.utc),
                         task=Task(prompt="test"), executor_id="mock")
        trace.events.append(RunEvent(run_id="test", kind=EventKind.RUN_STARTED))
        await store.save(trace)
        trace.events[0].payload["tampered"] = True
        with pytest.raises(ValueError, match="immutable"):
            await store.save(trace)
        assert (await store.get("test")).events[0].payload == {}
        with pytest.raises(ValueError):
            await store.get("../outside")
    asyncio.run(check())


def test_legacy_traces_are_read_without_removing_them(tmp_path):
    async def check():
        trace = RunTrace(run_id="legacy", started_at=datetime.now(timezone.utc),
                         task=Task(prompt="old trace"), executor_id="mock")
        path = tmp_path / "legacy.json"
        path.write_text(trace.model_dump_json())
        store = LocalRunStore(tmp_path)
        assert (await store.get("legacy")).task.prompt == "old trace"
        await store.save(trace)
        assert len(await store.list_recent()) == 1
        assert path.exists()
    asyncio.run(check())


def test_sessions_survive_restart_and_close_archives(tmp_path):
    store = SessionStore(tmp_path)
    session = ChatSession.new("Investigation")
    session.messages.append(ChatMessage("user", "hello", ["/image.png"]))
    session.input_history.append("hello")
    store.save(session)
    assert SessionStore(tmp_path).list() == [session]
    session.closed = True
    store.save(session)
    assert not SessionStore(tmp_path).list()
    assert SessionStore(tmp_path).list(include_closed=True) == [session]


def test_executor_configuration_changes_apply_only_to_next_run(tmp_path):
    async def check():
        config = DenniceConfig(runs={"path": str(tmp_path)})
        harness = Harness(config)
        stream = harness.run_events("snapshot")
        await anext(stream)
        config.executor.provider = "not-a-provider"
        async for _ in stream:
            pass
        assert harness._last_trace.status == "unverified"
        with pytest.raises(ValueError):
            await harness.run("next run")
    asyncio.run(check())


def test_owned_process_can_be_stopped():
    import os
    import sys
    from dennice.core.process import process_group_options, stop_process
    if os.name != "posix":
        pytest.skip("POSIX lifecycle contract; Windows requires its own CI")
    async def check():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", **process_group_options()
        )
        await stop_process(process)
        assert process.returncode is not None
    asyncio.run(check())


def test_command_timeout_stops_spawned_descendants(tmp_path):
    import os
    import sys
    from dennice.core.tools import run_command
    if os.name != "posix":
        pytest.skip("POSIX process-group contract; Windows requires its own CI")

    async def check():
        sentinel = tmp_path / "descendant-survived"
        child = "import pathlib,sys,time; time.sleep(.5); pathlib.Path(sys.argv[1]).write_text('alive')"
        parent = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', " + repr(child) + ", sys.argv[1]]); "
            "time.sleep(60)"
        )
        with pytest.raises(TimeoutError):
            await run_command([sys.executable, "-c", parent, str(sentinel)], tmp_path, .1)
        await asyncio.sleep(.6)
        assert not sentinel.exists()

    asyncio.run(check())
