import asyncio

from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.runs.store import LocalRunStore


def test_harness_streams_and_persists_trace(tmp_path) -> None:
    asyncio.run(_run_harness(tmp_path))


async def _run_harness(tmp_path) -> None:
    config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
    harness = Harness(config, store=LocalRunStore(config.runs.path))
    events = [event async for event in harness.run_events("Why did Snowflake credits increase?")]
    trace = harness._last_trace
    assert any(event.kind.value == "routing_completed" for event in events)
    assert trace.result and "offline executor" in trace.result.output
    loaded = await harness.store.get(trace.run_id)
    assert loaded.routing and loaded.routing.task_family == "cost_optimization"
