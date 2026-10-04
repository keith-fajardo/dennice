"""Provider-neutral connection checks used by interactive setup surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from dennice.core.config import DenniceConfig, PermissionMode
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.factory import executor_from_config
from dennice.routing.factory import router_from_config


@dataclass(frozen=True)
class ConnectionCheck:
    component: str
    ok: bool
    detail: str


async def verify_router(config: DenniceConfig) -> ConnectionCheck:
    """Validate the chosen System 1 router with one typed classification."""
    router = router_from_config(config.router, jev=config.jev, openjev=config.openjev)
    if config.router.provider == "rule":
        return ConnectionCheck("System 1 router", True, "Rule router is local and ready.")
    try:
        decision = await router.classify(Task(prompt="Connection check: classify this analytics task."))
    except Exception as error:  # Adapter details are returned verbatim for setup remediation.
        return ConnectionCheck("System 1 router", False, str(error))
    return ConnectionCheck(
        "System 1 router",
        True,
        f"{router.id} returned {decision.task_family} / {decision.primary_demand.value}.",
    )


async def verify_executor(config: DenniceConfig) -> ConnectionCheck:
    """Validate System 2 with a minimal, non-writing request."""
    executor = executor_from_config(config.executor.model_copy(update={"permission_mode": PermissionMode.READ_ONLY}))
    if config.executor.provider == "mock":
        return ConnectionCheck("System 2 executor", True, "Mock executor is local and ready.")
    request = ExecutionRequest(
        task=Task(prompt="Reply exactly: Dennice connection OK."),
        system_instructions="This is a connection check. Do not inspect, modify, or run tools.",
    )
    try:
        saw_output = False
        async for _event in executor.execute("connection_check", request):
            if _event.kind == EventKind.MODEL_STREAM and str(_event.payload.get("text", "")).strip():
                saw_output = True
        if not saw_output:
            return ConnectionCheck("System 2 executor", False, "Provider completed without a response.")
    except Exception as error:  # The adapter's provider-specific diagnostic is useful to the user.
        return ConnectionCheck("System 2 executor", False, str(error))
    return ConnectionCheck("System 2 executor", True, f"{executor.id} responded in safe connection-check mode.")
