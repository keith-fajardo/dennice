"""Durable, explicitly bounded objectives with evidence-based stopping."""

import asyncio
import time
import copy
from uuid import uuid4

from pydantic import BaseModel, Field

from dennice.core.models import EventKind, RunEvent, Task
from dennice.runs.store import LocalRunStore


class GoalState(BaseModel):
    id: str = Field(default_factory=lambda: f"goal_{uuid4().hex}")
    session_id: str
    objective: str = Field(min_length=1)
    status: str = "ready"
    max_runs: int = Field(default=3, ge=1, le=10)
    runs: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    elapsed_seconds: float = 0
    detail: str = ""
    context: dict = Field(default_factory=dict)


class GoalController:
    def __init__(self, path):
        self.store = LocalRunStore(path)

    def save(self, goal):
        with self.store._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            db.execute("INSERT INTO goals VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                       (goal.id, goal.model_dump_json()))

    def get(self, goal_id):
        with self.store._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            row = db.execute("SELECT state FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not row:
            raise ValueError("No such goal")
        return GoalState.model_validate_json(row[0])

    async def run_events(self, goal, harness, *, initial_task=None):
        config = harness.config.model_copy(deep=True)
        if initial_task is not None and not goal.runs:
            goal.context = copy.deepcopy(initial_task.context)
        if goal.status in {"complete", "cancelled"}:
            raise ValueError("Completed or cancelled goals cannot be resumed; create a new goal.")
        if goal.tokens_used >= config.budgets.max_total_tokens or goal.elapsed_seconds >= config.budgets.max_seconds or len(goal.runs) >= goal.max_runs:
            goal.status, goal.detail = "budget_exhausted", "Existing goal budget exhausted; create a new goal to authorize a new budget."
            self.save(goal)
            yield RunEvent(run_id=goal.id, kind=EventKind.GOAL_STATUS, payload=goal.model_dump(mode="json"))
            return
        prior = None
        if goal.runs:
            prior = await harness.store.get(goal.runs[-1])
            if harness.store.effects(prior):
                goal.status, goal.detail = "awaiting_input", "Previous run has unresolved external effects. Inspect and explicitly reconcile them before resuming; nothing was replayed."
                self.save(goal)
                yield RunEvent(run_id=goal.id, kind=EventKind.GOAL_STATUS, payload=goal.model_dump(mode="json"))
                return
        if not (config.verification.commands or config.verification.required_files):
            goal.status, goal.detail = "awaiting_input", "Configure independent completion checks before starting a goal. A nonempty answer is not proof of goal completion."
            self.save(goal)
            yield RunEvent(run_id=goal.id, kind=EventKind.GOAL_STATUS, payload=goal.model_dump(mode="json"))
            return
        goal.status = "running"
        start = time.monotonic()
        handoff = ""
        if prior:
            observed = [{"kind": event.kind.value, "details": event.payload}
                        for event in prior.events if event.kind in {EventKind.TOOL_COMPLETED, EventKind.HOOK_COMPLETED, EventKind.EFFECT_RECONCILED}]
            handoff = f"Previous run {prior.run_id}, status {prior.status}. Preserve its completed actions; do not blindly repeat effects.\nObserved action evidence: {observed!s}\nResponse: " + (prior.result.output if prior.result else "No response")
            handoff = handoff[-12000:]
        self.save(goal)
        try:
            async with asyncio.timeout(max(0.01, config.budgets.max_seconds - goal.elapsed_seconds)):
                while len(goal.runs) < goal.max_runs:
                    # Each continuation gets the remaining goal budget rather
                    # than a fresh full run allowance. The independent harness
                    # shares journal/trust/approvals without mutating UI config.
                    from dennice.core.harness import Harness
                    remaining = config.model_copy(deep=True)
                    remaining.budgets.max_total_tokens = max(1, config.budgets.max_total_tokens - goal.tokens_used)
                    remaining.budgets.max_seconds = max(0.01, config.budgets.max_seconds - goal.elapsed_seconds - (time.monotonic() - start))
                    execution_harness = Harness(remaining, router=harness._router_override,
                        executor=harness._executor_override, registry=harness.registry,
                        composer=harness.composer, store=harness.store,
                        hooks=harness.hooks, mcp=harness.mcp, approve=harness.approve)
                    context = copy.deepcopy(goal.context)
                    if handoff:
                        history = context.get("conversation_history", [])
                        context["conversation_history"] = (history if isinstance(history, list) else [])[-8:] + [{"role": "assistant", "content": handoff}]
                    task = Task(prompt=goal.objective, metadata={"session_id": goal.session_id, "goal_id": goal.id}, context=context)
                    run_id = None
                    async for event in execution_harness.run_events(task):
                        run_id = event.run_id
                        if event.kind == EventKind.RUN_STARTED:
                            goal.runs.append(run_id)
                            self.save(goal)
                        yield event
                    trace = await harness.store.get(run_id)
                    if trace.accounting:
                        goal.tokens_used += (trace.accounting.get("known_input_tokens_subtotal") or 0) + (trace.accounting.get("known_output_tokens_subtotal") or 0)
                    elif trace.result:
                        goal.tokens_used += (trace.result.input_tokens or 0) + (trace.result.output_tokens or 0)
                    if trace.status == "completed" and trace.verification and trace.verification.get("independent_checks") and trace.verification.get("passed"):
                        goal.status, goal.detail = "complete", "Configured independent completion checks passed."
                        break
                    if goal.tokens_used >= config.budgets.max_total_tokens:
                        goal.status, goal.detail = "budget_exhausted", "Goal token budget exhausted."
                        break
                    # Never automatically repeat a run after tool effects or
                    # uncertain failures. Continued action requires user input.
                    if any(event.kind in {EventKind.TOOL_STARTED, EventKind.HOOK_STARTED, EventKind.APPROVAL_REQUESTED} for event in trace.events) or trace.error:
                        goal.status, goal.detail = "awaiting_input", "Inspect the failed run before continuing; external effects may have occurred."
                        break
                    handoff = f"Previous run {run_id}: completion checks did not pass.\n" + (trace.result.output if trace.result else "No response")[-8000:]
                else:
                    goal.status, goal.detail = "budget_exhausted", "Goal run limit reached."
        except TimeoutError:
            goal.status, goal.detail = "budget_exhausted", "Goal elapsed-time budget exhausted."
        except asyncio.CancelledError:
            saved = self.get(goal.id)
            goal.status = saved.status if saved.status in {"paused", "cancelled"} else "paused"
            goal.detail = "Stopped; resume requires an explicit user command."
            raise
        except Exception:
            goal.status, goal.detail = "awaiting_input", "Goal stopped after an execution error; inspect its run before resuming."
            raise
        finally:
            goal.elapsed_seconds += time.monotonic() - start
            self.save(goal)
        yield RunEvent(run_id=goal.id, kind=EventKind.GOAL_STATUS, payload=goal.model_dump(mode="json"))
