import pytest
from pydantic import ValidationError

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.config import DenniceConfig, ProviderConfig, ReasoningEffort
from dennice.core.models import CognitiveScore, ExecutionRequest, RoutingDecision, Task
from dennice.executors.codex import CodexExecutor
from dennice.executors.factory import executor_from_config


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
