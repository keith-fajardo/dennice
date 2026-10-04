"""The CLI must not report a failed execution as a successful shell command."""

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dennice.core.models import ExecutionResult, RunTrace, Task


cli = import_module("dennice.cli.app")


def _trace(status, *, output="", error=None):
    return RunTrace(
        run_id="run-fixture", started_at=datetime.now(timezone.utc),
        task=Task(prompt="analyze"), executor_id="mock-v1", status=status,
        result=ExecutionResult(output=output), error=error,
    )


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled", "interrupted"])
def test_run_failure_shows_error_and_returns_nonzero(monkeypatch, status):
    trace = _trace(status, output="partial work", error="Execution stopped")

    async def run(task):
        return trace

    monkeypatch.setattr(cli.Harness, "from_config", lambda: SimpleNamespace(run=run))
    result = CliRunner().invoke(cli.app, ["run", "analyze"])
    assert result.exit_code == 1
    assert "Partial response (unverified)" in result.stdout
    assert "partial work" in result.stdout
    assert f"Run stopped ({status})" in result.stdout

    machine = CliRunner().invoke(cli.app, ["run", "analyze", "--json"])
    assert machine.exit_code == 1
    assert f'"status": "{status}"' in machine.stdout


def test_inspect_preserves_status_partial_output_and_failure_reason(monkeypatch):
    trace = _trace("timed_out", output="partial work", error="Run time budget exhausted")

    async def get(run_id):
        assert run_id == "run-fixture"
        return trace

    monkeypatch.setattr(cli.Harness, "from_config", lambda: SimpleNamespace(store=SimpleNamespace(get=get)))
    result = CliRunner().invoke(cli.app, ["inspect", "run-fixture"])
    assert result.exit_code == 0
    assert "Status: timed_out" in result.stdout
    assert "partial work" in result.stdout
    assert "Run time budget exhausted" in result.stdout


def test_unverified_response_is_reported_without_claiming_verification(monkeypatch):
    trace = _trace("unverified", output="answer")

    async def run(task):
        return trace

    monkeypatch.setattr(cli.Harness, "from_config", lambda: SimpleNamespace(run=run))
    result = CliRunner().invoke(cli.app, ["run", "analyze"])
    assert result.exit_code == 0
    assert "(unverified)" in result.stdout
    assert "answer" in result.stdout
