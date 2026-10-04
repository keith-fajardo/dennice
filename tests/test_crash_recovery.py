import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from dennice.core.models import EventKind, RunEvent, RunTrace, Task
from dennice.runs.store import LocalRunStore


def fixture_trace(run_id="fixture"):
    return RunTrace(run_id=run_id, started_at=datetime.now(timezone.utc),
                    task=Task(prompt="fixture"), executor_id="fixture")


def test_live_owner_not_recovered_or_overwritten_by_other_store(tmp_path):
    async def test():
        owner, observer = LocalRunStore(tmp_path), LocalRunStore(tmp_path)
        trace = fixture_trace()
        owner.claim(trace.run_id)
        try:
            await owner.save(trace)
            assert await observer.recover_stale() == []
            with pytest.raises(RuntimeError, match="live process"):
                await observer.save(trace)
            with pytest.raises(RuntimeError):
                observer.claim(trace.run_id)
        finally:
            owner.release(trace.run_id)
        assert await observer.recover_stale() == [trace.run_id]
        assert (await observer.get(trace.run_id)).status == "interrupted"
        assert await observer.recover_stale() == []
    asyncio.run(test())


def test_abrupt_subprocess_death_recovers_unknown_effect_without_replay(tmp_path):
    code = """
import asyncio, os, sys
from datetime import datetime, timezone
from dennice.runs.store import LocalRunStore
from dennice.core.models import RunTrace, RunEvent, EventKind, Task
store = LocalRunStore(sys.argv[1])
store.claim('crashed')
trace = RunTrace(run_id='crashed', started_at=datetime.now(timezone.utc), task=Task(prompt='fixture'), executor_id='fixture')
trace.events = [RunEvent(run_id='crashed', kind=EventKind.TOOL_STARTED, payload={'tool':'shell','operation_id':'action'})]
asyncio.run(store.save(trace))
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], timeout=20, capture_output=True)
    assert result.returncode == 17, result.stderr
    async def test():
        store = LocalRunStore(tmp_path)
        original = await store.get("crashed")
        assert await store.recover_stale() == ["crashed"]
        trace = await store.get("crashed")
        assert trace.events[:len(original.events)] == original.events
        state = await store.inspect_recovery("crashed")
        assert state["unresolved_effects"][0]["state"] == "unknown"
        assert not state["replay"]
        assert not (tmp_path / "replayed").exists()
        resolved = await store.reconcile("crashed", "action", "not_executed", "Fixture confirms only the start marker was written.")
        assert resolved["unresolved_effects"] == []
        with pytest.raises(ValueError, match="No unresolved"):
            await store.reconcile("crashed", "action", "completed", "Duplicate resolution")
        assert (await store.get("crashed")).status == "interrupted"
    asyncio.run(test())


def test_concurrent_process_lock_not_pid_age_heuristic(tmp_path):
    code = """
import asyncio, sys
from datetime import datetime, timezone
from dennice.runs.store import LocalRunStore
from dennice.core.models import RunTrace, Task
store=LocalRunStore(sys.argv[1]); store.claim('active')
trace=RunTrace(run_id='active',started_at=datetime.now(timezone.utc),task=Task(prompt='fixture'),executor_id='fixture')
asyncio.run(store.save(trace)); print('ready',flush=True); sys.stdin.readline()
store.release('active')
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        assert asyncio.run(LocalRunStore(tmp_path).recover_stale()) == []
        process.stdin.write("exit\n")
        process.stdin.flush()
        process.wait(timeout=10)
        assert asyncio.run(LocalRunStore(tmp_path).recover_stale()) == ["active"]
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_effect_pairing_and_reconciliation_excludes_completed_reads(tmp_path):
    trace = fixture_trace()
    events = [
        (EventKind.TOOL_STARTED, {"tool": "read_file", "operation_id": "read"}),
        (EventKind.TOOL_STARTED, {"tool": "shell", "operation_id": "done"}),
        (EventKind.TOOL_COMPLETED, {"tool": "shell", "operation_id": "done"}),
        (EventKind.HOOK_STARTED, {"name": "validate", "event": "before_tool", "operation_id": "hook"}),
        (EventKind.HOOK_COMPLETED, {"operation_id": "hook", "success": False, "outcome_known": False}),
        (EventKind.TOOL_STARTED, {"tool": "verification", "operation_id": "verify"}),
    ]
    trace.events = [RunEvent(run_id=trace.run_id, kind=kind, payload=data) for kind, data in events]
    trace.status = "cancelled"
    assert {entry["operation_id"] for entry in LocalRunStore.effects(trace)} == {"hook", "verify"}
    async def test():
        store = LocalRunStore(tmp_path)
        await store.save(trace)
        with pytest.raises(ValueError):
            await store.reconcile(trace.run_id, "hook", "probably", "guess")
        with pytest.raises(ValueError):
            await store.reconcile(trace.run_id, "hook", "completed", "")
    asyncio.run(test())


