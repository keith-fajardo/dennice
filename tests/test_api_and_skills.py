import asyncio

import pytest

from dennice.core.config import DenniceConfig, ProviderConfig, ReasoningEffort
from dennice.core.model_catalog import claude_model_options
from dennice.core.models import ExecutionRequest, Task
from dennice.core.skills import discover_skills, skill_user_prompt
from dennice.executors.api import APIExecutor, connection, load_api_models
from dennice.executors.factory import executor_from_config
from dennice.executors.codex import CodexExecutor
from dennice.executors.claude import ClaudeExecutor
from dennice.core.attachments import paste_image, image_data
from PIL import Image


def test_claude_catalog_labels_resolved_versions():
    options = claude_model_options([
        {"value": "opus", "displayName": "Opus", "resolvedModel": "claude-opus-5-5",
         "description": "Opus 5.5 · complex tasks"},
        {"value": "opus", "displayName": "Duplicate"},
    ])
    assert options == (("Opus 5.5 [claude-opus-5-5]", "opus"), ("Custom model…", "custom"))
    with pytest.raises(RuntimeError):
        claude_model_options([])


def test_claude_catalog_never_replaces_names_with_descriptions():
    options = claude_model_options([
        {"value": "sonnet", "displayName": "Sonnet 5.5",
         "description": "For complex work and everyday tasks",
         "resolvedModel": "claude-sonnet-5-5"},
        {"value": "haiku", "displayName": "Haiku", "description": "Fastest for quick answers"},
    ])
    assert options[0] == ("Sonnet 5.5 [claude-sonnet-5-5]", "sonnet")
    assert options[1] == ("Haiku [haiku]", "haiku")


def test_api_credentials_only_go_to_official_host(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "private-key")
    config = ProviderConfig(provider="openai-api", model="test", api_key_env="TEST_API_KEY")
    base, headers = connection(config)
    assert base == "https://api.openai.com/v1"
    assert headers["Authorization"] == "Bearer private-key"
    for url in ["https://example.com/v1", "http://api.openai.com/v1", "https://api.openai.com:8080/v1"]:
        with pytest.raises(RuntimeError):
            connection(config.model_copy(update={"base_url": url}))


def test_local_connection_has_no_default_cloud_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    _, headers = connection(ProviderConfig(provider="local", model="local-model"))
    assert "Authorization" not in headers
    with pytest.raises(RuntimeError):
        connection(ProviderConfig(provider="local", model="test", base_url="http://example.com/v1"))


@pytest.mark.parametrize("provider,path", [
    ("openai-api", "/responses"), ("anthropic-api", "/messages"),
    ("local", "/chat/completions"),
])
def test_api_payloads_include_cognitive_instructions(provider, path):
    config = ProviderConfig(provider=provider, model="test-model", reasoning_effort=ReasoningEffort.HIGH)
    executor = executor_from_config(config)
    request = ExecutionRequest(task=Task(prompt="hello"), system_instructions="selected cognitive policies")
    actual_path, body = executor.payload(request)
    assert actual_path == path
    assert "selected cognitive policies" in str(body)
    assert body["model"] == "test-model"
    if provider == "openai-api":
        assert body["store"] is False
        assert body["reasoning"] == {"effort": "high"}


def test_api_response_extraction(monkeypatch):
    monkeypatch.setattr("dennice.executors.api.request_json", lambda *args: {
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": "Hello"}]}]
    })
    async def execute():
        executor = APIExecutor(ProviderConfig(provider="openai-api", model="test"))
        request = ExecutionRequest(task=Task(prompt="hi"), system_instructions="policy")
        return [event async for event in executor.execute("test", request)]
    assert asyncio.run(execute())[0].payload["text"] == "Hello"


