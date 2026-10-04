"""GitHub Copilot CLI adapter using the user's existing subscription login."""

from __future__ import annotations

import asyncio

from dennice.core.config import PermissionMode, ReasoningEffort
from dennice.core.attachments import task_images
from dennice.core.models import EventKind, ExecutionRequest, RunEvent
from dennice.core.process import command_for_platform, process_group_options, read_bounded, stop_process
from dennice.core.skills import skill_user_prompt


class CopilotExecutor:
    """Run a single prompt through the locally authenticated GitHub Copilot CLI.

    Dennice never reads Copilot credentials. The CLI owns authentication and
    its subscription/account policy. The adapter only grants explicitly
    selected read and file-write tool classes; shell and MCP remain disabled.
    """

    id = "copilot"
    version = "cli-v1"

    def __init__(
        self,
        model: str = "default",
        reasoning_effort: ReasoningEffort | None = None,
        permission_mode: PermissionMode | None = None,
        command: str = "copilot",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.permission_mode = permission_mode or PermissionMode.READ_ONLY
        self.command = command

    def command_for(self, request: ExecutionRequest) -> list[str]:
        command = [self.command, "-p", "", "--silent", "--no-color", "--no-ask-user"]
        if self.model not in {"", "default"}:
            command.append(f"--model={self.model}")
        if self.reasoning_effort is not None:
            command.append(f"--effort={self.reasoning_effort.value}")

        # `--available-tools` is an allow-list. Keep shell/MCP out of the
        # subscription process so Dennice's explicit permissions stay narrow.
        if self.permission_mode == PermissionMode.PLAN:
            command.extend(["--plan", "--available-tools=view,grep,glob", "--allow-tool=read"])
        elif self.permission_mode == PermissionMode.WORKSPACE_WRITE:
            command.extend([
                "--available-tools=view,grep,glob,create,edit,apply_patch",
                "--allow-tool=read,write",
            ])
        else:
            command.extend(["--available-tools=view,grep,glob", "--allow-tool=read"])

        prompt = (
            "DENNICE SYSTEM INSTRUCTIONS\n"
            f"{request.system_instructions}\n\n"
            "DENNICE USER TASK\n"
            f"{skill_user_prompt(request.task)}"
        )
        command[2] = prompt
        for path in task_images(request.task):
            command.extend(["--attachment", path])
        return command_for_platform(command)

    def configure_runtime(self, *, root=".", **_runtime) -> None:
        """Set the per-session project root used as the CLI working directory."""
        self.root = root

    async def execute(self, run_id: str, request: ExecutionRequest):
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(request),
                cwd=getattr(self, "root", None),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_group_options(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "GitHub Copilot CLI was not found. Install `@github/copilot`, run `copilot login`, "
                "then select Copilot in Dennice Setup."
            ) from exc

        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Could not capture GitHub Copilot CLI output.")

        stderr_task = asyncio.create_task(read_bounded(process.stderr))
        yield RunEvent(
            run_id=run_id,
            kind=EventKind.MODEL_CALL_STARTED,
            payload={"attempt_id": f"copilot:{run_id}", "phase": "executor", "provider": self.id,
                     "model": self.model, "scope": "Copilot CLI run; internal model request count unavailable"},
        )
        output_bytes = 0
        try:
            while chunk := await process.stdout.read(8192):
                output_bytes += len(chunk)
                if output_bytes > 2_000_000:
                    raise RuntimeError("GitHub Copilot CLI response exceeded the 2 MB output limit.")
                text = chunk.decode("utf-8", errors="replace")
                if text:
                    yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})
            return_code = await process.wait()
            stderr = (await stderr_task).decode(errors="replace").strip()
        finally:
            await stop_process(process)
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        if return_code != 0:
            detail = f" {stderr}" if stderr else ""
            raise RuntimeError(f"GitHub Copilot CLI exited with status {return_code}.{detail}")
        if output_bytes == 0:
            raise RuntimeError("GitHub Copilot CLI completed without an agent response.")
