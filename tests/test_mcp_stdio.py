"""Real stdio MCP round-trip against an isolated fixture process."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from dennice.core.config import MCPServerConfig
from dennice.core.mcp import MCPManager


FIXTURE = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"


def fixture_config(*, mode="all", tools=(), resources=(), prompts=(), timeout=5):
    return MCPServerConfig(
        name="fixture", command=[sys.executable, "-I", str(FIXTURE), mode], enabled=True,
        approved_tools=list(tools), approved_resources=list(resources),
        approved_prompts=list(prompts), timeout_seconds=timeout,
    )


def test_real_stdio_mcp_discovery_and_allowlisted_calls(tmp_path):
    config = fixture_config(tools=["echo"], resources=["file:///approved"], prompts=["summary"])
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            async with manager.connect([config], root=tmp_path) as connected:
                assert connected.status[0]["approved_tools"] == 1
                assert connected.status[0]["approved_resources"] == 1
                assert connected.status[0]["approved_prompts"] == 1
                definitions = connected.definitions()
                assert len(definitions) == 3
                assert "secret" not in json.dumps(definitions)
                tool = next(item for item in definitions if item["name"] in connected.tools)
                resource = next(item for item in definitions if item["name"] in connected.resources)
                prompt = next(item for item in definitions if item["name"] in connected.prompts)
                assert "echo:hello" in await connected.call(tool["name"], {"value": "hello"})
                assert "resource fixture content" in await connected.call(resource["name"], {})
                assert "Summarize dbt" in await connected.call(prompt["name"], {"subject": "dbt"})
                with pytest.raises(PermissionError):
                    await connected.call("mcp_fixture_unapproved", {})
                with pytest.raises(Exception):
                    await connected.call(tool["name"], {"unknown": "value"})

    asyncio.run(run())


def test_real_stdio_mcp_oversized_result_and_disconnect_fail_closed(tmp_path):
    config = fixture_config(tools=["oversized", "failure", "disconnect"])
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            async with manager.connect([config], root=tmp_path) as connected:
                names = {original: name for name, (_, original, _) in connected.tools.items()}
                with pytest.raises(RuntimeError, match="size limit"):
                    await connected.call(names["oversized"], {})
                with pytest.raises(RuntimeError, match="tool failure"):
                    await connected.call(names["failure"], {})
                with pytest.raises(Exception):
                    await connected.call(names["disconnect"], {})

    asyncio.run(run())


@pytest.mark.parametrize("mode,tools,resources,prompts,expected", [
    ("tools", ["echo"], [], [], (1, 0, 0)),
    ("resources", [], ["file:///approved"], [], (0, 1, 0)),
    ("prompts", [], [], ["summary"], (0, 0, 1)),
])
def test_real_stdio_mcp_single_capability_servers(tmp_path, mode, tools, resources, prompts, expected):
    config = fixture_config(mode=mode, tools=tools, resources=resources, prompts=prompts)
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            async with manager.connect([config], root=tmp_path) as connected:
                status = connected.status[0]
                assert (status["approved_tools"], status["approved_resources"], status["approved_prompts"]) == expected
                assert len(connected.definitions()) == 1

    asyncio.run(run())


def test_real_stdio_mcp_timeout_does_not_retry(tmp_path, monkeypatch):
    config = fixture_config(mode="tools", tools=["delayed"])
    marker = tmp_path / "calls.txt"
    monkeypatch.setenv("DENNICE_FIXTURE_LOG", str(marker))
    config.env = {"MCP_FIXTURE_LOG": "DENNICE_FIXTURE_LOG"}
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            with pytest.raises(Exception, match="(?i)timed out|timeout"):
                async with manager.connect([config], root=tmp_path) as connected:
                    name = next(iter(connected.tools))
                    session, original, _ = connected.tools[name]
                    connected.tools[name] = (session, original, 1)
                    await connected.call(name, {})

    asyncio.run(run())
    assert marker.read_text() == "called\n"
