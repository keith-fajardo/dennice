"""Safe local adapter for an authenticated Claude Code installation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from dennice.core.config import PermissionMode, ReasoningEffort
from dennice.core.models import EventKind, ExecutionRequest, RunEvent
from dennice.core.process import command_for_platform


class ClaudeExecutor:
    """Execute through Claude Code's existing subscription or CLI login.

    Dennice delegates authentication entirely to ``claude``. Its print-mode
    invocation is read-oriented (plan permission mode) and streams only model
    messages into Dennice's provider-neutral event system.
    """

    id = "claude"
    version = "cli-v1"

    def __init__(
        self,
        model: str = "default",
        reasoning_effort: ReasoningEffort | None = None,
        permission_mode: PermissionMode | None = None,
        command: str = "claude",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.permission_mode = permission_mode or PermissionMode.PLAN
        self.command = command

    def command_for(self, request: ExecutionRequest) -> list[str]:
        command = [
            self.command,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self._permission_mode(),
            "--append-system-prompt",
            self._system_instructions(request),
        ]
        if self.model not in {"", "default"}:
            command.extend(["--model", self.model])
        command.append(request.task.prompt)
        return command_for_platform(command)

    def _permission_mode(self) -> str:
        """Claude's supported non-interactive safe mode is plan.

        The configuration remains visible even if an imported Codex setting was
        read-only/workspace-write; Claude is deliberately kept in plan mode
        until a provider-native writable flow is introduced.
        """
        return "plan"

    def _system_instructions(self, request: ExecutionRequest) -> str:
        if self.reasoning_effort is None:
            return request.system_instructions
        return (
            f"{request.system_instructions}\n\n"
            "DENNICE EXECUTION EFFORT\n"
            f"Use {self.reasoning_effort.value} effort: scale evidence gathering, validation, "
            "and decomposition to the task's risk and complexity."
        )

    async def execute(self, run_id: str, request: ExecutionRequest) -> AsyncIterator[RunEvent]:
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Claude Code CLI was not found. Install it, run `claude` and sign in, then select Claude in Setup."
            ) from exc
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Could not capture Claude Code output.")

        stderr_task = asyncio.create_task(process.stderr.read())
        emitted_text = False
        while line := await process.stdout.readline():
            text = self._event_text(line)
            if text:
                emitted_text = True
                yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})

        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace").strip()
        if return_code != 0:
            raise RuntimeError(f"Claude Code exited with status {return_code}. {stderr}".strip())
        if not emitted_text:
            raise RuntimeError("Claude Code completed without an agent response.")

    @staticmethod
    def _event_text(line: bytes) -> str | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("type") != "assistant":
            return None
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            return None
        text = "".join(
            part["text"]
            for part in message["content"]
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
        return text or None
