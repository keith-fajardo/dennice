"""MCP rejection cases through a real stdio transport and SDK session."""

import asyncio
import sys
from pathlib import Path

import pytest

from dennice.core.config import MCPServerConfig
from dennice.core.mcp import MCPManager


FIXTURE = Path(__file__).parent / "fixtures" / "mcp_fault_server.py"


def fixture_config(mode):
    return MCPServerConfig(
        name="fault_fixture", command=[sys.executable, "-I", str(FIXTURE), mode], enabled=True,
        approved_tools=["approved"], approved_resources=["file:///approved"],
        approved_prompts=["approved"], timeout_seconds=5,
    )


@pytest.mark.parametrize("mode,message", [
    ("tool_cycle", "pagination did not advance"),
    ("resource_cycle", "pagination did not advance"),
    ("prompt_cycle", "pagination did not advance"),
    ("external_schema", "External JSON schema references"),
    ("duplicate_prompt_args", "duplicate arguments"),
])
def test_real_stdio_mcp_rejects_bad_discovery(tmp_path, mode, message):
    config = fixture_config(mode)
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            with pytest.raises((RuntimeError, ValueError), match=message):
                async with manager.connect([config], root=tmp_path):
                    pass

    asyncio.run(run())


def test_real_stdio_mcp_rejects_substituted_resource(tmp_path):
    config = fixture_config("substituted_resource")
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            async with manager.connect([config], root=tmp_path) as connected:
                name = next(iter(connected.resources))
                with pytest.raises(PermissionError, match="unapproved resource URI"):
                    await connected.call(name, {})

    asyncio.run(run())


def test_real_stdio_mcp_ignores_unapproved_resource(tmp_path):
    config = fixture_config("unexpected_resource")
    manager = MCPManager()
    manager.trust(config, root=tmp_path)

    async def run():
        async with asyncio.timeout(20):
            async with manager.connect([config], root=tmp_path) as connected:
                assert connected.resources == {}
                assert connected.status[0]["approved_resources"] == 0

    asyncio.run(run())
