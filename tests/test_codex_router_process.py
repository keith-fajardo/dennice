"""Codex classification subprocess must not inherit the project directory."""

import asyncio
import sys
from pathlib import Path

import pytest

from dennice.routing.codex import CodexRouter


class AuthClient:
    response = {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        return self.response


@pytest.fixture(autouse=True)
def auth_client(monkeypatch):
    AuthClient.response = {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}
    AuthClient.requests = []
    monkeypatch.setattr("dennice.routing.codex.AppServerClient", AuthClient)
    return AuthClient


def test_codex_router_runs_in_disposable_directory(monkeypatch):
    original = asyncio.create_subprocess_exec
    roots = []

    async def launch(*args, **kwargs):
        command = " ".join(args)
        assert "exec --json --sandbox" in command
        assert "--ephemeral" in command
        root = kwargs["cwd"]
        roots.append(root)
        assert Path(root).is_dir()
        program = "print('{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"typed decision\"}}', flush=True)"
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    result = asyncio.run(CodexRouter()._run("classify", "/tmp/schema.json"))
    assert result == "typed decision"
    assert roots and not Path(roots[0]).exists()
    assert AuthClient.requests == [("account/read", {"refreshToken": False})]


def test_codex_router_auth_mismatch_stops_before_inference(monkeypatch, auth_client):
    auth_client.response = {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}

    async def must_not_launch(*args, **kwargs):
        raise AssertionError("classification started before auth check")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", must_not_launch)
    with pytest.raises(RuntimeError, match="Codex authentication is apiKey"):
        asyncio.run(CodexRouter()._run("classify", "/tmp/schema.json"))
    assert auth_client.requests == [("account/read", {"refreshToken": False})]


def test_codex_router_timeout_stops_owned_process(monkeypatch):
    original = asyncio.create_subprocess_exec
    processes = []

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        process = await original(sys.executable, "-u", "-c",
                                 "import time; time.sleep(10)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(TimeoutError):
        asyncio.run(CodexRouter(timeout_seconds=0.05)._run("classify", "/tmp/schema.json"))
    assert processes and processes[0].returncode is not None
