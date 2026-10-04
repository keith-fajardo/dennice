"""Provider-neutral connection checks used by interactive setup surfaces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from tempfile import TemporaryDirectory

from dennice.core.config import DenniceConfig, PermissionMode
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.api import APIExecutor, API_PROVIDERS
from dennice.executors.factory import executor_from_config
from dennice.routing.factory import router_from_config


@dataclass(frozen=True)
class ConnectionCheck:
    component: str
    ok: bool
    detail: str


def _failure_detail(error: Exception) -> str:
    """Preserve adapter-authored diagnostics, but hide arbitrary SDK exception text."""
    if type(error) is RuntimeError:
        return str(error)
    return (
        f"Connection check failed ({type(error).__name__}); provider details were withheld. "
        "Check the provider CLI, authentication, model access, or endpoint locally."
    )


async def verify_router(config: DenniceConfig) -> ConnectionCheck:
    """Validate the chosen System 1 router with one typed classification."""
    router = router_from_config(config.router, jev=config.jev, openjev=config.openjev)
    if config.router.provider == "rule":
        return ConnectionCheck("System 1 router", True, "Rule router is local and ready.")
    try:
        async with asyncio.timeout(min(config.budgets.max_seconds, 30)):
            decision = await router.classify(Task(prompt="Connection check: classify this analytics task."))
    except TimeoutError:
        return ConnectionCheck("System 1 router", False, "Live router check timed out.")
    except Exception as error:
        return ConnectionCheck("System 1 router", False, _failure_detail(error))
    return ConnectionCheck(
        "System 1 router",
        True,
        f"{router.id} returned {decision.task_family} / {decision.primary_demand.value}.",
    )


async def verify_executor(config: DenniceConfig) -> ConnectionCheck:
    """Make one bounded live request in an empty temporary workspace."""
    if config.executor.provider == "mock":
        return ConnectionCheck("System 2 executor", True, "Mock executor is local and ready.")
    safe_config = config.executor.model_copy(update={"permission_mode": PermissionMode.PLAN})
    limits = config.budgets.model_copy(update={
        "max_seconds": min(config.budgets.max_seconds, 30),
        "max_model_calls": 1,
        "max_tool_calls": 0,
        "max_output_tokens": min(config.budgets.max_output_tokens, 128),
        "max_total_tokens": min(config.budgets.max_total_tokens, 4096),
    })
    executor = (APIExecutor(safe_config, budgets=limits)
                if safe_config.provider in API_PROVIDERS else executor_from_config(safe_config))
    request = ExecutionRequest(
        task=Task(prompt="Reply exactly: Dennice connection OK."),
        system_instructions="This is a connection check. Do not inspect, modify, or run tools.",
    )
    try:
        saw_output = False
        with TemporaryDirectory(prefix="dennice-executor-check-") as root:
            if hasattr(executor, "configure_runtime"):
                runtime = {"root": root, "budgets": limits}
                if safe_config.provider == "claude":
                    runtime["connection_check"] = True
                executor.configure_runtime(**runtime)
            async with asyncio.timeout(limits.max_seconds):
                async for _event in executor.execute("connection_check", request):
                    if _event.kind == EventKind.MODEL_STREAM and str(_event.payload.get("text", "")).strip():
                        saw_output = True
        if not saw_output:
            return ConnectionCheck("System 2 executor", False, "Provider completed without a response.")
    except TimeoutError:
        return ConnectionCheck("System 2 executor", False, "Live connection check timed out.")
    except Exception as error:
        return ConnectionCheck("System 2 executor", False, _failure_detail(error))
    return ConnectionCheck("System 2 executor", True, f"{executor.id} responded to one bounded live request.")
