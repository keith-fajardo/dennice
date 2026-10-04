"""Real local child-process fixtures test framing, EOF and owned cleanup."""
import asyncio
import json
import sys

import pytest

from dennice.core.jsonrpc import AppServerClient


PROGRAM = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if method == 'initialized':
        continue
    if method == 'disconnect':
        break
    if method == 'malformed':
        print('not-json', flush=True)
        break
    if method == 'notification':
        print(json.dumps({'method':'test/event','params':{'value':1}}), flush=True)
    if method == 'error':
        result = {'id':request['id'], 'error':{'code':123, 'message':'fixture failure'}}
    else:
        result = {'id':request['id'], 'result':{'ok':True}}
    print(json.dumps(result), flush=True)
"""


def test_jsonrpc_frames_responses_events_errors_and_cleanup(monkeypatch):
    original = asyncio.create_subprocess_exec
    processes = []
    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        assert "app-server" in " ".join(args)
        process = await original(sys.executable, "-u", "-c", PROGRAM, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    async def journey():
        async with AppServerClient() as client:
            assert await client.request("notification", {}) == {"ok": True}
            assert await client.next_event() == {"method": "test/event", "params": {"value": 1}}
            with pytest.raises(RuntimeError, match="123"):
                await client.request("error", {})
            assert not client.pending
        assert processes[0].returncode is not None
        assert client.reader.done() and client.stderr.done()
    asyncio.run(journey())


@pytest.mark.parametrize("method", ["disconnect", "malformed"])
def test_jsonrpc_eof_and_malformed_stream_do_not_hang(monkeypatch, method):
    original = asyncio.create_subprocess_exec
    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        return await original(sys.executable, "-u", "-c", PROGRAM, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    async def journey():
        async with AppServerClient() as client:
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(client.request(method, {}), 2)
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(client.next_event(), 2)
    asyncio.run(journey())


def test_initialize_failure_reports_exit_status_without_stderr(monkeypatch):
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        program = "import sys; sys.stderr.write('private fixture diagnostic\\n'); sys.exit(7)"
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def journey():
        with pytest.raises(RuntimeError, match="exit status 7") as error:
            async with AppServerClient():
                pass
        assert "private fixture diagnostic" not in str(error.value)

    asyncio.run(journey())


def test_protocol_error_does_not_publish_raw_message(monkeypatch):
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == "taskkill":
            return await original(*args, **kwargs)
        program = """import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if message.get('method')=='initialized': continue
    error={'id':message['id'],'error':{'code':42,'message':'private-fixture-token'}}
    if message.get('method')=='initialize':
        print(json.dumps({'id':message['id'],'result':{}}),flush=True)
    else:
        print(json.dumps(error),flush=True)
"""
        return await original(sys.executable, "-u", "-c", program, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)

    async def journey():
        async with AppServerClient() as client:
            with pytest.raises(RuntimeError, match="protocol error 42") as error:
                await client.request("turn/start", {})
            assert "private-fixture-token" not in str(error.value)

    asyncio.run(journey())
