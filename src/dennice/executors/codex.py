"""Safe, local adapter for an authenticated Codex CLI installation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from dennice.core.models import EventKind, ExecutionRequest, RunEvent


class CodexExecutor:
    """Execute one Dennice request through ``codex exec`` with read-only access.

    The adapter deliberately delegates authentication to the locally installed Codex
    CLI. It neither reads nor stores a ChatGPT session, API key, or access token.
    """

    id = "codex"
    version = "cli-v1"

    def __init__(self, model: str = "default", command: str = "codex") -> None:
        self.model = model
        self.command = command

    def command_for(self, request: ExecutionRequest) -> list[str]:
        """Build a fixed, non-destructive Codex invocation for a request."""
        command = [
            self.command,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
        ]
        if self.model not in {"", "default"}:
            command.extend(["--model", self.model])
        command.append(
            f"{request.system_instructions}\n\nUSER TASK\n{request.task.prompt}"
        )
        return command

    async def execute(self, run_id: str, request: ExecutionRequest) -> AsyncIterator[RunEvent]:
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Codex CLI was not found. Install it, run `codex login`, then select Codex in Setup."
            ) from exc

        if process.stdout is None or process.stderr is None:  # Defensive for type checkers.
            raise RuntimeError("Could not capture Codex CLI output.")

        stderr_task = asyncio.create_task(process.stderr.read())
        emitted_text = False
        errors: list[str] = []
        while line := await process.stdout.readline():
            payload = self._decode_event(line)
            if payload is None:
                continue
            if payload.get("type") == "error":
                errors.append(str(payload.get("message") or payload.get("error") or payload))
                continue
            text = self._event_text(payload)
            if text:
                emitted_text = True
                yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})

        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace").strip()
        if return_code != 0:
            detail = "\n".join(errors + ([stderr] if stderr else []))
            raise RuntimeError(f"Codex CLI exited with status {return_code}. {detail}".strip())
        if errors:
            raise RuntimeError("Codex CLI reported an error: " + "\n".join(errors))
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
