import asyncio
import json
import sys
from pathlib import Path

import pytest

from dennice.core.config import BudgetConfig
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.copilot import CopilotExecutor


def otel_span(input_tokens=8, output_tokens=3):
    return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{
        "name": "invoke_agent",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": str(input_tokens)}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": str(output_tokens)}},
            {"key": "github.copilot.turn_count", "value": {"intValue": "4"}},
        ],
    }]}]}]}) + "\n"


def test_copilot_stream_preserves_utf8_split_across_pipe_chunks(monkeypatch):
    original = asyncio.create_subprocess_exec
    captured = {}

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        captured.update(kwargs["env"])
        with open(kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"], "w") as telemetry:
            telemetry.write(otel_span())
        program = (
            "import os,time; os.write(1,b'\\xe3'); time.sleep(.05); "
            "os.write(1,b'\\x81\\x82')"
        )
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def journey():
        request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")
        executor = CopilotExecutor()
        executor.configure_runtime(budgets=BudgetConfig(max_total_tokens=100))
        events = [event async for event in executor.execute("utf8", request)]
        output = "".join(event.payload["text"] for event in events
                         if event.kind == EventKind.MODEL_STREAM)
        assert output == "あ"
        usage = next(event.payload for event in events if event.kind == EventKind.USAGE)
        assert (usage["input_tokens"], usage["output_tokens"]) == (8, 3)
        assert captured["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
        assert not Path(captured["COPILOT_OTEL_FILE_EXPORTER_PATH"]).parent.exists()

    asyncio.run(journey())


def test_copilot_does_not_inherit_byok_provider_configuration(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec
    observed = {}
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "do-not-leak")
    monkeypatch.setenv("COPILOT_PROVIDER_TYPE", "openai")
    monkeypatch.setenv("COPILOT_PROVIDERS_CONFIG", str(tmp_path / "user-providers.json"))
    monkeypatch.setenv("COPILOT_MODEL", "custom-openai-model")

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        observed.update(kwargs["env"])
        observed["registry"] = Path(kwargs["env"]["COPILOT_PROVIDERS_CONFIG"]).read_text()
        with open(kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"], "w") as telemetry:
            telemetry.write(otel_span())
        return await original(sys.executable, "-u", "-c", "print('answer')", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    executor = CopilotExecutor()
    executor.configure_runtime(root=tmp_path)
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        _ = [event async for event in executor.execute("no-byok", request)]
        assert not any(key.startswith("COPILOT_PROVIDER_") for key in observed)
        assert "COPILOT_MODEL" not in observed
        assert json.loads(observed["registry"]) == {"providers": [], "models": []}
        assert not Path(observed["COPILOT_PROVIDERS_CONFIG"]).exists()

    asyncio.run(journey())


@pytest.mark.parametrize("name", ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"])
def test_copilot_rejects_environment_auth_override_before_startup(monkeypatch, name):
    monkeypatch.setenv(name, "fixture-secret")

    def unexpected(*_args, **_kwargs):
        pytest.fail("Copilot must reject a potentially different account before process startup")

    monkeypatch.setattr("dennice.executors.copilot.tempfile.mkdtemp", unexpected)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected)
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        with pytest.raises(RuntimeError, match=f"authentication is overridden by {name}"):
            _ = [event async for event in CopilotExecutor().execute("auth-override", request)]

    asyncio.run(journey())


def test_copilot_missing_usage_fails_budgeted_run(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        program = "print('answer')"
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    executor = CopilotExecutor()
    executor.configure_runtime(root=tmp_path, budgets=BudgetConfig())
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        with pytest.raises(RuntimeError, match="without valid local token usage telemetry"):
            _ = [event async for event in executor.execute("missing-usage", request)]

    asyncio.run(journey())


def test_copilot_spawn_failure_cleans_telemetry_directory(monkeypatch, tmp_path):
    telemetry_dir = tmp_path / "telemetry"

    def make_telemetry_dir(**_kwargs):
        telemetry_dir.mkdir()
        return str(telemetry_dir)

    async def fail_to_spawn(*_args, **_kwargs):
        raise PermissionError("fixture spawn denied")

    monkeypatch.setattr("dennice.executors.copilot.tempfile.mkdtemp", make_telemetry_dir)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_to_spawn)
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        with pytest.raises(PermissionError, match="fixture spawn denied"):
            _ = [event async for event in CopilotExecutor().execute("spawn-failure", request)]
        assert not telemetry_dir.exists()

    asyncio.run(journey())


def test_copilot_over_budget_run_is_rejected(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        with open(kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"], "w") as telemetry:
            telemetry.write(otel_span(80, 21))
        return await original(sys.executable, "-u", "-c", "print('partial')", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    executor = CopilotExecutor()
    executor.configure_runtime(root=tmp_path, budgets=BudgetConfig(max_total_tokens=100))
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        with pytest.raises(RuntimeError, match="101 tokens.*100-token per-run limit"):
            _ = [event async for event in executor.execute("over-budget", request)]

    asyncio.run(journey())


def test_copilot_model_call_limit_is_checked_from_usage_span(monkeypatch, tmp_path):
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        with open(kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"], "w") as telemetry:
            telemetry.write(otel_span())
        return await original(sys.executable, "-u", "-c", "print('partial')", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    executor = CopilotExecutor()
    executor.configure_runtime(root=tmp_path, budgets=BudgetConfig(max_model_calls=2))
    request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")

    async def journey():
        with pytest.raises(RuntimeError, match="model-call total.*could not be verified"):
            _ = [event async for event in executor.execute("too-many-calls", request)]

    asyncio.run(journey())


def test_copilot_usage_parser_uses_only_top_level_agent_span(tmp_path):
    document = json.loads(otel_span(10, 2))
    child = json.loads(otel_span(1000, 2000))["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    child["parentSpanId"] = "1234567890abcdef"
    document["resourceSpans"][0]["scopeSpans"][0]["spans"].append(child)
    path = tmp_path / "telemetry.jsonl"
    path.write_text(json.dumps(document) + "\n", encoding="utf-8")
    assert CopilotExecutor._read_otel_usage(path) == (10, 2, 4)


def test_copilot_usage_parser_rejects_malformed_jsonl(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    path.write_text("{broken\n", encoding="utf-8")
    assert CopilotExecutor._read_otel_usage(path) is None