def test_schema_migration_retains_events_and_unknown_legacy_ownership(tmp_path):
    trace = fixture_trace("legacy")
    trace.events = [RunEvent(run_id="legacy", kind=EventKind.RUN_STARTED)]
    db = sqlite3.connect(tmp_path / "runs.sqlite3")
    db.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, trace TEXT NOT NULL)")
    db.execute("CREATE TABLE events (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(run_id,sequence))")
    db.execute("INSERT INTO runs VALUES(?,?,?)", ("legacy", trace.started_at.isoformat(), trace.model_dump_json()))
    db.execute("INSERT INTO events VALUES(?,?,?)", ("legacy", 0, trace.events[0].model_dump_json()))
    db.execute("PRAGMA user_version=1")
    db.commit(); db.close()
    async def test():
        store = LocalRunStore(tmp_path)
        assert await store.recover_stale() == []  # No ownership record, cannot infer death.
        assert (await store.get("legacy")).events == trace.events
        with store._connection() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        broken = trace.model_copy(deep=True)
        broken.events[0].run_id = "different"
        with pytest.raises(ValueError):
            await store.save(broken)
        assert (await store.get("legacy")).events == trace.events
    asyncio.run(test())


def test_goal_resume_blocks_until_unknown_effect_is_reconciled(tmp_path):
    from dennice.core.config import DenniceConfig
    from dennice.core.goals import GoalController, GoalState
    from dennice.core.harness import Harness
    async def test():
        settings = DenniceConfig(runs={"path": str(tmp_path / "runs")})
        settings.tools.root = str(tmp_path)
        settings.verification.required_files = ["artifact"]
        (tmp_path / "artifact").write_text("fixture")
        harness = Harness(settings)
        controller = GoalController(settings.runs.path)
        trace = fixture_trace("previous")
        trace.status = "interrupted"
        trace.events = [RunEvent(run_id="previous", kind=EventKind.TOOL_STARTED,
                                payload={"tool": "shell", "operation_id": "unknown"})]
        await harness.store.save(trace)
        goal = GoalState(session_id="s", objective="Continue", runs=["previous"])
        blocked = [event async for event in controller.run_events(goal, harness)]
        assert len(blocked) == 1 and blocked[0].payload["status"] == "awaiting_input"
        assert len(goal.runs) == 1
        await harness.store.reconcile("previous", "unknown", "not_executed", "Inspected fixture evidence.")
        finished = [event async for event in controller.run_events(goal, harness)]
        assert finished[-1].payload["status"] == "complete"
        assert len(goal.runs) == 2
        current = await harness.store.get(goal.runs[-1])
        assert "explicit_user_resolution" in str(current.task.context)
    asyncio.run(test())


def test_cancelled_hook_keeps_unknown_intent_after_process_cleanup(tmp_path):
    from dennice.core.config import HookConfig
    from dennice.core.hooks import HookManager
    async def test():
        events = []
        async def emit(kind, payload):
            events.append(RunEvent(run_id="fixture", kind=kind, payload=payload))
        hook = HookConfig(name="fixture", event="before_execution", enabled=True,
                          command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=.05)
        manager = HookManager()
        manager.trust(hook)
        with pytest.raises(RuntimeError, match="timed out"):
            await manager.dispatch([hook], "before_execution", {}, str(tmp_path), emit=emit)
        assert [event.kind for event in events] == [EventKind.HOOK_STARTED, EventKind.HOOK_COMPLETED]
        assert events[-1].payload["outcome_known"] is False
        trace = fixture_trace()
        trace.events = events
        trace.status = "failed"
        assert LocalRunStore.effects(trace)[0]["state"] == "unknown"
    asyncio.run(test())


@pytest.mark.parametrize("include_arguments", [False, True])
def test_hook_tool_arguments_require_explicit_opt_in(tmp_path, include_arguments):
    from dennice.core.config import HookConfig
    from dennice.core.hooks import HookManager
    script = "import json,sys; data=json.load(sys.stdin); assert ('arguments' in data)==" + str(include_arguments) + "; print(json.dumps({'decision':'allow'}))"
    hook = HookConfig(name="fixture", event="before_tool", enabled=True,
                      command=[sys.executable, "-c", script], include_arguments=include_arguments)
    manager = HookManager()
    manager.trust(hook)
    result = asyncio.run(manager.dispatch([hook], "before_tool", {"tool": "replace_text", "arguments": {"path": "fixture"}}, str(tmp_path)))
    assert result[0]["success"]
