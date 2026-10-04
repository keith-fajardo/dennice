"""Mock-only native Claude conformance tests; never invoke a provider."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dennice.core.config import BudgetConfig, PermissionMode, ReasoningEffort
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.claude import ClaudeExecutor
from dennice.runs.provider_sessions import ProviderSessionStore

NATIVE_ID = "d075ae2a-c847-4b94-915b-af14d8e597ca"


def request():
    return ExecutionRequest(
        task=Task(prompt="Current task", metadata={"session_id": "dennice-session"},
                  context={"conversation_history": [{"role": "user", "content": "OLD HISTORY"}]}),
        system_instructions="Current PA instructions",
    )


def successful_events(input_tokens=10, cost=.01):
    return [
        {"type": "system", "subtype": "init", "session_id": NATIVE_ID, "model": "claude-test"},
        {"type": "assistant", "message": {"id": "message-1", "content": [
            {"type": "tool_use", "id": "call-1", "name": "Read", "input": {"file_path": "README.md"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "file data"},
        ]}},
        {"type": "assistant", "message": {"id": "message-2", "content": [
            {"type": "text", "text": "Final answer"},
        ]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Final answer",
         "session_id": NATIVE_ID, "total_cost_usd": cost,
         "modelUsage": {"claude-test": {"inputTokens": input_tokens, "outputTokens": 5,
             "cacheReadInputTokens": 2, "cacheCreationInputTokens": 0, "contextWindow": 1000}},
         "usage": {"input_tokens": input_tokens, "output_tokens": 5,
             "cache_read_input_tokens": 2, "cache_creation_input_tokens": 0}},
    ]


def mock_process(monkeypatch, events, captures, returncode=0):
    async def spawn(*command, **options):
        captures.append((command, options))
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        for event in events:
            stdout.feed_data(json.dumps(event).encode() + b"\n")
        stdout.feed_eof()
        stderr.feed_eof()
        async def wait():
            return returncode
        return SimpleNamespace(stdout=stdout, stderr=stderr, wait=wait, returncode=returncode)
    async def stop(process):
        await process.wait()
    monkeypatch.setattr("dennice.executors.claude.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("dennice.executors.claude.stop_process", stop)


def configured(root):
    executor = ClaudeExecutor(reasoning_effort=ReasoningEffort.HIGH,
                              permission_mode=PermissionMode.READ_ONLY)
    executor.configure_runtime(root=root, store_path=root / "runs", budgets=BudgetConfig())
    return executor


def test_native_resume_tools_usage_and_updated_pa(tmp_path, monkeypatch):
    asyncio.run(_resume(tmp_path, monkeypatch))


async def _resume(root, monkeypatch):
    captures = []
    executor = configured(root)
    mock_process(monkeypatch, successful_events(), captures)
    first = [event async for event in executor.execute("run-1", request())]
    assert [e.kind for e in first].count(EventKind.TOOL_STARTED) == 1
    assert [e.kind for e in first].count(EventKind.TOOL_COMPLETED) == 1
    assert [e.payload["text"] for e in first if e.kind == EventKind.MODEL_STREAM] == ["Final answer"]
    usage = next(e.payload for e in first if e.kind == EventKind.USAGE)
    assert usage["input_tokens"] == 10 and usage["cost_amount"] == .01
    assert usage["context_tokens"] == 17 and usage["context_window_tokens"] == 1000
    assert "subscription_billing" in usage["source"]
    initial = captures[0][0]
    assert "OLD HISTORY" in initial[-1] and "--resume" not in initial
    assert initial[initial.index("--effort") + 1] == "high"
    assert initial[initial.index("--max-turns") + 1] == "8"
    assert captures[0][1]["cwd"] == str(root)
    state = ProviderSessionStore(root / "runs").get("dennice-session", "claude", str(root))
    assert state["session_id"] == NATIVE_ID and state["status"] == "completed"
    later = successful_events(input_tokens=17, cost=.015)
    later[-1]["modelUsage"]["claude-test"]["outputTokens"] = 8
    mock_process(monkeypatch, later, captures)
    followup = request().model_copy(update={"system_instructions": "UPDATED PA"})
    followup.task.context["compaction_summary"] = "IMPORTANT EARLIER REQUIREMENT"
    second = [event async for event in executor.execute("run-2", followup)]
    resumed = captures[-1][0]
    assert resumed[resumed.index("--resume") + 1] == NATIVE_ID
    assert "OLD HISTORY" not in resumed[-1]
    assert "IMPORTANT EARLIER REQUIREMENT" in resumed[-1]
    assert "UPDATED PA" in resumed[resumed.index("--append-system-prompt") + 1]
    usage = next(e.payload for e in second if e.kind == EventKind.USAGE)
    assert usage["input_tokens"] == 7 and usage["output_tokens"] == 3
    assert usage["cost_amount"] == pytest.approx(.005)


@pytest.mark.parametrize("case", ["missing_result", "permission_denied", "is_error", "empty_final", "wrong_session", "aborted"])
def test_native_incomplete_turn_never_claims_completion(tmp_path, monkeypatch, case):
    asyncio.run(_failed(tmp_path, monkeypatch, case))


async def _failed(root, monkeypatch, case):
    events = successful_events()
    if case == "missing_result":
        events.pop()
    elif case == "permission_denied":
        events[-1]["permission_denials"] = [{"tool_name": "Write"}]
    elif case == "is_error":
        events[-1]["is_error"] = True
    elif case == "empty_final":
        events[-1]["result"] = ""
    elif case == "wrong_session":
        events[-1]["session_id"] = "another-session"
    elif case == "aborted":
        events[-1]["terminal_reason"] = "aborted_tools"
    captures = []
    mock_process(monkeypatch, events, captures)
    executor = configured(root)
    with pytest.raises(RuntimeError):
        _ = [event async for event in executor.execute("run", request())]
    state = ProviderSessionStore(root / "runs").get("dennice-session", "claude", str(root))
    assert state["status"] != "completed"
    with pytest.raises(RuntimeError, match="prior native Claude turn"):
        _ = [event async for event in executor.execute("retry", request())]
    assert len(captures) == 1


def test_usage_missing_is_unknown_and_regression_rejected():
    usage, totals = ClaudeExecutor._usage({}, None, "attempt")
    assert not usage["reported"] and usage["input_tokens"] is None and not totals
    previous = {"session_id": NATIVE_ID, "usage": {"inputTokens": 20, "outputTokens": 5,
                "cacheReadInputTokens": 2, "cacheCreationInputTokens": 0}}
    with pytest.raises(RuntimeError, match="regressed"):
        ClaudeExecutor._usage(successful_events()[-1], previous, "attempt")


def test_native_profile_does_not_widen_shell_or_mcp_authority(tmp_path):
    executor = configured(tmp_path)
    command = executor.command_for(request())
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
    assert command[command.index("--disallowedTools") + 1] == "mcp__*"
    assert "--restricted" in command
    assert "--dangerously-skip-permissions" not in command
    assert "--permission-prompt-tool" not in command
