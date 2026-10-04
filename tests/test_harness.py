import asyncio

from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import CognitiveScore, EventKind, RoutingDecision, RunEvent
from dennice.cognition.taxonomy import CognitiveDemand
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


def test_policy_confidence_gates_are_applied_before_execution(tmp_path) -> None:
    class Router:
        id, version = "fixture", "v1"
        primary_confidence = 0.79

        async def classify(self, task):
            return RoutingDecision(
                task_family="data_analysis",
                cognitive_demands=[
                    CognitiveScore(demand=CognitiveDemand.EMPIRICAL_INDUCTION,
                                   confidence=self.primary_confidence),
                    CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=0.54),
                    CognitiveScore(demand=CognitiveDemand.CRITICAL_INQUIRY, confidence=0.56),
                ],
                primary_demand=CognitiveDemand.EMPIRICAL_INDUCTION,
                supporting_demands=[CognitiveDemand.DECOMPOSITION,
                                    CognitiveDemand.CRITICAL_INQUIRY],
            )

    class Executor:
        id, version = "fixture", "v1"

        def __init__(self):
            self.requests = []

        async def execute(self, run_id, request):
            self.requests.append(request)
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM,
                           payload={"text": "Fixture response"})

    async def check():
        router, executor = Router(), Executor()
        config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
        harness = Harness(config, router=router, executor=executor)
        low = await harness.run("Investigate the metric")
        assert low.policies == []
        assert "PRIMARY REASONING POLICY" not in executor.requests[-1].system_instructions
        router.primary_confidence = 0.9
        high = await harness.run("Investigate the metric")
        assert [policy.cognitive_demand for policy in high.policies] == [
            CognitiveDemand.EMPIRICAL_INDUCTION, CognitiveDemand.CRITICAL_INQUIRY]
        assert "PRIMARY REASONING POLICY" in executor.requests[-1].system_instructions
        assert "SUPPORTING REASONING POLICY: critical_inquiry" in executor.requests[-1].system_instructions
        assert "SUPPORTING REASONING POLICY: decomposition" not in executor.requests[-1].system_instructions

    asyncio.run(check())