def test_missing_usage_stops_api_tool_loop_without_faking_zero(monkeypatch):
    class Broker:
        calls = 0

        def definitions(self):
            return [{"name": "shell", "description": "test", "parameters": {}}]

        async def invoke(self, *args):
            self.calls += 1
            return "executed"

    async def stream(*args):
        yield "result", {
            "usage": {},
            "choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"id": "call-1", "function": {"name": "shell", "arguments": "{}"}}],
            }}],
        }

    monkeypatch.setattr("dennice.executors.api.stream_request", stream)
    broker = Broker()
    executor = APIExecutor(ProviderConfig(provider="local", model="test"),
                           broker=broker, budgets=DenniceConfig().budgets)
    request = ExecutionRequest(task=Task(prompt="run a command"), system_instructions="policy")
    events = []

    async def execute():
        with pytest.raises(RuntimeError, match="token budget cannot be enforced"):
            async for event in executor.execute("run", request):
                events.append(event)

    asyncio.run(execute())
    usage = next(event for event in events if event.kind.value == "usage")
    assert usage.payload["reported"] is False
    assert usage.payload["input_tokens"] is None
    assert usage.payload["output_tokens"] is None
    assert broker.calls == 0


def test_anthropic_catalog_paginates(monkeypatch):
    calls = []
    def request(config, path):
        calls.append(path)
        return {"data": [{"id": "first", "display_name": "First"}], "has_more": True, "last_id": "first"} if len(calls) == 1 else {"data": [{"id": "second"}], "has_more": False}
    monkeypatch.setattr("dennice.executors.api.request_json", request)
    options = asyncio.run(load_api_models(ProviderConfig(provider="anthropic-api", model="first")))
    assert ("second", "second") in options
    assert "after_id=first" in calls[1]


def test_api_config_saves_key_reference_not_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "private-key")
    config = DenniceConfig(executor=ProviderConfig(provider="openai-api", model="test", api_key_env="TEST_API_KEY"))
    path = config.save(tmp_path / "dennice.yaml")
    assert "private-key" not in path.read_text()
    assert DenniceConfig.load(path).executor.api_key_env == "TEST_API_KEY"


def test_skill_discovery_and_selection_are_read_only(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    for provider, relative in [("claude", ".claude/skills"), ("codex", ".agents/skills")]:
        path = project / relative / "review" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: review\ndescription: Review code\n---\nKeep constraints.\n")
    hidden = home / ".claude/skills/hidden/SKILL.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("---\nname: hidden\nuser-invocable: false\n---\nInternal only.\n")
    skills = discover_skills(project, home)
    assert {skill.provider for skill in skills} == {"claude", "codex"}
    assert len(skills) == 2
    skill = skills[0]
    task = Task(prompt="Review my code", context={"selected_skill": {"name": skill.name, "path": str(skill.path)}})
    assert str(skill.path) in skill_user_prompt(task)
    assert task.prompt == "Review my code"
    assert skill.path.read_text().endswith("Keep constraints.\n")


def test_api_does_not_claim_filesystem_skill_support():
    executor = APIExecutor(ProviderConfig(provider="local", model="test"))
    request = ExecutionRequest(task=Task(prompt="test", context={"selected_skill": {"path": "/test/SKILL.md"}}), system_instructions="test")
    with pytest.raises(RuntimeError, match="no local tools"):
        executor.payload(request)


def test_clipboard_image_is_saved_only_on_explicit_paste(tmp_path, monkeypatch):
    monkeypatch.setattr("dennice.core.attachments.ImageGrab.grabclipboard", lambda: Image.new("RGB", (8, 8), "red"))
    directory = tmp_path / "attachments"
    assert not directory.exists()
    path = paste_image(directory)
    assert path.is_file()
    assert image_data(str(path))[0] == "image/png"


@pytest.mark.parametrize("provider,block", [("openai-api", "input_image"), ("anthropic-api", "image"), ("local", "image_url")])
def test_image_payloads_use_native_provider_blocks(tmp_path, provider, block):
    path = tmp_path / "test.png"
    Image.new("RGB", (8, 8), "red").save(path)
    request = ExecutionRequest(task=Task(prompt="Describe", context={"images": [str(path)]}), system_instructions="policy")
    _, payload = APIExecutor(ProviderConfig(provider=provider, model="vision-test")).payload(request)
    content = payload["input"][0]["content"] if provider == "openai-api" else payload["messages"][-1]["content"]
    assert content[1]["type"] == block


def test_cli_images_are_passed_to_executor(tmp_path):
    path = tmp_path / "test.png"
    Image.new("RGB", (8, 8), "red").save(path)
    request = ExecutionRequest(task=Task(prompt="Describe", context={"images": [str(path)]}), system_instructions="policy")
    command = CodexExecutor().command_for(request)
    assert command[command.index("--image") + 1] == str(path)
    assert str(path) in ClaudeExecutor().command_for(request)[-1]
