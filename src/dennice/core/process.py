"""Small cross-platform process helpers used by local provider adapters."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Sequence


def command_for_platform(command: Sequence[str], platform: str | None = None) -> list[str]:
    """Run provider commands through Bash on Windows and directly elsewhere."""
    active_platform = platform or sys.platform
    if active_platform.startswith("win"):
        return ["bash", "-lc", shlex.join(command)]
    return list(command)
