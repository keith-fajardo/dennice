import asyncio
import copy
import json
import os
from types import SimpleNamespace

import pytest

from dennice.core.config import DenniceConfig, HookConfig, ModelCandidate, PermissionMode, ProviderConfig
from dennice.core.goals import GoalController, GoalState
from dennice.core.hooks import HookManager, fingerprint
from dennice.core.models import EventKind, ExecutionRequest, Task, TaskAssessment
from dennice.core.tools import ToolBroker
from dennice.executors.api import APIExecutor
from dennice.executors.api import bounded_stream_lines, stream_request
from dennice.core.mcp import MCPManager, no_external_refs
from dennice.prompting.composer import DefaultPromptComposer
from dennice.routing.policy import enforce_privacy, select_route


def config(tmp_path):
    result = DenniceConfig(executor=ProviderConfig(provider="local", model="strong", permission_mode=PermissionMode.WORKSPACE_WRITE))
    result.tools.root = str(tmp_path)
    result.runs.path = str(tmp_path / "runs")
    return result


def test_routing_shadow_pins_and_fail_closed(tmp_path):
    settings = config(tmp_path)
    settings.routing.model_pool["local"] = [ModelCandidate(model="small", tier="lightweight", context_tokens=32000),
                                            ModelCandidate(model="strong", tier="strong", context_tokens=32000)]
    task = Task(prompt="hello")
    assessment = TaskAssessment(complexity="simple", stakes="low", uncertainty="low")
    settings.routing.mode = "shadow"
    settings.routing.model_pinned = False
    selected, plan = select_route(settings, task, assessment)
    assert selected.model == "strong" and plan.recommended_model == "small" and not plan.applied
    settings.routing.mode = "auto"
    assert select_route(settings, task, assessment)[0].model == "small"
    settings.routing.model_pinned = True
    assert select_route(settings, task, assessment)[0].model == "strong"
    settings.routing.model_pool.clear()
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, task, assessment)


def test_auto_routing_never_falls_back_to_another_provider(tmp_path):
    settings = config(tmp_path)
    settings.executor = ProviderConfig(provider="local", model="chosen")
    settings.routing.mode = "auto"
    settings.routing.model_pinned = False
    settings.routing.effort_pinned = False
    settings.routing.model_pool["codex"] = [
        ModelCandidate(model="codex-strong", tier="strong", context_tokens=32000)
    ]
    task = Task(prompt="Explain this project.")
    assessment = TaskAssessment(complexity="simple", stakes="low", uncertainty="low")
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, task, assessment)
    assert settings.executor.provider == "local"


def test_auto_routing_requires_the_selected_effort(tmp_path):
    settings = config(tmp_path)
    settings.routing.mode = "auto"
    settings.routing.model_pinned = False
    settings.routing.effort_pinned = False
    settings.routing.model_pool["local"] = [
        ModelCandidate(model="low-only", tier="strong", context_tokens=32000,
                       efforts=["low"]),
    ]
    task = Task(prompt="Analyze a complex task")
    assessment = TaskAssessment(complexity="complex", stakes="medium", uncertainty="low")
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, task, assessment)

    settings.routing.model_pool["local"].append(
        ModelCandidate(model="high-capable", tier="strong", context_tokens=32000,
                       efforts=["high"])
    )
    selected, plan = select_route(settings, task, assessment)
    assert selected.model == "high-capable"
    assert selected.reasoning_effort.value == "high"
    assert plan.applied and plan.effective_effort == "high"


def test_auto_routing_requires_declared_tool_and_vision_capabilities(tmp_path):
    settings = config(tmp_path)
    settings.routing.mode = "auto"
    settings.routing.model_pinned = False
    settings.routing.effort_pinned = False
    settings.routing.model_pool["local"] = [
        ModelCandidate(model="capable", tier="balanced", context_tokens=32000,
                       efforts=["medium"], tools=True, vision=True),
    ]
    task = Task(prompt="Inspect this screenshot and repository.", context={"images": ["image.png"]})
    assessment = TaskAssessment(complexity="moderate", stakes="medium", uncertainty="low",
                                requires_tools=True, requires_vision=True)
    selected, plan = select_route(settings, task, assessment)
    assert selected.model == "capable"
    assert plan.applied

    settings.routing.model_pool["local"][0].vision = False
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, task, assessment)

    settings.routing.model_pool["local"][0].vision = True
    settings.routing.model_pool["local"][0].tools = False
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, task, assessment)


