from __future__ import annotations

import asyncio
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
from dennice.routing.factory import router_from_config
from dennice.runs.store import LocalRunStore
from dennice.core.hooks import HookManager
from dennice.core.mcp import MCPManager
from dennice.core.checks import verify_result
from dennice.core.tools import ToolBroker
from dennice.core.models import TaskAssessment
from dennice.routing.policy import enforce_privacy, select_route
from dennice.executors.api import APIExecutor, API_PROVIDERS
from dennice.core.accounting import phase_tokens, summarize_usage


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
        hooks: HookManager | None = None,
        mcp: MCPManager | None = None,
        approve=None,
    ) -> None:
        self.config = config or DenniceConfig()
        self._router_override = router
        self._executor_override = executor
        self.router = router or router_from_config(
            self.config.router, jev=self.config.jev, openjev=self.config.openjev
        )
        self.registry = registry or PackagePolicyRegistry()
        self.composer = composer or DefaultPromptComposer()
        self.executor = executor or executor_from_config(self.config.executor)
        self.store = store or LocalRunStore(self.config.runs.path)
        self._last_trace: RunTrace | None = None
        self.hooks = hooks or HookManager()
        self.mcp = mcp or MCPManager()
        self.approve = approve

    @classmethod
    def from_config(cls, path: str | Path | None = None) -> "Harness":
        return cls(DenniceConfig.load(path))

    async def classify(self, task: str | Task) -> RoutingDecision:
        normalized = task.model_copy(deep=True) if isinstance(task, Task) else Task(prompt=task)
        config = self.config.model_copy(deep=True)
        enforce_privacy(config)
        router = self._router_override or router_from_config(
            config.router, jev=config.jev, openjev=config.openjev
        )
        return await router.classify(normalized)

    async def run(self, task: str | Task) -> RunTrace:
        completed: list[RunTrace] = []
        async for _ in self._run_events(task, classify=True, completed=completed):
            pass
        if not completed:
            raise RuntimeError("Run completed without producing a trace")
        return completed[0]

    async def run_base(self, task: str | Task, benchmark: dict[str, object] | None = None) -> RunTrace:
        completed: list[RunTrace] = []
        async for _ in self._run_events(
            task, classify=False, benchmark=benchmark, completed=completed
        ):
            pass
        if not completed:
            raise RuntimeError("Base run completed without producing a trace")
        return completed[0]

    async def run_with_routing(
        self,
        task: str | Task,
        decision: RoutingDecision,
        benchmark: dict[str, object] | None = None,
    ) -> RunTrace:
        completed: list[RunTrace] = []
        async for _ in self._run_events(
            task, routing=decision, classify=False, benchmark=benchmark, completed=completed
        ):
            pass
        if not completed:
            raise RuntimeError("Routed run completed without producing a trace")
        return completed[0]

    async def run_events(self, task: str | Task) -> AsyncIterator[RunEvent]:
        events = self._run_events(task, classify=True)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    async def _run_events(self, task, *, classify, routing=None, benchmark=None, completed=None):
        traces = completed if completed is not None else []
        stream = self._run_unbounded_events(task, classify=classify, routing=routing,
                                            benchmark=benchmark, completed=traces)
        try:
            async with asyncio.timeout(self.config.budgets.max_seconds):
                async for event in stream:
                    yield event
        except TimeoutError:
            if not traces:
                raise
            trace = traces[-1]
            trace.status, trace.error = "timed_out", "Run time budget exhausted."
            event = RunEvent(run_id=trace.run_id, kind=EventKind.RUN_FAILED, payload={"error": trace.error})
            trace.events.append(event)
            await self.store.save(trace)
            yield event
        finally:
            await stream.aclose()

    async def _run_unbounded_events(
        self,
        task: str | Task,
        *,
        classify: bool,
        routing: RoutingDecision | None = None,
        benchmark: dict[str, object] | None = None,
        completed: list[RunTrace] | None = None,
    ) -> AsyncIterator[RunEvent]:
        normalized = task.model_copy(deep=True) if isinstance(task, Task) else Task(prompt=task)
        config = self.config.model_copy(deep=True)
        router = self._router_override or router_from_config(
            config.router, jev=config.jev, openjev=config.openjev
        )
        executor = self._executor_override or executor_from_config(config.executor)
        run_id = f"run_{uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        trace = RunTrace(
            run_id=run_id,
            started_at=started_at,
            task=normalized,
            executor_id=f"{executor.id}-{executor.version}",
            prompt_composer_version=self.composer.version,
            config=config.model_dump(mode="json"),
            benchmark=benchmark,
        )
        self.store.claim(run_id)

        async def record(kind: EventKind, payload: dict[str, object] | None = None) -> RunEvent:
            event = RunEvent(run_id=run_id, kind=kind, payload=payload or {})
            if kind == EventKind.USAGE:
                summarize_usage([event])
            trace.events.append(event)
            await self.store.save(trace)
            return event

        def tool_count(broker):
            if broker is not None:
                return broker.calls
            ids = {event.payload.get("operation_id") or event.payload.get("effect_id") or event.payload.get("call_id") or f"event_{index}"
                   for index, event in enumerate(trace.events)
                   if event.kind == EventKind.TOOL_STARTED and event.payload.get("tool") != "verification"}
            return len(ids)

        async def dispatch(name):
            await self.hooks.dispatch(config.hooks, name, {"run_id": run_id}, config.tools.root, emit=record)

        try:
            yield await record(EventKind.RUN_STARTED, {"task_id": normalized.id})
            enforce_privacy(config)
            await dispatch("before_route")
            decision = routing
            if classify:
                yield await record(EventKind.ROUTING_STARTED, {"router": router.id})
                router_task = normalized.model_copy(deep=True)
                router_task.context = {}
                if config.privacy.router_history:
                    router_task.context["routing_history"] = [
                        {"role": turn["role"], "content": turn["content"][:1500]}
                        for turn in normalized.context.get("conversation_history", [])[-6:]
                        if isinstance(turn, dict) and turn.get("role") in {"user", "assistant"}
                        and isinstance(turn.get("content"), str)
                    ]
                decision = await router.classify(router_task)
                trace.routing = decision
                yield await record(
                    EventKind.ROUTING_COMPLETED, {"decision": decision.model_dump(mode="json")}
                )
            if decision is not None:
                trace.routing = decision
            await dispatch("after_route")
            policies = []
            if decision and config.routing.pa_enabled:
                scores = {score.demand: score.confidence for score in decision.cognitive_demands}
                if scores[decision.primary_demand] >= config.routing.primary_threshold:
                    supporting = [
                        demand for demand in decision.supporting_demands
                        if scores[demand] >= config.routing.supporting_threshold
                    ][:config.routing.max_supporting_policies]
                    policies = self.registry.resolve_many([decision.primary_demand, *supporting])
            trace.policies = policies
            request = self.composer.compose(normalized, decision, policies)
            effective, plan = select_route(
                config, normalized, decision.assessment if decision else None,
                system_instructions=request.system_instructions,
            )
            trace.route_plan = plan
            yield await record(EventKind.ROUTE_SELECTED, plan.model_dump(mode="json"))
            config.executor = effective
            executor = self._executor_override or executor_from_config(effective)
            if hasattr(executor, "configure_runtime"):
                executor.configure_runtime(approve=self.approve, emit=record,
                    store_path=config.runs.path, root=config.tools.root,
                    budgets=config.budgets, hooks=self.hooks, hook_configs=config.hooks)
            trace.executor_id = f"{executor.id}-{executor.version}"
            for policy in policies:
                yield await record(
                    EventKind.POLICY_SELECTED, {"policy_id": policy.id, "version": policy.version}
                )

            request = request.model_copy(
                update={"executor_id": f"{executor.id}-{executor.version}"}
            )
            await dispatch("before_execution")
            yield await record(EventKind.EXECUTION_STARTED, {"executor": executor.id})
            output: list[str] = []
            async with self.mcp.connect(config.mcp if config.tools.enabled and effective.provider in API_PROVIDERS else [], local_only=config.privacy.local_only, root=config.tools.root) as connections:
                broker = ToolBroker(config, approve=self.approve, hooks=self.hooks, emit=record, mcp=connections) if config.tools.enabled else None
                if effective.provider in API_PROVIDERS and self._executor_override is None:
                    executor = APIExecutor(effective, broker=broker, budgets=config.budgets)
                execution = executor.execute(run_id, request)
                input_tokens, output_tokens = None, None
                try:
                    async for event in execution:
                        if event.run_id != run_id:
                            raise RuntimeError("Executor emitted an event for another run")
                        if event.kind == EventKind.USAGE:
                            summarize_usage([event])
                        if event not in trace.events:
                            trace.events.append(event)
                        if event.kind == EventKind.MODEL_STREAM:
                            output.append(str(event.payload.get("text", "")))
                        input_tokens, output_tokens = phase_tokens(summarize_usage(trace.events,
                            executor=effective.model_dump(mode="json")))
                        trace.result = ExecutionResult(output="".join(output),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            tool_calls=tool_count(broker))
                        await self.store.save(trace)
                        yield event
                finally:
                    await execution.aclose()
            trace.result = ExecutionResult(output="".join(output))
            input_tokens, output_tokens = phase_tokens(summarize_usage(trace.events,
                executor=effective.model_dump(mode="json")))
            trace.result = trace.result.model_copy(update={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tool_calls": tool_count(broker),
            })
            if self.store.effects(trace):
                raise RuntimeError("Execution ended with unresolved external effects; inspect and reconcile the run before continuing. Completion was not claimed.")
            await dispatch("before_verification")
            trace.verification = await verify_result(config, trace.result.output, approve=self.approve, emit=record)
            yield await record(EventKind.VERIFICATION_COMPLETED, trace.verification)
            if not trace.verification["passed"]:
                raise RuntimeError("Completion checks failed; run is not verified complete.")
            await dispatch("turn_complete")
            trace.completed_at = datetime.now(timezone.utc)
            verified = trace.verification["independent_checks"]
            trace.status = "completed" if verified else "unverified"
            yield await record(EventKind.RUN_COMPLETED, {
                "output_chars": len(trace.result.output), "verified": verified,
            })
        except (asyncio.CancelledError, GeneratorExit) as exc:
            trace.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "interrupted"
            trace.completed_at = datetime.now(timezone.utc)
            await record(
                EventKind.RUN_CANCELLED if trace.status == "cancelled" else EventKind.RUN_INTERRUPTED
            )
            raise
        except Exception as exc:
            trace.error = str(exc)
            trace.completed_at = datetime.now(timezone.utc)
            trace.status = "failed"
            yield await record(EventKind.RUN_FAILED, {"error": trace.error})
        finally:
            try:
                await self.store.save(trace)
                self._last_trace = trace
                if completed is not None:
                    completed.append(trace)
            finally:
                self.store.release(run_id)
