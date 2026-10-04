"""GitHub Copilot CLI adapter using the user's existing subscription login."""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import shutil
import tempfile
from pathlib import Path

from dennice.core.config import PermissionMode, ReasoningEffort
from dennice.core.attachments import task_images
from dennice.core.models import EventKind, ExecutionRequest, RunEvent
from dennice.core.process import command_for_platform, process_group_options, read_bounded, stop_process
from dennice.core.skills import skill_user_prompt

COPILOT_AUTH_OVERRIDES = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


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

    def configure_runtime(self, *, root=".", budgets=None, **_runtime) -> None:
        """Set the per-session project root used as the CLI working directory."""
        self.root = root
        self.budgets = budgets

    async def execute(self, run_id: str, request: ExecutionRequest):
        auth_override = next((name for name in COPILOT_AUTH_OVERRIDES if os.environ.get(name)), None)
        if auth_override:
            raise RuntimeError(
                f"Copilot CLI authentication is overridden by {auth_override}; unset it and use "
                "`copilot login` so Dennice uses the intended local Copilot account."
            )
        telemetry_dir = Path(tempfile.mkdtemp(prefix="dennice-copilot-otel-"))
        telemetry_path = telemetry_dir / "telemetry.jsonl"
        providers_path = telemetry_dir / "providers.json"
        # This adapter is specifically for the user's GitHub Copilot login.
        # Do not inherit BYOK environment overrides or the user's BYOK registry:
        # those can redirect the CLI to a different provider (including OpenAI).
        providers_path.write_text('{"providers": [], "models": []}\n', encoding="utf-8")
        environment = os.environ.copy()
        for name in list(environment):
            if name.startswith("COPILOT_PROVIDER_"):
                environment.pop(name, None)
        environment["COPILOT_PROVIDERS_CONFIG"] = str(providers_path)
        environment.pop("COPILOT_MODEL", None)
        # GitHub documents the file exporter as a local JSONL source for
        # invoke_agent token totals. Never persist prompt content in this file.
        environment.update({
            "COPILOT_OTEL_EXPORTER_TYPE": "file",
            "COPILOT_OTEL_FILE_EXPORTER_PATH": str(telemetry_path),
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
        })
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command_for(request),
                cwd=getattr(self, "root", None),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_group_options(),
            )
        except FileNotFoundError as exc:
            shutil.rmtree(telemetry_dir, ignore_errors=True)
            raise RuntimeError(
                "GitHub Copilot CLI was not found. Install `@github/copilot`, run `copilot login`, "
                "then select Copilot in Dennice Setup."
            ) from exc
        except BaseException:
            # Cancellation, permission errors, and other spawn failures happen
            # before the later process-finally block, so remove local telemetry
            # state here as well.
            shutil.rmtree(telemetry_dir, ignore_errors=True)
            raise

        if process.stdout is None or process.stderr is None:
            await stop_process(process)
            shutil.rmtree(telemetry_dir, ignore_errors=True)
            raise RuntimeError("Could not capture GitHub Copilot CLI output.")

        stderr_task = asyncio.create_task(read_bounded(process.stderr))
        try:
            yield RunEvent(
                run_id=run_id,
                kind=EventKind.MODEL_CALL_STARTED,
                payload={"attempt_id": f"copilot:{run_id}", "phase": "executor", "provider": self.id,
                         "model": self.model, "scope": "Copilot CLI invocation; token usage from local OTel"},
            )
            output_bytes = 0
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while chunk := await process.stdout.read(8192):
                    output_bytes += len(chunk)
                    if output_bytes > 2_000_000:
                        raise RuntimeError("GitHub Copilot CLI response exceeded the 2 MB output limit.")
                    text = decoder.decode(chunk, final=False)
                    if text:
                        yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})
                return_code = await process.wait()
                await stderr_task
                tail = decoder.decode(b"", final=True)
                if tail:
                    yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": tail})
            finally:
                await stop_process(process)
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)

            if return_code != 0:
                raise RuntimeError(
                    f"GitHub Copilot CLI exited with status {return_code}. "
                    "Inspect the provider CLI locally; raw diagnostics are withheld from the run trace."
                )

            usage = self._read_otel_usage(telemetry_path)
            if usage is None:
                if getattr(self, "budgets", None):
                    raise RuntimeError(
                        "Copilot completed without valid local token usage telemetry; the per-run "
                        "token budget could not be enforced. Check that your Copilot CLI supports OTel."
                    )
            else:
                input_tokens, output_tokens, model_calls = usage
                yield RunEvent(run_id=run_id, kind=EventKind.USAGE, payload={
                    "attempt_id": f"copilot:{run_id}", "phase": "executor", "provider": self.id,
                    "model": self.model, "reported": True, "source": "copilot_cli_otel",
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "model_calls": model_calls,
                    "input_includes_cache": True,
                    "scope": "top-level Copilot invoke_agent total; model-provider cost excluded",
                })
                budget = getattr(self, "budgets", None)
                if budget and (model_calls is None or model_calls > budget.max_model_calls):
                    raise RuntimeError(
                        "Copilot did not report a valid model-call total within the configured "
                        "limit; the per-run model-call budget could not be verified."
                    )
                if budget and input_tokens + output_tokens >= budget.max_total_tokens:
                    raise RuntimeError(
                        f"Copilot reported {input_tokens + output_tokens:,} tokens, reaching the "
                        f"{budget.max_total_tokens:,}-token per-run limit. The report arrives after "
                        "the CLI invocation; partial output is retained and no continuation is started."
                    )

            if output_bytes == 0:
                raise RuntimeError("GitHub Copilot CLI completed without an agent response.")
        finally:
            shutil.rmtree(telemetry_dir, ignore_errors=True)

    @staticmethod
    def _read_otel_usage(path: Path) -> tuple[int, int, int | None] | None:
        """Read top-level invoke_agent token totals without loading captured content."""
        try:
            with path.open("rb") as stream:
                raw = stream.read(4_000_001)
        except OSError:
            return None
        if len(raw) > 4_000_000:
            raise RuntimeError("Copilot usage telemetry exceeded its 4 MB safety limit.")
        totals = []
        try:
            for line in raw.splitlines():
                if not line.strip():
                    continue
                document = json.loads(line)
                for resource in document.get("resourceSpans", []):
                    for scope in resource.get("scopeSpans", []):
                        for span in scope.get("spans", []):
                            parent = span.get("parentSpanId", "")
                            if parent and set(parent) != {"0"}:
                                continue
                            attributes = span.get("attributes", [])
                            if isinstance(attributes, list):
                                attributes = {item.get("key"): item.get("value", {})
                                              for item in attributes if isinstance(item, dict)}
                            elif not isinstance(attributes, dict):
                                continue

                            def token_count(name):
                                value = attributes.get(name)
                                if isinstance(value, dict):
                                    value = value.get("intValue", value.get("int_value"))
                                if isinstance(value, str) and value.isdecimal():
                                    value = int(value)
                                return value if type(value) is int and value >= 0 else None

                            operation = attributes.get("gen_ai.operation.name")
                            if isinstance(operation, dict):
                                operation = operation.get("stringValue", operation.get("string_value"))
                            if operation != "invoke_agent" and span.get("name") != "invoke_agent":
                                continue
                            input_tokens = token_count("gen_ai.usage.input_tokens")
                            output_tokens = token_count("gen_ai.usage.output_tokens")
                            if input_tokens is not None and output_tokens is not None:
                                totals.append((input_tokens, output_tokens,
                                               token_count("github.copilot.turn_count")))
        except (ValueError, TypeError, AttributeError):
            return None
        return (sum(row[0] for row in totals), sum(row[1] for row in totals),
                sum(row[2] for row in totals) if all(row[2] is not None for row in totals) else None
                ) if totals else None
