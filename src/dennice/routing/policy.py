"""Deterministic model selection. The router cannot grant authority or name models."""

from dennice.core.config import DenniceConfig, ReasoningEffort
from dennice.core.models import RoutePlan, Task, TaskAssessment


def enforce_privacy(config: DenniceConfig) -> None:
    if config.privacy.local_only and (
        config.router.provider not in {"rule", "openjev"}
        or config.executor.provider not in {"mock", "local"}
        or any(server.enabled for server in config.mcp)
        or any(hook.enabled for hook in config.hooks)
    ):
        raise PermissionError("Local-only mode rejects remote/CLI providers and unsandboxed hooks/MCP processes.")


def select_route(config: DenniceConfig, task: Task, assessment: TaskAssessment | None):
    current = config.executor.model_copy(deep=True)
    route = config.routing
    plan = RoutePlan(mode=route.mode, recommended_model=current.model,
                     effective_model=current.model, reason="Fixed execution configuration.",
                     effective_effort=current.reasoning_effort.value if current.reasoning_effort else None)
    if route.mode == "fixed":
        return current, plan
    assessment = assessment or TaskAssessment()
    tier = "strong" if (
        assessment.stakes in {"high", "unknown"}
        or assessment.uncertainty == "high"
        or assessment.complexity in {"complex", "unknown"}
    ) else "lightweight" if assessment.complexity == "simple" and assessment.stakes == "low" else "balanced"
    # The conservative estimate includes PA/history/image overhead; capability
    # declarations are operator-managed, never inferred from a model's name.
    estimated_context = len(task.prompt + str(task.context)) + 6000
    # Provider identity is an explicit operator choice. Cognitive routing can
    # choose among that provider's candidates, never change billing/login
    # boundaries by switching to another provider's pool.
    eligible = [candidate for candidate in route.model_pool.get(current.provider, [])
                if candidate.enabled and candidate.context_tokens >= estimated_context
                and (not (task.context.get("images") or assessment.requires_vision) or candidate.vision)
                and (not (assessment.requires_tools or config.tools.enabled) or candidate.tools)]
    ranks = {"lightweight": 0, "balanced": 1, "strong": 2}
    eligible = [c for c in eligible if ranks[c.tier] >= ranks[tier]]
    if route.model_pinned:
        eligible = [c for c in eligible if c.model == current.model]
    if route.effort_pinned and current.reasoning_effort:
        eligible = [c for c in eligible if current.reasoning_effort in c.efforts]
    if not eligible:
        if route.mode == "auto":
            raise ValueError("No approved capability-compatible model satisfies the route and pins. Configure the pool or use fixed mode; execution was not started.")
        plan.reason = "No approved capability-compatible candidate; shadow mode retained explicit configuration."
        return current, plan
    chosen = min(eligible, key=lambda c: (ranks[c.tier], c.model))
    effort = current.reasoning_effort
    if not route.effort_pinned:
        desired = ReasoningEffort.HIGH if tier == "strong" else ReasoningEffort.MEDIUM if tier == "balanced" else ReasoningEffort.LOW
        effort = desired if desired in chosen.efforts else None
    plan.recommended_model = chosen.model
    plan.recommended_effort = effort.value if effort else None
    plan.reason = f"{tier} route from complexity={assessment.complexity}, stakes={assessment.stakes}, uncertainty={assessment.uncertainty}; same approved provider."
    if route.mode == "auto":
        current.model, current.reasoning_effort = chosen.model, effort
        plan.effective_model, plan.effective_effort = chosen.model, plan.recommended_effort
        plan.applied = True
    return current, plan
