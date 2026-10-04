"""Safe, local adapter for an authenticated Codex CLI installation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from dennice.core.config import PermissionMode, ReasoningEffort
from dennice.core.models import EventKind, ExecutionRequest, RunEvent
from dennice.core.process import command_for_platform, process_group_options, read_bounded, stop_process
from dennice.core.skills import skill_user_prompt
from dennice.core.attachments import task_images


class CodexExecutor:
    """Execute one Dennice request through ``codex exec`` with read-only access.

    The adapter deliberately delegates authentication to the locally installed Codex
    CLI. It neither reads nor stores a ChatGPT session, API key, or access token.
    """

    id = "codex"
    version = "cli-v1"

    def __init__(
        self,
        model: str = "default",
        reasoning_effort: ReasoningEffort | None = None,
        permission_mode: PermissionMode | None = None,
        command: str = "codex",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.permission_mode = permission_mode or PermissionMode.READ_ONLY
        self.command = command

    def command_for(self, request: ExecutionRequest) -> list[str]:
        """Build a fixed, non-destructive Codex invocation for a request."""
        command = [
            self.command,
            "exec",
            "--json",
            "--sandbox",
            self._sandbox_mode(),
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
        ]
        if self.model not in {"", "default"}:
            command.extend(["--model", self.model])
        if self.reasoning_effort is not None:
            command.extend(
                ["--config", f'model_reasoning_effort="{self.reasoning_effort.value}"']
            )
        for path in task_images(request.task):
            command.extend(["--image", path])
        instructions = request.system_instructions
        if self.permission_mode == PermissionMode.PLAN:
            instructions += "\n\nPLANNING MODE: Investigate and produce a plan only. Do not implement changes."
        command.append(f"{instructions}\n\nUSER TASK\n{skill_user_prompt(request.task)}")
        return command_for_platform(command)

    def _sandbox_mode(self) -> str:
        if self.permission_mode == PermissionMode.WORKSPACE_WRITE:
            return "workspace-write"
        return "read-only"

    async def execute(self, run_id: str, request: ExecutionRequest) -> AsyncIterator[RunEvent]:
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_group_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Codex CLI was not found. Install it, run `codex login`, then select Codex in Setup."
            ) from exc

        if process.stdout is None or process.stderr is None:  # Defensive for type checkers.
            raise RuntimeError("Could not capture Codex CLI output.")

        stderr_task = asyncio.create_task(read_bounded(process.stderr))
        emitted_text = False
        errors: list[str] = []
        try:
            while line := await process.stdout.readline():
                payload = self._decode_event(line)
                if payload is None:
                    continue
                if payload.get("type") == "error":
                    errors.append("Native provider reported an error event")
                    continue
                text = self._event_text(payload)
                if text:
                    emitted_text = True
                    yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})
            return_code = await process.wait()
            await stderr_task
        finally:
            await stop_process(process)
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        if return_code != 0:
            raise RuntimeError(
                f"Codex CLI exited with status {return_code}. "
                "Inspect the provider CLI locally; raw diagnostics are withheld from the run trace."
            )
        if errors:
            raise RuntimeError("Codex CLI reported an error event; no automatic replay.")
        if not emitted_text:
            raise RuntimeError("Codex CLI completed without an agent response.")

    @staticmethod
    def _decode_event(line: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _event_text(payload: dict[str, Any]) -> str | None:
        """Extract only user-visible agent text from documented JSONL event shapes."""
        event_type = payload.get("type")
        item = payload.get("item")
        if event_type == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                return item["text"]
        if event_type in {"item.delta", "item.updated"}:
            delta = payload.get("delta")
            if isinstance(delta, str):
                return delta
        return None
