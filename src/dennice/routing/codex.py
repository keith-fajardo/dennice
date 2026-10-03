"""Codex-backed System 1 router that emits only a structured classification."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from importlib.resources import as_file, files
from typing import Any

from dennice.core.config import ReasoningEffort
from dennice.core.models import RoutingDecision, Task
from dennice.core.process import command_for_platform


class CodexRouter:
    """Use local Codex CLI authentication for a narrowly scoped cognitive route."""

    id = "codex"
    version = "cli-v1"

    def __init__(
        self,
        model: str = "default",
        reasoning_effort: ReasoningEffort | None = None,
        command: str = "codex",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.command = command

    def command_for(self, prompt: str, schema_path: str) -> list[str]:
        """Build a read-only Codex invocation that is incapable of executing a task."""
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
            "--output-schema",
            schema_path,
        ]
        if self.model not in {"", "default"}:
            command.extend(["--model", self.model])
        if self.reasoning_effort is not None:
            command.extend(
                ["--config", f'model_reasoning_effort="{self.reasoning_effort.value}"']
            )
        command.append(prompt)
        return command_for_platform(command)

    async def classify(self, task: Task) -> RoutingDecision:
        schema = files("dennice.routing").joinpath("routing_decision.schema.json")
        with as_file(schema) as schema_path:
            response = await self._run(self._classification_prompt(task), str(schema_path))
        try:
            raw = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex router returned invalid structured output.") from exc
        decision = RoutingDecision.model_validate(raw)
        return decision.model_copy(update={"router_id": self.id, "router_version": self.version})

    async def _run(self, prompt: str, schema_path: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(prompt, schema_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Codex CLI was not found. Install it, run `codex login`, then configure router.provider: codex."
            ) from exc
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Could not capture Codex router output.")

        stderr_task = asyncio.create_task(process.stderr.read())
        final_message: str | None = None
        errors: list[str] = []
        async for payload in self._events(process.stdout):
            if payload.get("type") == "error":
                errors.append(str(payload.get("message") or payload.get("error") or payload))
            item = payload.get("item")
            if payload.get("type") == "item.completed" and isinstance(item, dict):
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    final_message = item["text"]

        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace").strip()
        if return_code != 0:
            detail = "\n".join(errors + ([stderr] if stderr else []))
            raise RuntimeError(f"Codex router exited with status {return_code}. {detail}".strip())
        if errors:
            raise RuntimeError("Codex router reported an error: " + "\n".join(errors))
        if final_message is None:
            raise RuntimeError("Codex router completed without a routing decision.")
        return final_message

    @staticmethod
    async def _events(stream: asyncio.StreamReader) -> AsyncIterator[dict[str, Any]]:
        while line := await stream.readline():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload

    @staticmethod
    def _classification_prompt(task: Task) -> str:
        return f"""You are Dennice System 1, a cognitive router for data analytics tasks.

Classify the cognitive demands required to execute the task. Do not solve the task,
diagnose its cause, recommend actions, write SQL, or claim any evidence. Your only
job is to identify the task family and one primary plus zero or more supporting
cognitive demands. Confidence reflects routing uncertainty, not task correctness.

Canonical demands:
- critical_inquiry: clarify assumptions, form discriminating hypotheses.
- empirical_induction: prioritize observed evidence and cautious inference.
- decomposition: isolate components and reason through subproblems.
- constraint_reasoning: preserve invariants, semantics, and business rules.
- causal_categorization: classify entities, failure classes, and causal structure.
- contradiction_resolution: reconcile competing claims or definitions.
- abstraction: reason about reusable conceptual structures and architecture.

TASK\n{task.prompt}"""
