"""Run-local exact-call Claude gate using documented PreToolUse command hooks."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import sys

from dennice.core.config import DenniceConfig, PermissionMode
from dennice.core.models import EventKind
from dennice.core.tools import ToolBroker


def verdict(allowed, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "allow" if allowed else "deny", "permissionDecisionReason": reason}}


class NativeToolGate:
    def __init__(self, root, permission, *, approve=None, emit=None, hooks=None, hook_configs=(), budgets=None):
        self.root = str(Path(root).resolve())
        self.permission, self.approve, self.emit = permission, approve, emit
        self.hooks, self.hook_configs, self.budgets = hooks, hook_configs, budgets
        config = DenniceConfig()
        config.tools.root = self.root
        self.paths = ToolBroker(config)
        self.token = secrets.token_hex(32)
        self.seen, self.tasks = set(), set()
        self.lock = asyncio.Lock()
        self.calls = 0
        self.session_id = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=4096)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self.server.close()
        await self.server.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def settings(self):
        helper = str(Path(__file__).with_name("native_hook_client.py").resolve())
        command = shlex.join([sys.executable, "-I", helper])
        return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": command, "timeout": 120}]}]}}

    def environment(self):
        return {**os.environ, "DENNICE_HOOK_PORT": str(self.port), "DENNICE_HOOK_TOKEN": self.token}

    async def journal(self, kind, payload):
        if self.emit:
            await self.emit(kind, payload)

    async def decide(self, payload):
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse":
            return verdict(False, "Invalid native event")
        if not isinstance(payload.get("cwd"), str) or str(Path(payload["cwd"]).resolve()) != self.root:
            return verdict(False, "Session directory mismatch")
        call_id, tool, arguments = payload.get("tool_use_id"), payload.get("tool_name"), payload.get("tool_input")
        native_session = payload.get("session_id")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256 or not isinstance(arguments, dict):
            return verdict(False, "Invalid tool request")
        if self.session_id and native_session != self.session_id:
            return verdict(False, "Native session mismatch")
        if tool not in {"Read", "Glob", "Grep", "Edit", "Write"}:
            return verdict(False, "Tool is outside the restricted file profile")
        async with self.lock:
            if call_id in self.seen:
                return verdict(False, "Duplicate tool request; no automatic replay")
            self.seen.add(call_id)
            self.calls += 1
            if self.budgets and self.calls > self.budgets.max_tool_calls:
                return verdict(False, "Tool budget exhausted")
            path = arguments.get("file_path" if tool in {"Read", "Edit", "Write"} else "path", ".")
            def check_path():
                if not isinstance(path, str):
                    raise PermissionError("Invalid tool path")
                target = Path(path)
                relative = str(target.relative_to(self.root)) if target.is_absolute() else path
                checked = self.paths.path(relative)
                if tool in {"Read", "Edit", "Write", "Grep"}:
                    if checked == Path(self.root):
                        raise PermissionError("Expected a file, not the workspace root")
                    if checked.exists():
                        info = checked.stat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise PermissionError("Only regular, non-hardlinked files are allowed")
                    elif tool == "Grep":
                        raise PermissionError("Grep requires an explicit existing file")
                return checked
            try:
                check_path()
                if tool == "Glob":
                    pattern = arguments.get("pattern")
                    if not isinstance(pattern, str) or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                        raise PermissionError("Glob patterns must remain workspace-relative")
                if tool in {"Edit", "Write"} and self.permission != PermissionMode.WORKSPACE_WRITE:
                    return verdict(False, "Writing is disabled under current permissions")
                metadata = {"provider": "claude", "tool": tool, "call_id": call_id,
                            "arguments": arguments, "cwd": self.root, "blocking": True}
                if self.hooks:
                    await self.hooks.dispatch(self.hook_configs, "before_tool", metadata, self.root, emit=self.emit)
                allowed = True
                if tool in {"Edit", "Write"}:
                    await self.journal(EventKind.APPROVAL_REQUESTED, metadata)
                    allowed = bool(self.emit and self.approve and await self.approve(f"Claude {tool}", metadata))
                    # Recheck scope after the user has answered. Native file
                    # tools remain provider-owned, not an OS filesystem sandbox.
                    check_path()
                    await self.journal(EventKind.APPROVAL_RESOLVED, {
                        "provider": "claude", "tool": tool, "call_id": call_id, "approved": allowed})
                return verdict(allowed, "Exact file operation approved" if allowed else "User denied file operation")
            except (PermissionError, ValueError, OSError, RuntimeError):
                return verdict(False, "Scope, hook or audit check failed; no authority granted")

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if len(self.tasks) > 8:
                return
            async with asyncio.timeout(5):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 4096:
                    return
                lines = header.decode("ascii").split("\r\n")
                if lines[0] != "POST /tool HTTP/1.1":
                    return
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.lower()
                    if key in headers:
                        return
                    headers[key] = value.strip()
                if not hmac.compare_digest(headers.get("authorization", ""), f"Bearer {self.token}"):
                    return
                size = int(headers.get("content-length", "0"))
                if not 0 < size <= 32000 or "transfer-encoding" in headers:
                    return
                payload = json.loads(await reader.readexactly(size))
            async with asyncio.timeout(100):
                result = await self.decide(payload)
            output = json.dumps(result).encode()
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(output)}\r\nConnection: close\r\n\r\n".encode() + output)
            await writer.drain()
        except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass  # helper exit 2 denies malformed, timed-out or disconnected calls
        finally:
            writer.close()
            self.tasks.discard(task)
