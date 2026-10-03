import pytest
from pydantic import ValidationError

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.config import DenniceConfig, ProviderConfig, ReasoningEffort
from dennice.core.models import CognitiveScore, ExecutionRequest, RoutingDecision, Task
from dennice.executors.codex import CodexExecutor
from dennice.executors.claude import ClaudeExecutor
from dennice.executors.factory import executor_from_config
from dennice.core.process import command_for_platform


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=1.01)


def test_primary_must_be_scored() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(
            task_family="x",
            cognitive_demands=[CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=0.8)],
            primary_demand=CognitiveDemand.EMPIRICAL_INDUCTION,
        )


def test_executor_config_round_trips_to_yaml(tmp_path) -> None:
    path = tmp_path / "dennice.yaml"
    config = DenniceConfig(
        executor=ProviderConfig(
            provider="codex", model="default", reasoning_effort=ReasoningEffort.HIGH
        )
    )
    config.save(path)
    loaded = DenniceConfig.load(path)
    assert loaded.executor.provider == "codex"
    assert loaded.executor.model == "default"
    assert loaded.executor.reasoning_effort == ReasoningEffort.HIGH


def test_codex_executor_is_read_only_and_uses_composed_prompt() -> None:
    executor = CodexExecutor(model="gpt-test", reasoning_effort=ReasoningEffort.HIGH)
    request = ExecutionRequest(task=Task(prompt="Investigate warehouse cost."), system_instructions="Policy")
    command = executor.command_for(request)
    assert command[:3] == ["codex", "exec", "--json"]
    assert ["--sandbox", "read-only"] == command[3:5]
    assert "--ephemeral" in command
    assert ["--model", "gpt-test"] == command[command.index("--model") : command.index("--model") + 2]
    assert ["--config", 'model_reasoning_effort="high"'] == command[
        command.index("--config") : command.index("--config") + 2
    ]
    assert command[-1] == "Policy\n\nUSER TASK\nInvestigate warehouse cost."
    assert executor_from_config(ProviderConfig(provider="codex", model="default")).id == "codex"


def test_claude_executor_uses_local_subscription_cli_and_composed_prompt() -> None:
    executor = ClaudeExecutor(model="sonnet")
    request = ExecutionRequest(task=Task(prompt="Investigate warehouse cost."), system_instructions="Policy")
    command = executor.command_for(request)
    assert command[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert ["--permission-mode", "plan"] == command[5:7]
    assert ["--append-system-prompt", "Policy"] == command[7:9]
    assert ["--model", "sonnet"] == command[9:11]
    assert command[-1] == "Investigate warehouse cost."
    assert executor_from_config(ProviderConfig(provider="claude", model="default")).id == "claude"


def test_claude_executor_extracts_only_assistant_text_events() -> None:
    event = (
        b'{"type":"assistant","message":{"content":['
        b'{"type":"text","text":"Investigate daily credits."},'
        b'{"type":"tool_use","name":"Read"}]}}\n'
    )
    assert ClaudeExecutor._event_text(event) == "Investigate daily credits."
    assert ClaudeExecutor._event_text(b'{"type":"result"}\n') is None


def test_windows_provider_commands_use_bash() -> None:
    assert command_for_platform(["codex", "exec", "hello world"], "win32") == [
        "bash",
        "-lc",
        "codex exec 'hello world'",
    ]
