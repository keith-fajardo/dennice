"""Local JSON-RPC peer for Codex executor transport tests; no provider access."""

import json
import os
import sys
from pathlib import Path


log_path = Path(os.environ["DENNICE_CODEX_FIXTURE_LOG"])
auth = os.environ.get("DENNICE_CODEX_FIXTURE_AUTH", "chatgpt")
mode = os.environ.get("DENNICE_CODEX_FIXTURE_MODE", "approval")


def write(message):
    print(json.dumps(message), flush=True)


def log(value):
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialized":
        continue
    if method:
        log(method)
    if method == "initialize":
        write({"id": message["id"], "result": {"userAgent": "fixture"}})
    elif method == "account/read":
        write({"id": message["id"], "result": {
            "account": {"type": auth}, "requiresOpenaiAuth": True,
        }})
    elif method in {"thread/start", "thread/resume"}:
        write({"id": message["id"], "result": {"thread": {"id": "thread-fixture"}}})
    elif method == "turn/start":
        write({"id": message["id"], "result": {"turn": {"id": "turn-fixture"}}})
        if mode == "approval":
            write({"id": 900, "method": "item/fileChange/requestApproval", "params": {
                "threadId": "thread-fixture", "turnId": "turn-fixture", "itemId": "edit-fixture",
                "changes": [{"path": "sample.txt", "kind": "update"}],
            }})
        elif mode == "hang":
            write({"method": "item/agentMessage/delta", "params": {
                "threadId": "thread-fixture", "turnId": "turn-fixture",
                "itemId": "answer-fixture", "delta": "partial",
            }})
    elif method == "turn/interrupt":
        write({"id": message["id"], "result": {}})
    elif message.get("id") == 900:
        decision = message.get("result", {}).get("decision", "missing")
        log("approval:" + decision)
        write({"method": "item/agentMessage/delta", "params": {
            "threadId": "thread-fixture", "turnId": "turn-fixture",
            "itemId": "answer-fixture", "delta": "fixture answer",
        }})
        write({"method": "turn/completed", "params": {
            "threadId": "thread-fixture", "turnId": "turn-fixture",
            "turn": {"id": "turn-fixture", "status": "completed"},
        }})
