"""Provider-owned model metadata, without a generation request."""

import asyncio
import json
import os
import tempfile

from dennice.core.process import command_for_platform


def claude_model_options(models: list[dict]) -> tuple[tuple[str, str], ...]:
    options = []
    seen = set()
    for model in models:
        value = model.get("value")
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        name = str(model.get("displayName") or value)
        resolved = model.get("resolvedModel")
        # Descriptions often contain only marketing copy. Never use that copy
        # instead of the actual model name. Keep version-bearing descriptions
        # only when they explicitly start with the reported model name.
        description = str(model.get("description") or "").split(" · ")[0]
        label = description if description.casefold().startswith(name.casefold() + " ") else name
        if resolved:
            label += f" [{resolved}]"
        elif value != name:
            label += f" [{value}]"
        options.append((label, value))
    if not options:
        raise RuntimeError("Claude Code returned no selectable models.")
    return (*options, ("Custom model…", "custom"))


async def load_claude_model_catalog() -> tuple[tuple[str, str], ...]:
    """Read the CLI initialization catalog. Never submit a user message.

    Safe mode skips hooks/plugins; no tools or MCP servers are enabled. A
    temporary working directory keeps project instructions out of discovery.
    """
    command = command_for_platform(
        ["claude", "--print", "--input-format", "stream-json",
         "--output-format", "stream-json", "--verbose", "--safe-mode",
         "--strict-mcp-config", "--tools", "", "--no-session-persistence"],
        "win32" if os.name == "nt" else "posix",
    )
    with tempfile.TemporaryDirectory(prefix="dennice-models-") as directory:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=directory, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            request = {"type": "control_request", "request_id": "dennice-models",
                       "request": {"subtype": "initialize"}}
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            async with asyncio.timeout(20):
                while line := await process.stdout.readline():
                    message = json.loads(line)
                    if message.get("type") != "control_response":
                        continue
                    response = message.get("response", {})
                    if response.get("request_id") != "dennice-models":
                        continue
                    if response.get("subtype") == "error":
                        raise RuntimeError("Claude Code could not load its model catalog.")
                    return claude_model_options(response.get("response", {}).get("models", []))
                raise RuntimeError("Claude Code exited before returning its model catalog.")
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
