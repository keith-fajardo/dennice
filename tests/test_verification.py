import asyncio
from pathlib import Path

import pytest

from dennice.core.config import DenniceConfig, PermissionMode, ProviderConfig
from dennice.core.models import EventKind, RunEvent
from dennice.core.verification import verify_executor, verify_router


def test_local_connection_checks_are_ready_without_network() -> None:
    async def check() -> None:
        config = DenniceConfig()
        router, executor = await asyncio.gather(verify_router(config), verify_executor(config))
        assert router.ok
        assert executor.ok

    asyncio.run(check())


def test_live_executor_check_uses_one_bounded_call_in_temporary_workspace(monkeypatch):
    roots = []

    class FixtureExecutor:
        id = "claude"

        def configure_runtime(self, *, root, budgets, connection_check):
            roots.append(root)
            assert Path(root).is_dir()
            assert connection_check
            assert budgets.max_model_calls == 1
            assert budgets.max_tool_calls == 0
            assert budgets.max_output_tokens == 128
            assert budgets.max_total_tokens == 4096

        async def execute(self, run_id, request):
            assert request.task.prompt == "Reply exactly: Dennice connection OK."
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM,
                           payload={"text": "Dennice connection OK."})

    monkeypatch.setattr("dennice.core.verification.executor_from_config", lambda config: (
        FixtureExecutor() if config.permission_mode == PermissionMode.PLAN else None
    ))
    config = DenniceConfig(executor=ProviderConfig(provider="claude", model="fixture"))
    result = asyncio.run(verify_executor(config))
    assert result.ok and "bounded live request" in result.detail
    assert roots and not Path(roots[0]).exists()


def test_live_api_check_receives_budget_and_times_out(monkeypatch):
    class FixtureAPI:
        id = "openai-api"

        def __init__(self, config, *, budgets):
            assert config.permission_mode == PermissionMode.PLAN
            assert budgets.max_model_calls == 1
            assert budgets.max_total_tokens == 4096

        async def execute(self, run_id, request):
            await asyncio.sleep(1)
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM,
                           payload={"text": "late"})

    monkeypatch.setattr("dennice.core.verification.APIExecutor", FixtureAPI)
    config = DenniceConfig(executor=ProviderConfig(provider="openai-api", model="fixture"))
    config.budgets.max_seconds = 0.01
    result = asyncio.run(verify_executor(config))
    assert not result.ok and result.detail == "Live connection check timed out."


def test_live_router_check_has_elapsed_time_limit(monkeypatch):
    class FixtureRouter:
        async def classify(self, task):
            await asyncio.sleep(1)

    monkeypatch.setattr("dennice.core.verification.router_from_config", lambda *args, **kwargs: FixtureRouter())
    config = DenniceConfig(router=ProviderConfig(provider="codex", model="fixture"))
    config.budgets.max_seconds = 0.01
    result = asyncio.run(verify_router(config))
    assert not result.ok and result.detail == "Live router check timed out."


@pytest.mark.parametrize("component", ["executor", "router"])
def test_setup_withholds_unclassified_provider_exception_text(monkeypatch, component):
    sentinel = "private-fixture-token"

    class FixtureExecutor:
        id = "fixture"

        async def execute(self, run_id, request):
            raise ValueError(sentinel)
            yield  # pragma: no cover - makes this an async generator

    class FixtureRouter:
        async def classify(self, task):
            raise ValueError(sentinel)

    config = DenniceConfig(executor=ProviderConfig(provider="codex", model="fixture"),
                           router=ProviderConfig(provider="codex", model="fixture"))
    if component == "executor":
        monkeypatch.setattr("dennice.core.verification.executor_from_config", lambda _config: FixtureExecutor())
        result = asyncio.run(verify_executor(config))
    else:
        monkeypatch.setattr("dennice.core.verification.router_from_config", lambda *_args, **_kwargs: FixtureRouter())
        result = asyncio.run(verify_router(config))
    assert not result.ok
    assert sentinel not in result.detail
    assert "details were withheld" in result.detail