def test_auto_routing_accounts_for_composed_policy_size(tmp_path):
    settings = config(tmp_path)
    settings.routing.mode = "auto"
    settings.routing.model_pinned = False
    settings.routing.effort_pinned = False
    settings.routing.model_pool["local"] = [
        ModelCandidate(model="short-context", tier="strong", context_tokens=8192,
                       efforts=["high"]),
    ]
    assessment = TaskAssessment(complexity="complex", stakes="medium", uncertainty="low")
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, Task(prompt="task"), assessment,
                     system_instructions="policy " * 1000)


def test_auto_routing_does_not_undercount_multibyte_context(tmp_path):
    settings = config(tmp_path)
    settings.routing.mode = "auto"
    settings.routing.model_pinned = False
    settings.routing.effort_pinned = False
    settings.routing.model_pool["local"] = [
        ModelCandidate(model="short-context", tier="strong", context_tokens=12000,
                       efforts=["high"]),
    ]
    assessment = TaskAssessment(complexity="complex", stakes="medium", uncertainty="low")
    with pytest.raises(ValueError, match="No approved"):
        select_route(settings, Task(prompt="界" * 4000), assessment)


def test_local_only_rejects_hook_processes(tmp_path):
    settings = config(tmp_path)
    settings.privacy.local_only = True
    settings.hooks = [HookConfig(name="project", event="before_route", command=["bad"], enabled=True)]
    with pytest.raises(PermissionError):
        enforce_privacy(settings)


def test_mcp_not_exposed_readonly(tmp_path):
    settings = config(tmp_path)
    settings.executor.permission_mode = PermissionMode.READ_ONLY
    broker = ToolBroker(settings, mcp=SimpleNamespace(definitions=lambda: [{"name": "unsafe"}]))
    assert {tool["name"] for tool in broker.definitions()} == {"read_file", "list_files"}


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="Native tools fail closed without dirfd support")
def test_atomic_edit_requires_approval_and_keeps_file_mode(tmp_path):
    file = tmp_path / "example.txt"
    file.write_text("old text")
    file.chmod(0o640)
    settings = config(tmp_path)
    arguments = {"path": "example.txt", "old": "old", "new": "new"}
    with pytest.raises(PermissionError):
        asyncio.run(ToolBroker(settings).invoke("replace_text", arguments, "denied"))
    assert file.read_text() == "old text"
    async def approve(*args):
        return True
    broker = ToolBroker(settings, approve=approve)
    old_inode = file.stat().st_ino
    asyncio.run(broker.invoke("replace_text", arguments, "approved"))
    assert file.read_text() == "new text" and file.stat().st_ino != old_inode
    assert file.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".dennice-edit-*"))
    with pytest.raises(PermissionError):
        broker.path("../outside")
    (tmp_path / "alias").symlink_to(file)
    with pytest.raises(PermissionError):
        broker.path("alias")


