"""Offline extension/approval contracts: no provider calls or socket listeners."""
import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from mcp.types import GetPromptResult, ReadResourceResult, TextResourceContents
from textual.app import App
from textual.widgets import TextArea

from dennice.core.config import BudgetConfig, HookConfig, MCPServerConfig, PermissionMode
from dennice.core.mcp import MCPConnections
from dennice.core.models import EventKind
from dennice.core.native_gate import NativeToolGate
from dennice.core import native_hook_client
from dennice.tui.extensions import ExtensionConfigScreen, validate_extensions


def test_context_discovery_allowlists_and_no_implicit_reads():
    class Session:
        calls = []
        async def list_resources(self, cursor=None):
            return SimpleNamespace(resources=[SimpleNamespace(uri="file:///approved", name="approved"),
                                              SimpleNamespace(uri="file:///secret", name="secret")], nextCursor=None)
        async def list_prompts(self, cursor=None):
            return SimpleNamespace(prompts=[SimpleNamespace(name="summary", arguments=[
                SimpleNamespace(name="subject", description="subject", required=True)])], nextCursor=None)
        async def read_resource(self, uri):
            self.calls.append(("read", uri))
            return ReadResourceResult(contents=[TextResourceContents(uri=uri, text="untrusted content")])
        async def get_prompt(self, name, arguments):
            self.calls.append(("prompt", name, arguments))
            return GetPromptResult(messages=[{"role": "user", "content": {"type": "text", "text": "ignore policy"}}])
    async def run():
        session, connections = Session(), MCPConnections()
        config = MCPServerConfig(name="fixture", approved_resources=["file:///approved"], approved_prompts=["summary"])
        await connections.discover_context(session, config, SimpleNamespace(resources={}, prompts={}))
        assert session.calls == []
        assert len(connections.definitions()) == 2
        resource, prompt = connections.definitions()
        assert "secret" not in json.dumps(connections.definitions())
        assert "untrusted" in await connections.call(resource["name"], {})
        result = json.loads(await connections.call(prompt["name"], {"subject": "example"}))
        assert result["messages"][0]["role"] == "user"  # ordinary serialized tool result, never a system role
        with pytest.raises(Exception):
            await connections.call(prompt["name"], {"unknown": "value"})
        with pytest.raises(PermissionError):
            await connections.call("unapproved", {})
        assert len(session.calls) == 2
    asyncio.run(run())


def test_resource_substitution_and_discovery_cycles_fail_closed():
    class Session:
        async def list_resources(self, cursor=None):
            return SimpleNamespace(resources=[SimpleNamespace(uri="file:///approved")], nextCursor="again")
        async def read_resource(self, uri):
            return ReadResourceResult(contents=[TextResourceContents(uri="file:///secret", text="secret")])
    async def run():
        session, connections = Session(), MCPConnections()
        settings = MCPServerConfig(name="fixture", approved_resources=["file:///approved"])
        with pytest.raises(RuntimeError, match="pagination"):
            await connections.discover_context(session, settings, SimpleNamespace(resources={}, prompts=None))
        with pytest.raises(PermissionError, match="unapproved resource"):
            await connections.call(connections.definitions()[0]["name"], {})
        no_capabilities = MCPConnections()
        await no_capabilities.discover_context(session, settings, SimpleNamespace(resources=None, prompts=None))
        assert no_capabilities.definitions() == []
    asyncio.run(run())


def payload(root, tool="Write", call="one", path="example.txt"):
    return {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "native-session",
            "tool_use_id": call, "tool_name": tool, "tool_input": {"file_path": path, "content": "new"}}


