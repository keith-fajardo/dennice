from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.metrics import evaluate_routing
from dennice.benchmark.schema import (
    AggregateRoutingMetrics,
    BenchmarkItem,
    BenchmarkItemResult,
    BenchmarkRunResult,
    RoutingMetrics,
)
from dennice.benchmark.store import LocalBenchmarkStore
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode, CognitiveScore, RoutingDecision


class BenchmarkRunner:
    """Runs deterministic benchmark modes through the same Harness execution path."""

    def __init__(self, harness: Harness, store: LocalBenchmarkStore | None = None) -> None:
        self.harness = harness
        self.store = store or LocalBenchmarkStore(harness.store.path.parent / "benchmarks")

    async def run(self, dataset: BenchmarkDataset, mode: BenchmarkMode) -> BenchmarkRunResult:
        if mode not in {BenchmarkMode.BASE, BenchmarkMode.ORACLE, BenchmarkMode.ROUTER}:
            raise ValueError(f"Benchmark mode {mode.value!r} is designed but not implemented yet")
        results: list[BenchmarkItemResult] = []
        for item in dataset.items:
            task = item.to_task(dataset.evidence.get(item.id))
            benchmark_metadata = {
                "item_id": item.id,
                "item_version": item.version,
                "mode": mode.value,
                "dataset_split": item.split,
            }
            if mode is BenchmarkMode.BASE:
                trace = await self.harness.run_base(task, benchmark_metadata)
            elif mode is BenchmarkMode.ORACLE:
                trace = await self.harness.run_with_routing(
                    task, self._oracle_decision(item), benchmark_metadata
                )
            else:
                trace = await self.harness.run(task)
                trace.benchmark = benchmark_metadata
                await self.harness.store.save(trace)
            metrics = evaluate_routing(trace.routing, item.gold_cognitive_demands)
            results.append(
                BenchmarkItemResult(
                    item_id=item.id,
                    item_version=item.version,
                    mode=mode,
                    trace_run_id=trace.run_id,
                    routing=metrics,
                    task_family=item.task_family,
                    policy_ids=[policy.id for policy in trace.policies],
                    tool_calls=trace.result.tool_calls if trace.result else 0,
                    error=trace.error,
                )
            )
        result = BenchmarkRunResult(
            benchmark_run_id=f"benchmark_{uuid4().hex}",
            mode=mode,
            created_at=datetime.now(timezone.utc).isoformat(),
            items=results,
            aggregate_routing=self._aggregate(results),
        )
        await self.store.save(result)
        return result

    @staticmethod
    def _oracle_decision(item: BenchmarkItem) -> RoutingDecision:
        demands = [item.gold_cognitive_demands.primary, *item.gold_cognitive_demands.supporting]
        return RoutingDecision(
            task_family=item.task_family,
            cognitive_demands=[CognitiveScore(demand=demand, confidence=1.0) for demand in demands],
            primary_demand=item.gold_cognitive_demands.primary,
            supporting_demands=item.gold_cognitive_demands.supporting,
            rationale="Benchmark oracle labels; not a general routing prediction.",
            router_id="oracle",
            router_version="v1",
        )

    @staticmethod
    def _aggregate(results: list[BenchmarkItemResult]) -> AggregateRoutingMetrics:
        scored = [item.routing for item in results if item.routing.multilabel_f1 is not None]
        if not scored:
            return AggregateRoutingMetrics()
        primary = [metric.primary_correct for metric in scored if metric.primary_correct is not None]
        return AggregateRoutingMetrics(
            primary_accuracy=sum(primary) / len(primary) if primary else None,
            multilabel_precision=sum(metric.multilabel_precision or 0.0 for metric in scored) / len(scored),
            multilabel_recall=sum(metric.multilabel_recall or 0.0 for metric in scored) / len(scored),
            multilabel_f1=sum(metric.multilabel_f1 or 0.0 for metric in scored) / len(scored),
        )
