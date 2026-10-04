import asyncio
from datetime import datetime, timezone

import pytest

from dennice.core.accounting import phase_tokens, summarize_usage
from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import EventKind, RunEvent, RunTrace, Task
from dennice.runs.store import LocalRunStore


def event(kind, **payload):
    return RunEvent(run_id="run", kind=kind, payload=payload)


def test_router_overhead_and_partial_usage_remain_unknown():
    events = [event(EventKind.ROUTING_STARTED, router="jev"),
              event(EventKind.MODEL_CALL_STARTED, attempt_id="api0", phase="executor"),
              event(EventKind.USAGE, attempt_id="api0", phase="executor", reported=True, input_tokens=10)]
    result = summarize_usage(events)
    assert result["input_tokens"] is None and result["output_tokens"] is None
    assert result["known_input_tokens_subtotal"] == 10
    assert result["known_output_tokens_subtotal"] is None
    assert result["unknown_attempts"] == 2 and result["cost_amount"] is None
    assert phase_tokens(result) == (10, None)


def test_cumulative_snapshots_replace_not_double_count():
    result = summarize_usage([
        event(EventKind.USAGE, attempt_id="native", cumulative=True, reported=True, input_tokens=4, output_tokens=2, cost_amount=.001, currency="USD"),
        event(EventKind.USAGE, attempt_id="native", cumulative=True, reported=True, input_tokens=9, output_tokens=3, cost_amount=.003, currency="USD"),
    ])
    assert result["input_tokens"] == 9 and result["output_tokens"] == 3
    assert result["cost_amount"] == .003 and result["currency"] == "USD"
    assert len(result["records"]) == 1


def test_cache_write_and_read_count_once_under_provider_specific_semantics():
    raw = summarize_usage([event(EventKind.USAGE, reported=True, input_tokens=10, output_tokens=2,
        cached_input_tokens=20, cache_creation_input_tokens=30, input_includes_cache=False)])
    assert raw["input_tokens"] == 60
    total = summarize_usage([event(EventKind.USAGE, reported=True, input_tokens=60, output_tokens=2,
        cached_input_tokens=20, cache_creation_input_tokens=30, input_includes_cache=True)])
    assert total["input_tokens"] == 60
    assert total["known_input_tokens_subtotal"] == 60
    partial = summarize_usage([event(EventKind.USAGE, reported=True, input_tokens=10, output_tokens=2,
        cached_input_tokens=None, cache_creation_input_tokens=30, input_includes_cache=False)])
    assert partial["input_tokens"] is None
    assert partial["known_input_tokens_subtotal"] == 40


def test_interrupted_model_attempt_is_not_zero_tokens():
    result = summarize_usage([event(EventKind.MODEL_CALL_STARTED, attempt_id="interrupted", phase="executor")])
    assert result["unknown_attempts"] == 1
    assert result["input_tokens"] is None and result["cost_amount"] is None


@pytest.mark.parametrize("value", [-1, True, 1.2, "100"])
def test_invalid_reported_usage_rejected(value):
    with pytest.raises(ValueError):
        summarize_usage([event(EventKind.USAGE, reported=True, input_tokens=value)])


def test_rule_router_accounted_as_no_model_call_not_provider_zero():
    result = summarize_usage([event(EventKind.ROUTING_STARTED, router="rule")])
    assert result["records"][0]["source"] == "no_model_call"
    assert result["records"][0]["reported"] is False
    assert result["input_tokens"] == 0


def test_checkpoint_partial_executor_counts_preserves_unknown_and_cancellation(tmp_path):
    class Executor:
        id, version = "fixture", "v1"
        def __init__(self):
            self.started = asyncio.Event()
        async def execute(self, run_id, request):
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_CALL_STARTED, payload={"attempt_id": "fixture"})
            yield RunEvent(run_id=run_id, kind=EventKind.USAGE, payload={"attempt_id": "fixture", "reported": True, "input_tokens": 7})
            self.started.set()
            await asyncio.Event().wait()
    async def test():
        executor = Executor()
        harness = Harness(DenniceConfig(runs={"path": str(tmp_path)}), executor=executor)
        run = asyncio.create_task(harness.run_base("fixture"))
        await executor.started.wait()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        trace = await harness.store.get((await harness.store.list_recent())[0].run_id)
        assert trace.status == "cancelled"
        assert trace.result.input_tokens == 7 and trace.result.output_tokens is None
        assert trace.accounting["known_input_tokens_subtotal"] == 7
        assert trace.accounting["unknown_attempts"] == 1
    asyncio.run(test())


def test_invalid_usage_checkpoint_rolls_back_event_journal(tmp_path):
    async def test():
        store = LocalRunStore(tmp_path)
        trace = RunTrace(run_id="run", started_at=datetime.now(timezone.utc), task=Task(prompt="fixture"), executor_id="fixture")
        await store.save(trace)
        trace.events.append(event(EventKind.USAGE, reported=True, input_tokens=-1))
        with pytest.raises(ValueError):
            await store.save(trace)
        assert (await store.get("run")).events == []
    asyncio.run(test())


def test_native_tool_usage_is_observed_and_pending_effect_blocks_completion(tmp_path):
    class Executor:
        id, version = "native-fixture", "v1"
        async def execute(self, run_id, request):
            for effect in ["same", "same", "other"]:
                yield RunEvent(run_id=run_id, kind=EventKind.TOOL_STARTED,
                               payload={"tool": "native-action", "effect_id": effect})
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": "claimed done"})
    async def test():
        harness = Harness(DenniceConfig(runs={"path": str(tmp_path)}), executor=Executor())
        trace = await harness.run_base("fixture")
        assert trace.result.tool_calls == 2
        assert trace.status == "failed"
        assert "unresolved external effects" in trace.error
        assert len(harness.store.effects(trace)) == 2
    asyncio.run(test())