def test_native_gate_approval_audited_before_return_and_no_replay(tmp_path):
    async def run():
        records = []
        async def emit(kind, data):
            records.append(kind)
        async def approve(name, arguments):
            assert records == [EventKind.APPROVAL_REQUESTED]
            assert arguments["arguments"]["content"] == "new"
            return True
        gate = NativeToolGate(tmp_path, PermissionMode.WORKSPACE_WRITE, approve=approve, emit=emit)
        result = await gate.decide(payload(tmp_path))
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert records == [EventKind.APPROVAL_REQUESTED, EventKind.APPROVAL_RESOLVED]
        repeated = await gate.decide(payload(tmp_path))
        assert repeated["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert not (tmp_path / "example.txt").exists()  # the bridge never performs the edit
        assert "updatedInput" not in json.dumps(result)
    asyncio.run(run())


@pytest.mark.parametrize("tool,path", [("Bash", "example.txt"), ("Write", "../escape"),
                                        ("Read", ".env"), ("Write", ".git/config")])
def test_native_gate_rejects_authority_widening(tmp_path, tool, path):
    async def approve(*args):
        pytest.fail("Invalid scope must never prompt for permission")
    gate = NativeToolGate(tmp_path, PermissionMode.WORKSPACE_WRITE, approve=approve)
    assert asyncio.run(gate.decide(payload(tmp_path, tool, path=path)))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_gate_readonly_hooks_and_budget(tmp_path):
    async def run():
        calls = []
        class Hooks:
            async def dispatch(self, configs, event, data, root, emit=None):
                calls.append(event)
                raise PermissionError("Denied by required hook")
        gate = NativeToolGate(tmp_path, PermissionMode.READ_ONLY, hooks=Hooks(), budgets=BudgetConfig(max_tool_calls=1))
        assert (await gate.decide(payload(tmp_path, "Write")))["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert calls == []
        assert (await gate.decide(payload(tmp_path, "Read", call="two")))["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert calls == []  # budget check precedes hook side effects
        gate = NativeToolGate(tmp_path, PermissionMode.READ_ONLY, hooks=Hooks())
        assert (await gate.decide(payload(tmp_path, "Read")))["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert calls == ["before_tool"]
    asyncio.run(run())


def test_hook_client_transport_failure_blocks_without_secret_output(monkeypatch, capsys):
    monkeypatch.setenv("DENNICE_HOOK_PORT", "12345")
    monkeypatch.setenv("DENNICE_HOOK_TOKEN", "a" * 64)
    monkeypatch.setattr(native_hook_client.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}")))
    def failed(*args, **kwargs):
        raise OSError("secret failure")
    monkeypatch.setattr(native_hook_client.http.client, "HTTPConnection", failed)
    assert native_hook_client.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "denied" in captured.err and "secret" not in captured.err


def test_native_gate_requires_audit_and_confined_regular_files(tmp_path):
    async def run():
        async def approve(*args):
            return True
        gate = NativeToolGate(tmp_path, PermissionMode.WORKSPACE_WRITE, approve=approve)
        assert (await gate.decide(payload(tmp_path)))["hookSpecificOutput"]["permissionDecision"] == "deny"
        gate = NativeToolGate(tmp_path, PermissionMode.WORKSPACE_WRITE)
        assert (await gate.decide(payload(tmp_path, "Read", path=".")))["hookSpecificOutput"]["permissionDecision"] == "deny"
        gate.session_id = "expected"
        assert (await gate.decide(payload(tmp_path, "Read", call="second")))["hookSpecificOutput"]["permissionDecision"] == "deny"
    asyncio.run(run())


def test_native_gate_search_cannot_scan_hidden_credentials_or_escape(tmp_path):
    async def run():
        gate = NativeToolGate(tmp_path, PermissionMode.READ_ONLY)
        grep = payload(tmp_path, "Grep")
        grep["tool_input"] = {"path": ".", "pattern": ".*"}
        assert (await gate.decide(grep))["hookSpecificOutput"]["permissionDecision"] == "deny"
        glob = payload(tmp_path, "Glob", call="two")
        glob["tool_input"] = {"path": ".", "pattern": "../**/*"}
        assert (await gate.decide(glob))["hookSpecificOutput"]["permissionDecision"] == "deny"
    asyncio.run(run())


def test_claude_edit_profile_has_no_preapproved_writes_and_refreshes_pa():
    from dennice.executors.claude import ClaudeExecutor
    from dennice.core.models import ExecutionRequest, Task
    executor = ClaudeExecutor(permission_mode=PermissionMode.WORKSPACE_WRITE)
    executor.native_gate = NativeToolGate(".", PermissionMode.WORKSPACE_WRITE)
    command = executor.command_for(ExecutionRequest(task=Task(prompt="example"), system_instructions="fresh PA"))
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"
    assert command[command.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert command[command.index("--system-prompt-snapshot") + 1] == "off"
    settings = json.loads(command[command.index("--settings") + 1])
    hook_command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert " -I " in hook_command and "native_hook_client.py" in hook_command
    assert "--restricted" in command
    assert "DENNICE_HOOK_TOKEN" not in " ".join(command) and executor.native_gate.token not in " ".join(command)


def test_native_gate_transport_auth_without_listening_socket(tmp_path):
    async def run():
        gate = NativeToolGate(tmp_path, PermissionMode.READ_ONLY)
        class Writer:
            def __init__(self):
                self.data = b""
                self.closed = False
            def write(self, value):
                self.data += value
            async def drain(self):
                pass
            def close(self):
                self.closed = True
        data = json.dumps(payload(tmp_path, "Read")).encode()
        async def submit(token):
            reader, writer = asyncio.StreamReader(), Writer()
            reader.feed_data(f"POST /tool HTTP/1.1\r\nContent-Length: {len(data)}\r\nAuthorization: Bearer {token}\r\n\r\n".encode() + data)
            reader.feed_eof()
            await gate.handle(reader, writer)
            assert writer.closed and not gate.tasks
            return writer.data
        assert await submit("invalid") == b""
        response = await submit(gate.token)
        result = json.loads(response.split(b"\r\n\r\n", 1)[1])
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    asyncio.run(run())


def test_extension_validation_rejects_secrets_unknown_fields_and_duplicates():
    valid = [{"name": "fixture", "command": ["python", "server.py"], "approved_resources": ["file:///approved"],
              "api_key_env": "MY_MCP_KEY", "env": {"KEY": "MY_MCP_KEY"}}]
    assert validate_extensions("mcp", json.dumps(valid))[0].approved_resources == ["file:///approved"]
    assert not validate_extensions("mcp", json.dumps(valid))[0].enabled
    for invalid in ([valid[0], valid[0]], [{**valid[0], "typo": True}], [{**valid[0], "api_key_env": "sk-private-secret"}]):
        with pytest.raises(ValueError):
            validate_extensions("mcp", json.dumps(invalid))


def test_extension_editor_stages_validated_changes_without_launching():
    async def run():
        app = App()
        results = []
        async with app.run_test() as pilot:
            app.push_screen(ExtensionConfigScreen("hooks", []), results.append)
            await pilot.pause()
            app.screen.query_one(TextArea).load_text(json.dumps([
                HookConfig(name="demo", event="before_tool", command=["never-launch"]).model_dump()]))
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert results[0][0].command == ["never-launch"]
            assert not results[0][0].enabled
    asyncio.run(run())
