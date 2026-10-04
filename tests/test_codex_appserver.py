"""Offline protocol conformance fixtures; never launches Codex or inference."""
import asyncio

import pytest

from dennice.core.config import PermissionMode
from dennice.core.models import EventKind, ExecutionRequest, Task
from dennice.executors.codex_appserver import CodexAppServerExecutor


class FixtureClient:
    instances = []
    messages = []
    def __init__(self, *args, **kwargs):
        self.requests, self.sent = [], []
        self.queue = list(self.messages)
        self.closed = False
        self.instances.append(self)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        self.closed = True
    async def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread-fixture"}}
        return {"turn": {"id": "turn-fixture"}}
    async def next_event(self):
        if not self.queue:
            raise RuntimeError("Disconnected before completion")
        return self.queue.pop(0)
    async def send(self, message):
        self.sent.append(message)


def note(method, **params):
    return {"method": method, "params": {"threadId": "thread-fixture", "turnId": "turn-fixture", **params}}


def complete(status="completed"):
    return note("turn/completed", turn={"id": "turn-fixture", "status": status})


@pytest.fixture
def client(monkeypatch):
    FixtureClient.instances, FixtureClient.messages = [], []
    monkeypatch.setattr("dennice.executors.codex_appserver.AppServerClient", FixtureClient)
    return FixtureClient


def request():
    return ExecutionRequest(task=Task(prompt="Next task", context={"conversation_history": [{"role": "user", "content": "previous"}]},
                                      metadata={"session_id": "session-fixture"}), system_instructions="Fresh policy")


def test_codex_native_resume_stream_tool_and_usage(client, tmp_path):
    client.messages = [note("item/agentMessage/delta", itemId="answer", delta="Hello"),
        note("item/completed", item={"id": "answer", "type": "agentMessage", "text": "Hello"}),
        note("item/started", item={"id": "search", "type": "webSearch", "action": {"query": "docs"}}),
        note("item/completed", item={"id": "search", "type": "webSearch"}),
        note("thread/tokenUsage/updated", tokenUsage={
            "total": {"inputTokens": 12, "outputTokens": 8},
            "last": {"totalTokens": 90, "inputTokens": 80, "outputTokens": 10, "reasoningOutputTokens": 0},
            "modelContextWindow": 1000,
        }), complete()]
    async def journey():
        executor = CodexAppServerExecutor()
        executor.configure_runtime(store_path=tmp_path, root=tmp_path)
        first = [event async for event in executor.execute("run-first", request())]
        assert [e.payload["text"] for e in first if e.kind == EventKind.MODEL_STREAM] == ["Hello"]
        assert sum(e.kind == EventKind.TOOL_STARTED for e in first) == 1
        assert sum(e.kind == EventKind.TOOL_COMPLETED for e in first) == 1
        assert next(e.payload["input_tokens"] for e in first if e.kind == EventKind.USAGE) == 12
        usage_event = next(e for e in first if e.kind == EventKind.USAGE)
        assert usage_event.payload["context_tokens"] == 90
        assert usage_event.payload["context_window_tokens"] == 1000
        client.messages = [note("thread/tokenUsage/updated", tokenUsage={"total": {"inputTokens": 15, "outputTokens": 10}}), complete()]
        followup = request()
        followup.task.context["compaction_summary"] = "IMPORTANT EARLIER REQUIREMENT"
        second = [e async for e in executor.execute("run-second", followup)]
        native = client.instances[-1]
        resume = native.requests[0]
        assert resume[0] == "thread/resume"
        assert resume[1]["developerInstructions"] == "Fresh policy"
        turn = next(params for method, params in native.requests if method == "turn/start")
        assert "previous" not in turn["input"][0]["text"]
        assert turn["sandboxPolicy"] == {"type": "readOnly"}
        assert next(e.payload["input_tokens"] for e in second if e.kind == EventKind.USAGE) == 3
        assert "IMPORTANT EARLIER REQUIREMENT" in turn["input"][0]["text"]
        assert all(instance.closed for instance in client.instances)
    asyncio.run(journey())


@pytest.mark.parametrize("mode,consent,decision", [(PermissionMode.READ_ONLY, True, "decline"),
    (PermissionMode.PLAN, True, "decline"), (PermissionMode.WORKSPACE_WRITE, False, "decline"),
    (PermissionMode.WORKSPACE_WRITE, True, "accept")])
def test_codex_exact_approval_audited_before_response(client, tmp_path, mode, consent, decision):
    message = note("item/commandExecution/requestApproval", itemId="command", command="echo example")
    message["id"] = 11
    client.messages = [message, {"id": 12, **note("unknown/privilegedRequest")}, complete()]
    async def journey():
        audit = []
        async def emit(kind, payload):
            audit.append((kind, payload))
            assert not client.instances[-1].sent
        async def approve(tool, args):
            assert audit[0][0] == EventKind.APPROVAL_REQUESTED
            assert args["command"] == "echo example"
            return consent
        executor = CodexAppServerExecutor(permission_mode=mode)
        executor.configure_runtime(root=tmp_path, approve=approve, emit=emit)
        _ = [event async for event in executor.execute("run-approval", request())]
        assert client.instances[-1].sent[0] == {"id": 11, "result": {"decision": decision}}
        assert client.instances[-1].sent[1]["error"]["code"] == -32601
        assert audit[1][1]["approved"] == (decision == "accept")
    asyncio.run(journey())


@pytest.mark.parametrize("events", [[note("item/agentMessage/delta", delta="Interim")], [complete("failed")]])
def test_codex_disconnect_or_failed_turn_is_not_success(client, tmp_path, events):
    client.messages = events
    async def journey():
        executor = CodexAppServerExecutor()
        executor.configure_runtime(root=tmp_path)
        with pytest.raises(RuntimeError):
            _ = [event async for event in executor.execute("run-failed", request())]
        assert client.instances[-1].closed
        assert client.instances[-1].requests[-1][0] == "turn/interrupt"
    asyncio.run(journey())


def test_model_catalog_uses_metadata_only_paginated_api(monkeypatch):
    from dennice.core.jsonrpc import load_codex_model_catalog
    class MetadataClient(FixtureClient):
        async def request(self, method, params, **kwargs):
            self.requests.append((method, params))
            assert method == "model/list"
            if params.get("cursor"):
                return {"data": [{"model": "second", "displayName": "Second"}]}
            return {"data": [{"model": "first", "displayName": "First"}], "nextCursor": "page-2"}
    MetadataClient.instances = []
    monkeypatch.setattr("dennice.core.jsonrpc.AppServerClient", MetadataClient)
    options = asyncio.run(load_codex_model_catalog())
    assert ("First [first]", "first") in options
    assert ("Second [second]", "second") in options
    assert len(MetadataClient.instances[0].requests) == 2
