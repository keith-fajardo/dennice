"""Bounded JSONL client for the documented local Codex app-server protocol."""

import asyncio
import json
from contextlib import suppress

from dennice.core.process import command_for_platform, process_group_options, read_bounded, stop_process


class AppServerClient:
    def __init__(self, command="codex", *, cwd=None):
        self.command, self.cwd = command, cwd
        self.pending = {}
        self.events = asyncio.Queue(maxsize=256)
        self.sequence = 0
        self.failure = None
        self.process = None
        self.reader = self.stderr = None

    async def __aenter__(self):
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command_for_platform([self.command, "app-server"]), cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=1_048_576,
                **process_group_options(),
            )
        except FileNotFoundError:
            raise RuntimeError("Codex CLI was not found. Install Codex and sign in separately.") from None
        self.stderr = asyncio.create_task(read_bounded(self.process.stderr))
        self.reader = asyncio.create_task(self._read())
        try:
            await self.request("initialize", {"clientInfo": {
                "name": "dennice", "title": "Dennice", "version": "0.1.0",
            }})
            await self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_):
        await self.close()

    async def send(self, message):
        encoded = (json.dumps(message, ensure_ascii=False) + "\n").encode()
        if len(encoded) > 8 * 1024 * 1024:
            raise RuntimeError("Codex protocol request exceeded the bounded input limit")
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()

    async def request(self, method, params, *, timeout=20):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid protocol envelope")
                if "method" not in message and "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            # Provider diagnostics can contain user data. Keep
                            # the code and bounded message, never stderr/tokens.
                            error = message["error"]
                            future.set_exception(RuntimeError(
                                f"Codex protocol error {error.get('code')}: {str(error.get('message', 'request failed'))[:1000]}"
                            ))
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    self.events.put_nowait(message)
            self.failure = RuntimeError("Codex app-server closed before the operation completed")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failure = RuntimeError(f"Codex protocol stream stopped ({type(error).__name__})")
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.failure or RuntimeError("Codex connection closed"))

    async def next_event(self):
        while self.events.empty():
            if self.reader.done():
                raise self.failure or RuntimeError("Codex app-server closed")
            waiter = asyncio.create_task(self.events.get())
            try:
                done, _ = await asyncio.wait((waiter, self.reader), return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    return waiter.result()
            finally:
                waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await waiter
        return self.events.get_nowait()

    async def close(self):
        if self.process is not None:
            await stop_process(self.process)
        for task in (self.reader, self.stderr):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self.reader, self.stderr) if task), return_exceptions=True)


async def load_codex_model_catalog():
    """Metadata-only request: no thread, turn, or inference is started."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="dennice-codex-models-") as directory:
        async with AppServerClient(cwd=directory) as client:
            options, seen, cursor = [], set(), None
            async with asyncio.timeout(25):
                for _ in range(20):
                    params = {"limit": 100, "includeHidden": False}
                    if cursor:
                        params["cursor"] = cursor
                    page = await client.request("model/list", params)
                    for model in page.get("data", []):
                        value = model.get("model") or model.get("id")
                        if isinstance(value, str) and value and value not in seen:
                            seen.add(value)
                            name = str(model.get("displayName") or value)
                            options.append((name if name == value else f"{name} [{value}]", value))
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                else:
                    raise RuntimeError("Codex model catalog exceeded pagination limit")
                if not options:
                    raise RuntimeError("Codex returned no available models")
                return (("Default (recommended)", "default"), *options, ("Custom model…", "custom"))
