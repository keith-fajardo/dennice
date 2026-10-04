"""Small cross-platform process helpers used by local provider adapters."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
from collections.abc import Sequence


def command_for_platform(command: Sequence[str], platform: str | None = None) -> list[str]:
    """Run provider commands through Bash on Windows and directly elsewhere."""
    active_platform = platform or sys.platform
    if active_platform.startswith("win"):
        return ["bash", "-lc", shlex.join(command)]
    return list(command)


def process_group_options() -> dict:
    """Give a user/provider subprocess its own lifecycle boundary on POSIX."""
    return {"start_new_session": True} if os.name == "posix" else {}


async def stop_process(process: asyncio.subprocess.Process) -> None:
    """Stop the owned process tree; never target the harness's process group."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        # Windows taskkill scopes termination to this owned PID and its children.
        cleanup = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await cleanup.wait()
    await process.wait()


async def read_bounded(stream: asyncio.StreamReader, limit: int = 24_000) -> bytes:
    """Drain a pipe without retaining unlimited output or blocking its writer."""
    retained = bytearray()
    while chunk := await stream.read(8192):
        if len(retained) < limit:
            retained.extend(chunk[: limit - len(retained)])
    return bytes(retained)