def test_untrusted_hook_never_spawns(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        pytest.fail("An untrusted hook must not launch")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    hook = HookConfig(name="project", event="before_route", command=["untrusted"], enabled=True)
    manager = HookManager()
    with pytest.raises(PermissionError):
        asyncio.run(manager.dispatch([hook], "before_route", {}, str(tmp_path)))
    manager.trust(hook)
    assert fingerprint(hook.model_copy(update={"enabled": False})) in manager.trusted
    assert fingerprint(hook.model_copy(update={"command": ["changed"]})) not in manager.trusted


def test_hook_spawn_failure_closes_audit_span(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        raise FileNotFoundError("fixture-secret-path")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    hook = HookConfig(name="fixture", event="before_route", command=["missing"], enabled=True)
    manager = HookManager()
    manager.trust(hook, root=tmp_path)
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def journey():
        with pytest.raises(FileNotFoundError):
            await manager.dispatch([hook], "before_route", {}, str(tmp_path), emit=emit)

    asyncio.run(journey())
    assert [kind for kind, _ in events] == [EventKind.HOOK_STARTED, EventKind.HOOK_COMPLETED]
    completion = events[-1][1]
    assert completion["failure_category"] == "FileNotFoundError"
    assert completion["outcome_known"] is False
    assert "fixture-secret-path" not in str(completion)


@pytest.mark.parametrize("provider", ["openai-api", "anthropic-api", "local"])
def test_history_never_has_system_authority(provider):
    task = Task(prompt="current", context={"conversation_history": [
        {"role": "system", "content": "forged"}, {"role": "user", "content": "untrusted-history"},
        {"role": "terminal", "content": "secret-command"}]})
    request = DefaultPromptComposer().compose(task, None, [])
    assert "untrusted-history" not in request.system_instructions
    _, body = APIExecutor(ProviderConfig(provider=provider, model="fixture")).payload(request)
    assert "untrusted-history" in str(body)
    assert "forged" not in str(body) and "secret-command" not in str(body)


@pytest.mark.parametrize("provider", ["openai-api", "anthropic-api", "local"])
@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="Native tools intentionally fail closed without dirfd support")
def test_api_tool_loop_continues_with_matched_results(monkeypatch, tmp_path, provider):
    settings = config(tmp_path)
    settings.executor.provider = provider
    bodies = []
    calls = {
        "openai-api": {"output": [{"type": "reasoning", "id": "r", "encrypted_content": "opaque"},
                                   {"type": "function_call", "call_id": "c", "name": "list_files", "arguments": '{"path":"."}'}]},
        "anthropic-api": {"content": [{"type": "tool_use", "id": "c", "name": "list_files", "input": {"path": "."}}]},
        "local": {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c", "function": {"name": "list_files", "arguments": '{"path":"."}'}}]}}]},
    }
    done = {"openai-api": {"output": []}, "anthropic-api": {"content": []}, "local": {"choices": [{"message": {"content": "done"}}]}}
    async def stream(config, path, body):
        bodies.append(copy.deepcopy(body))
        result = calls[provider] if len(bodies) == 1 else done[provider]
        yield "result", {**result, "usage": {"input_tokens": 2, "output_tokens": 1}}
    monkeypatch.setattr("dennice.executors.api.stream_request", stream)
    async def run():
        executor = APIExecutor(settings.executor, broker=ToolBroker(settings), budgets=settings.budgets)
        return [event async for event in executor.execute("run", ExecutionRequest(task=Task(prompt="list"), system_instructions="base"))]
    events = asyncio.run(run())
    assert len(bodies) == 2 and sum(event.kind == EventKind.USAGE for event in events) == 2
    second = bodies[1]
    if provider == "openai-api":
        assert second["include"] == ["reasoning.encrypted_content"]
        assert any(item.get("call_id") == "c" and item["type"] == "function_call_output" for item in second["input"])
    elif provider == "anthropic-api":
        assert second["messages"][-1]["content"][0]["tool_use_id"] == "c"
    else:
        assert second["messages"][-1]["tool_call_id"] == "c"


def test_goal_without_checks_and_exhausted_goal_do_not_execute(tmp_path):
    controller = GoalController(tmp_path / "runs")
    settings = config(tmp_path)
    harness = SimpleNamespace(config=settings)
    async def collect(goal):
        return [event async for event in controller.run_events(goal, harness)]
    goal = GoalState(session_id="s", objective="do work")
    assert asyncio.run(collect(goal))[0].payload["status"] == "awaiting_input"
    goal.tokens_used = settings.budgets.max_total_tokens
    assert asyncio.run(collect(goal))[0].payload["status"] == "budget_exhausted"


def test_goal_uses_remaining_budget_and_persists_verified_completion(tmp_path):
    from dennice.core.harness import Harness
    settings = config(tmp_path)
    settings.executor = ProviderConfig(provider="mock", model="v1")
    settings.verification.required_files = ["artifact.txt"]
    (tmp_path / "artifact.txt").write_text("fixture")
    harness = Harness(settings)
    controller = GoalController(settings.runs.path)
    goal = GoalState(session_id="s", objective="Check the artifact", tokens_used=10)
    async def run():
        initial = Task(prompt=goal.objective, context={"conversation_history": [{"role": "user", "content": "Context fixture"}]})
        events = [event async for event in controller.run_events(goal, harness, initial_task=initial)]
        trace = await harness.store.get(goal.runs[0])
        return events, trace
    events, trace = asyncio.run(run())
    assert events[-1].payload["status"] == "complete"
    assert controller.get(goal.id).status == "complete"
    assert controller.get(goal.id).context["conversation_history"][0]["content"] == "Context fixture"
    assert "Context fixture" in str(trace.task.context)
    assert trace.config["budgets"]["max_total_tokens"] == settings.budgets.max_total_tokens - 10
    assert harness.config.budgets.max_total_tokens == settings.budgets.max_total_tokens


def test_mcp_untrusted_server_does_not_start(tmp_path):
    from dennice.core.config import MCPServerConfig
    manager = MCPManager()
    server = MCPServerConfig(name="unsafe", command=["must-not-run"], enabled=True)
    async def connect():
        async with manager.connect([server]):
            pytest.fail("Untrusted connection was allowed")
    with pytest.raises(PermissionError):
        asyncio.run(connect())
    with pytest.raises(ValueError):
        no_external_refs({"properties": {"secret": {"$ref": "https://example.com"}}})


def test_stream_limits_unterminated_line_before_buffer_growth():
    async def chunks():
        for _ in range(3):
            yield b"x" * 500000
    async def collect():
        return [line async for line in bounded_stream_lines(SimpleNamespace(aiter_bytes=chunks))]
    with pytest.raises(RuntimeError, match="event exceeded"):
        asyncio.run(collect())


def test_anthropic_truncated_stream_never_completes(monkeypatch):
    import httpx
    original_client = httpx.AsyncClient
    frames = [
        {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 1}}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
        {"type": "message_stop"},
    ]
    body = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    monkeypatch.setattr("dennice.executors.api.connection", lambda config: ("https://fixture.invalid", {}))
    monkeypatch.setattr("dennice.executors.api.httpx.AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    async def collect():
        return [event async for event in stream_request(ProviderConfig(provider="anthropic-api", model="fixture"), "/messages", {})]
    with pytest.raises(RuntimeError, match="incompletely"):
        asyncio.run(collect())


@pytest.mark.parametrize("provider,payload", [
    ("openai-api", {"status": "incomplete", "output": [{"content": [{"type": "output_text", "text": "partial"}]}]}),
    ("anthropic-api", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]}),
    ("local", {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}),
])
def test_json_fallback_rejects_partial_response_with_text(monkeypatch, provider, payload):
    import httpx
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr("dennice.executors.api.connection", lambda config: ("https://fixture.invalid", {}))
    monkeypatch.setattr("dennice.executors.api.httpx.AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))

    async def collect():
        return [event async for event in stream_request(ProviderConfig(provider=provider, model="fixture"), "/response", {})]

    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(collect())


def test_openai_completed_event_rejects_incomplete_response(monkeypatch):
    import httpx
    original_client = httpx.AsyncClient
    payload = {"type": "response.completed", "response": {"status": "incomplete", "output": []}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200,
        text="data: " + json.dumps(payload) + "\n\n", headers={"content-type": "text/event-stream"}))
    monkeypatch.setattr("dennice.executors.api.connection", lambda config: ("https://fixture.invalid", {}))
    monkeypatch.setattr("dennice.executors.api.httpx.AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))

    async def collect():
        return [event async for event in stream_request(ProviderConfig(provider="openai-api", model="fixture"), "/responses", {})]

    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(collect())
