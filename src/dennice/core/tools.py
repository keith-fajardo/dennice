"""Scoped native tools. Writes/shell/MCP require exact-call user approval."""

import asyncio
import json
import os
import secrets
import stat
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator

from dennice.core.config import PermissionMode
from dennice.core.process import process_group_options, read_bounded, stop_process


def schema(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOL_DEFINITIONS = [
    {"name": "read_file", "description": "Read a bounded UTF-8 file within the approved workspace.",
     "parameters": schema({"path": {"type": "string"}}, ["path"])},
    {"name": "list_files", "description": "List a bounded workspace directory; no recursive secret discovery.",
     "parameters": schema({"path": {"type": "string"}}, ["path"])},
    {"name": "replace_text", "description": "Replace one exact occurrence in a file; requires workspace-write and user approval.",
     "parameters": schema({key: {"type": "string"} for key in ("path", "old", "new")}, ["path", "old", "new"])},
    {"name": "shell", "description": "Request user approval to run an argument-array command. Not an OS sandbox; user-authorized full process authority.",
     "parameters": schema({"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100}}, ["argv"])},
]
SENSITIVE = {".git", ".dennice", ".ssh", ".aws", ".codex", ".claude", ".env"}


class ToolBroker:
    def __init__(self, config, *, approve=None, hooks=None, emit=None, mcp=None):
        self.config = config.model_copy(deep=True)
        self.root = Path(config.tools.root).resolve()
        self.approve, self.hooks, self.emit, self.mcp = approve, hooks, emit, mcp
        self.calls = 0
        self.seen: dict[str, int] = {}

    async def event(self, kind, payload):
        if self.emit:
            await self.emit(kind, payload)

    def path(self, value):
        value = Path(value)
        if value.is_absolute() or ".." in value.parts:
            raise PermissionError("Tool paths must be relative and cannot traverse parents.")
        if any(part in SENSITIVE or part.startswith(".env.") for part in value.parts):
            raise PermissionError("Sensitive workspace paths are not exposed to model tools.")
        path = self.root / value
        for ancestor in (path, *path.parents):
            if ancestor == self.root:
                break
            if ancestor.is_symlink():
                raise PermissionError("Symlink paths are not permitted.")
        path.resolve().relative_to(self.root)
        return path

    def _open(self, value, flags):
        path = self.path(value)
        # Descriptor-relative walking closes parent-symlink swap escapes. Native
        # filesystem tools fail closed where this contract is unavailable.
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise PermissionError("Native file tools need descriptor-relative no-symlink support on this platform.")
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = path.relative_to(self.root).parts
            if not parts:
                raise PermissionError("Expected a file path")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            return os.open(parts[-1], flags | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)

    def definitions(self):
        definitions = TOOL_DEFINITIONS[:2]
        if self.config.executor.permission_mode == PermissionMode.WORKSPACE_WRITE:
            definitions = TOOL_DEFINITIONS
            return definitions + (self.mcp.definitions() if self.mcp else [])
        # Server annotations cannot prove that a remote operation is read-only.
        return definitions

    async def invoke(self, name, arguments, call_id):
        from dennice.core.models import EventKind
        definition = next((d for d in self.definitions() if d["name"] == name), None)
        if definition is None:
            raise PermissionError("Tool is not available under the current permissions.")
        if len(json.dumps(arguments).encode()) > self.config.tools.output_limit:
            raise ValueError("Tool arguments exceed the size limit.")
        Draft202012Validator(definition["parameters"]).validate(arguments)
        self.calls += 1
        if self.calls > self.config.budgets.max_tool_calls:
            raise RuntimeError("Tool-call budget exhausted.")
        signature = json.dumps([name, arguments], sort_keys=True)
        self.seen[signature] = self.seen.get(signature, 0) + 1
        if self.seen[signature] > 2:
            raise RuntimeError("Repeated identical tool calls stopped (loop guard).")
        if self.hooks:
            await self.hooks.dispatch(self.config.hooks, "before_tool", {"tool": name, "call_id": call_id, "arguments": arguments}, str(self.root), emit=self.emit)
        if name not in {"read_file", "list_files"}:
            await self.event(EventKind.APPROVAL_REQUESTED, {"tool": name, "call_id": call_id, "arguments": arguments})
            approved = bool(self.approve and await self.approve(name, arguments))
            await self.event(EventKind.APPROVAL_RESOLVED, {"tool": name, "call_id": call_id, "approved": approved})
            if not approved:
                raise PermissionError("Tool call denied; no action was executed.")
        operation_id = uuid4().hex
        await self.event(EventKind.TOOL_STARTED, {"tool": name, "call_id": call_id,
                                                "operation_id": operation_id})
        result = await self._execute(name, arguments)
        result = result[:self.config.tools.output_limit]
        await self.event(EventKind.TOOL_COMPLETED, {"tool": name, "call_id": call_id,
                                                  "operation_id": operation_id, "result": result})
        if self.hooks:
            await self.hooks.dispatch(self.config.hooks, "after_tool", {"tool": name, "call_id": call_id}, str(self.root), emit=self.emit)
        return result

    async def _execute(self, name, arguments):
        limit = self.config.tools.output_limit
        if name == "read_file":
            with os.fdopen(self._open(arguments["path"], os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise PermissionError("Native reads require a regular, non-hardlinked file.")
                return stream.read(limit).decode("utf-8", errors="replace")
        if name == "list_files":
            path = self.path(arguments["path"])
            if os.open not in os.supports_dir_fd:
                raise PermissionError("Native file tools unsupported on this platform.")
            fd = self._open(arguments["path"], os.O_RDONLY | os.O_DIRECTORY) if path != self.root else os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                return "\n".join(sorted(name for name in os.listdir(fd) if name not in SENSITIVE and not name.startswith(".env."))[:500])
            finally:
                os.close(fd)
        if name == "replace_text":
            if not arguments["old"]:
                raise ValueError("Replacement requires a nonempty exact match.")
            with os.fdopen(self._open(arguments["path"], os.O_RDONLY | os.O_NONBLOCK), "r", encoding="utf-8") as stream:
                original_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(original_stat.st_mode) or original_stat.st_nlink != 1:
                    raise PermissionError("Native edits require a regular, non-hardlinked file.")
                if os.fstat(stream.fileno()).st_size > limit:
                    raise ValueError("File exceeds native edit size limit.")
                original = stream.read()
                if original.count(arguments["old"]) != 1:
                    raise ValueError("Replacement must match exactly once.")
                updated = original.replace(arguments["old"], arguments["new"], 1)
                if len(updated.encode()) > limit:
                    raise ValueError("Updated file exceeds native edit size limit.")
            self._atomic_replace(arguments["path"], updated.encode(), original_stat)
            return "Exact replacement applied."
        if name == "shell":
            if self.config.privacy.local_only:
                raise PermissionError("Shell is disabled in local-only mode (no network sandbox).")
            return await run_command(arguments["argv"], self.root, self.config.tools.timeout_seconds, limit)
        if self.mcp:
            return await self.mcp.call(name, arguments)
        raise PermissionError("Unavailable tool")

    def _atomic_replace(self, value, content, original_stat):
        """Stage and rename inside an anchored parent; never truncate in place."""
        parent = self.path(value).relative_to(self.root)
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = f".dennice-edit-{secrets.token_hex(12)}"
        staged = False
        try:
            for part in parent.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            current = os.stat(parent.name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (
                original_stat.st_dev, original_stat.st_ino, original_stat.st_mtime_ns, original_stat.st_size
            ):
                raise RuntimeError("File changed during the edit; no replacement applied.")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         stat.S_IMODE(original_stat.st_mode), dir_fd=directory)
            staged = True
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, parent.name, src_dir_fd=directory, dst_dir_fd=directory)
            staged = False
            os.fsync(directory)
        finally:
            if staged:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)


async def run_command(argv, root, timeout, limit=24000):
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **process_group_options(),
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await asyncio.gather(read_bounded(process.stdout, limit), read_bounded(process.stderr, limit))
            status = await process.wait()
        return json.dumps({"exit_code": status, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")})
    finally:
        await stop_process(process)
