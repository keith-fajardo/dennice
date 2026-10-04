"""Provider-controlled diagnostics must not enter Dennice's saved errors."""

import asyncio
import sys

import pytest

from dennice.core.models import ExecutionRequest, Task
from dennice.executors.codex import CodexExecutor
from dennice.executors.copilot import CopilotExecutor
from dennice.routing.codex import CodexRouter


class AuthPeer:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, *args, **kwargs):
        return {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}


@pytest.mark.parametrize("provider", ["codex_cli", "codex_router", "copilot"])
def test_cli_failure_keeps_raw_diagnostics_out_of_error(tmp_path, monkeypatch, provider):
    original = asyncio.create_subprocess_exec
    if provider == "codex_router":
        monkeypatch.setattr("dennice.routing.codex.AppServerClient", AuthPeer)

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        stdout = ("print('{\"type\":\"error\",\"message\":\"private-fixture-token\"}',flush=True);"
                  if provider != "copilot" else "")
        program = "import sys; " + stdout + "sys.stderr.write('private-fixture-token\\n'); sys.exit(7)"
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def journey():
        if provider == "codex_router":
            with pytest.raises(RuntimeError, match="exited with status 7") as error:
                await CodexRouter()._run("classify", str(tmp_path / "schema.json"))
        else:
            executor = CodexExecutor() if provider == "codex_cli" else CopilotExecutor()
            request = ExecutionRequest(task=Task(prompt="fixture"), system_instructions="fixture")
            with pytest.raises(RuntimeError, match="exited with status 7") as error:
                _ = [event async for event in executor.execute("failed", request)]
        assert "private-fixture-token" not in str(error.value)

    asyncio.run(journey())
