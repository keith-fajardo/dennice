"""Explicitly trusted, bounded user hooks. Hook output never grants authority."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from dennice.core.config import HookConfig
from dennice.core.process import process_group_options, read_bounded, stop_process


def fingerprint(config) -> str:
    # Enable/disable does not alter executable identity; any other config
    # change invalidates launch-local trust.
    return hashlib.sha256(json.dumps(config.model_dump(mode="json", exclude={"enabled"}), sort_keys=True).encode()).hexdigest()


class HookManager:
    def __init__(self):
        self.trusted: set[str] = set()
        self.trusted_roots: dict[str, set[str]] = {}

    def trust(self, hook: HookConfig, *, root=None) -> None:
        identity = fingerprint(hook)
        self.trusted.add(identity)
        if root is not None:
            self.trusted_roots.setdefault(identity, set()).add(str(Path(root).resolve()))

    def is_trusted(self, hook, root=None):
        identity = fingerprint(hook)
        return identity in self.trusted and (identity not in self.trusted_roots or
            str(Path(root or ".").resolve()) in self.trusted_roots[identity])

    async def dispatch(self, hooks, event: str, payload: dict, root: str, emit=None):
        from dennice.core.models import EventKind
        outcomes = []
        for hook in hooks:
            if not hook.enabled or hook.event != event:
                continue
            if not self.is_trusted(hook, root):
                raise PermissionError(f"Hook {hook.name} requires explicit /hooks trust {hook.name} for this launch.")
            hook_payload = {"event": event, **payload}
            if not hook.include_arguments:
                hook_payload.pop("arguments", None)
            encoded = (json.dumps(hook_payload) + "\n").encode()
            if len(encoded) > 32000:
                raise ValueError("Hook input exceeds the metadata size limit.")
            identity = {"name": hook.name, "event": event, "fingerprint": fingerprint(hook),
                        "operation_id": uuid4().hex}
            if emit:
                await emit(EventKind.HOOK_STARTED, identity)
            started = time.monotonic()
            success = False
            outcome_known = False
            process = await asyncio.create_subprocess_exec(
                *hook.command, cwd=root, env={key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "SYSTEMROOT") if key in os.environ},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, **process_group_options(),
            )
            try:
                async with asyncio.timeout(hook.timeout_seconds):
                    process.stdin.write(encoded)
                    await process.stdin.drain()
                    process.stdin.close()
                    output = await read_bounded(process.stdout, 8000)
                    code = await process.wait()
                    outcome_known = True
                if code != 0 and hook.required:
                    raise RuntimeError(f"Required hook {hook.name} failed (exit {code}).")
                try:
                    decision = json.loads(output or b"{}")
                except (ValueError, UnicodeError):
                    if hook.required:
                        raise RuntimeError(f"Hook {hook.name} returned invalid JSON.") from None
                    decision = {}
                if not isinstance(decision, dict):
                    raise RuntimeError(f"Hook {hook.name} returned an invalid decision.")
                if decision.get("decision") == "deny":
                    raise PermissionError(f"Hook {hook.name} denied {event}.")
                if decision.get("decision") not in {None, "allow"}:
                    raise RuntimeError(f"Hook {hook.name} returned an unsupported decision.")
                outcomes.append({"name": hook.name, "event": event, "success": code == 0})
                success = code == 0
            except TimeoutError:
                if hook.required:
                    raise RuntimeError(f"Required hook {hook.name} timed out.") from None
                outcomes.append({"name": hook.name, "event": event, "success": False})
            finally:
                await stop_process(process)
                if emit:
                    await emit(EventKind.HOOK_COMPLETED, {**identity, "success": success,
                               "outcome_known": outcome_known,
                               "elapsed_seconds": time.monotonic() - started})
        return outcomes
