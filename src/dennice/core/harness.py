from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dennice.cognition.registry import PackagePolicyRegistry
from dennice.core.config import DenniceConfig
from dennice.core.models import (
    EventKind,
    ExecutionResult,
    RunEvent,
    RunTrace,
    RoutingDecision,
    Task,
)
from dennice.executors.base import Executor
from dennice.executors.factory import executor_from_config
from dennice.prompting.composer import DefaultPromptComposer
from dennice.routing.base import CognitiveRouter
from dennice.routing.rule import RuleRouter
from dennice.runs.store import LocalRunStore


class Harness:
    """Coordinates routing, policy resolution, execution, and trace persistence."""

    def __init__(
        self,
        config: DenniceConfig | None = None,
        *,
        router: CognitiveRouter | None = None,
        registry: PackagePolicyRegistry | None = None,
        composer: DefaultPromptComposer | None = None,
        executor: Executor | None = None,
        store: LocalRunStore | None = None,
    ) -> None:
        self.config = config or DenniceConfig()
        self.router = router or RuleRouter()
        self.registry = registry or PackagePolicyRegistry()
        self.composer = composer or DefaultPromptComposer()
        self.executor = executor or executor_from_config(self.config.executor)
        self.store = store or LocalRunStore(self.config.runs.path)
        self._last_trace: RunTrace | None = None

    @classmethod
    def from_config(cls, path: str | Path | None = None) -> "Harness":
        return cls(DenniceConfig.load(path))

    async def classify(self, task: str | Task) -> RoutingDecision:
        normalized = task if isinstance(task, Task) else Task(prompt=task)
        return await self.router.classify(normalized)

    async def run(self, task: str | Task) -> RunTrace:
        async for _ in self.run_events(task):
            pass
        if self._last_trace is None:  # Defensive: run_events always assigns before completion.
            raise RuntimeError("Run completed without producing a trace")
        return self._last_trace

    async def run_base(self, task: str | Task, benchmark: dict[str, object] | None = None) -> RunTrace:
        async for _ in self._run_events(task, classify=False, benchmark=benchmark):
            pass
        if self._last_trace is None:
            raise RuntimeError("Base run completed without producing a trace")
        return self._last_trace

    async def run_with_routing(
        self,
        task: str | Task,
        decision: RoutingDecision,
        benchmark: dict[str, object] | None = None,
    ) -> RunTrace:
        async for _ in self._run_events(task, routing=decision, classify=False, benchmark=benchmark):
            pass
        if self._last_trace is None:
            raise RuntimeError("Routed run completed without producing a trace")
        return self._last_trace

    async def run_events(self, task: str | Task) -> AsyncIterator[RunEvent]:
        async for event in self._run_events(task, classify=True):
            yield event

    async def _run_events(
        self,
        task: str | Task,
        *,
        classify: bool,
        routing: RoutingDecision | None = None,
        benchmark: dict[str, object] | None = None,
    ) -> AsyncIterator[RunEvent]:
        normalized = task if isinstance(task, Task) else Task(prompt=task)
        run_id = f"run_{uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        trace = RunTrace(
            run_id=run_id,
            started_at=started_at,
            task=normalized,
            executor_id=f"{self.executor.id}-{self.executor.version}",
            config=self.config.model_dump(mode="json"),
            benchmark=benchmark,
        )

        def record(kind: EventKind, payload: dict[str, object] | None = None) -> RunEvent:
            event = RunEvent(run_id=run_id, kind=kind, payload=payload or {})
            trace.events.append(event)
            return event

        try:
            yield record(EventKind.RUN_STARTED, {"task_id": normalized.id})
            decision = routing
            if classify:
                yield record(EventKind.ROUTING_STARTED, {"router": self.router.id})
                decision = await self.router.classify(normalized)
                yield record(EventKind.ROUTING_COMPLETED, {"decision": decision.model_dump(mode="json")})
            if decision is not None:
                trace.routing = decision
            policies = self.registry.resolve_many(
                [decision.primary_demand, *decision.supporting_demands] if decision else []
            )
            trace.policies = policies
            for policy in policies:
                yield record(EventKind.POLICY_SELECTED, {"policy_id": policy.id, "version": policy.version})

            request = self.composer.compose(normalized, decision, policies)
            request = request.model_copy(
                update={"executor_id": f"{self.executor.id}-{self.executor.version}"}
            )
            yield record(EventKind.EXECUTION_STARTED, {"executor": self.executor.id})
            output: list[str] = []
            async for event in self.executor.execute(run_id, request):
                trace.events.append(event)
                output.append(str(event.payload.get("text", "")))
                yield event
            trace.result = ExecutionResult(output="".join(output))
            trace.completed_at = datetime.now(timezone.utc)
            yield record(EventKind.RUN_COMPLETED, {"output_chars": len(trace.result.output)})
        except Exception as exc:
            trace.error = str(exc)
            trace.completed_at = datetime.now(timezone.utc)
            yield record(EventKind.RUN_FAILED, {"error": trace.error})
        finally:
            await self.store.save(trace)
            self._last_trace = trace
