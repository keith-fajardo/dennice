import pytest
from pydantic import ValidationError

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.config import DenniceConfig, PermissionMode, ProviderConfig, ReasoningEffort
from dennice.core.models import CognitiveScore, ExecutionRequest, RoutingDecision, Task
from dennice.executors.codex import CodexExecutor
from dennice.executors.claude import ClaudeExecutor
from dennice.executors.copilot import CopilotExecutor
from dennice.executors.factory import executor_from_config
from dennice.core.process import command_for_platform
from dennice.routing.factory import router_from_config


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


def test_codex_workspace_write_permission_is_explicit() -> None:
    executor = CodexExecutor(permission_mode=PermissionMode.WORKSPACE_WRITE)
    request = ExecutionRequest(task=Task(prompt="Investigate warehouse cost."), system_instructions="Policy")
    command = executor.command_for(request)
    assert ["--sandbox", "workspace-write"] == command[3:5]


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


def test_claude_write_profile_is_restricted_and_applies_effort_guidance() -> None:
    executor = ClaudeExecutor(
        reasoning_effort=ReasoningEffort.HIGH,
        permission_mode=PermissionMode.WORKSPACE_WRITE,
    )
    request = ExecutionRequest(task=Task(prompt="Investigate warehouse cost."), system_instructions="Policy")
    command = executor.command_for(request)
    assert ["--permission-mode", "dontAsk"] == command[5:7]
    assert "Use high effort" in command[8]
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"
    assert command[command.index("--disallowedTools") + 1] == "mcp__*"
    assert "--restricted" in command
    assert "--dangerously-skip-permissions" not in command


def test_read_only_claude_cannot_access_edit_or_shell_tools() -> None:
    executor = ClaudeExecutor(permission_mode=PermissionMode.READ_ONLY)
    request = ExecutionRequest(task=Task(prompt="Inspect."), system_instructions="Policy")
    command = executor.command_for(request)
    assert executor._permission_mode() == "dontAsk"
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"


def test_codex_plan_profile_enforces_read_only_and_planning_guidance() -> None:
    executor = CodexExecutor(permission_mode=PermissionMode.PLAN)
    request = ExecutionRequest(task=Task(prompt="Plan."), system_instructions="Policy")
    command = executor.command_for(request)
    assert command[4] == "read-only"
    assert "PLANNING MODE" in command[-1]


def test_copilot_executor_uses_subscription_cli_with_scoped_permissions() -> None:
    request = ExecutionRequest(task=Task(prompt="Inspect this project."), system_instructions="Use PA.")
    executor = CopilotExecutor(model="gpt-5.4", reasoning_effort=ReasoningEffort.HIGH)
    command = executor.command_for(request)
    assert command[:2] == ["copilot", "-p"]
    assert command[2].endswith("DENNICE USER TASK\nInspect this project.")
    assert "--model=gpt-5.4" in command
    assert "--effort=high" in command
    assert "--available-tools=view,grep,glob" in command
    assert "--allow-tool=read" in command
    assert not any("shell" in arg or "write" in arg.lower() for arg in command)
    assert executor_from_config(ProviderConfig(provider="copilot", model="default")).id == "copilot"


def test_copilot_plan_and_read_write_tool_profiles_are_explicit() -> None:
    request = ExecutionRequest(task=Task(prompt="Plan."), system_instructions="Policy")
    readonly = CopilotExecutor(permission_mode=PermissionMode.PLAN).command_for(request)
    assert "--plan" in readonly
    assert "--available-tools=view,grep,glob" in readonly
    writable = CopilotExecutor(permission_mode=PermissionMode.WORKSPACE_WRITE).command_for(request)
    assert "--available-tools=view,grep,glob,create,edit,apply_patch" in writable
    assert "--allow-tool=read,write" in writable
    assert not any("shell" in arg.lower() or "mcp" in arg.lower() for arg in writable)


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


def test_codex_router_migrates_stale_rule_model_to_provider_default() -> None:
    router = router_from_config(ProviderConfig(provider="codex", model="v1"))
    assert router.model == "default"
