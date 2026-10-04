"""Purpose-built, non-networked MCP qualification fixture."""

import asyncio
import os
import sys

from mcp.server.fastmcp import FastMCP


server = FastMCP("dennice-stdio-fixture")
mode = sys.argv[1] if len(sys.argv) > 1 else "all"


def echo(value: str) -> str:
    return f"echo:{value}"


def oversized() -> str:
    return "x" * 120_000


def disconnect() -> str:
    os._exit(0)


def failure() -> str:
    raise ValueError("fixture tool failure")


async def delayed() -> str:
    if path := os.environ.get("MCP_FIXTURE_LOG"):
        with open(path, "a", encoding="utf-8") as log:
            log.write("called\n")
    await asyncio.sleep(3)
    return "late"


def approved() -> str:
    return "resource fixture content"


def secret() -> str:
    return "must never be discovered"


def summary(subject: str) -> str:
    return f"Summarize {subject}"


if mode in {"all", "tools"}:
    for tool in (echo, oversized, disconnect, failure, delayed):
        server.tool()(tool)

if mode in {"all", "resources"}:
    server.resource("file:///approved")(approved)
    server.resource("file:///secret")(secret)

if mode in {"all", "prompts"}:
    server.prompt()(summary)


if __name__ == "__main__":
    server.run(transport="stdio")
