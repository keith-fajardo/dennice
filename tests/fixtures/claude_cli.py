"""Local Claude CLI stream fixture; it never contacts a provider."""

import json
import os
import sys
import time
from pathlib import Path


log_path = Path(os.environ["DENNICE_CLAUDE_FIXTURE_LOG"])
operation = sys.argv[1]
mode = os.environ.get("DENNICE_CLAUDE_FIXTURE_MODE", "complete")


def log(value):
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")


def emit(value):
    print(json.dumps(value), flush=True)


if operation == "auth":
    log("auth")
    emit({"loggedIn": os.environ.get("DENNICE_CLAUDE_FIXTURE_SIGNED_IN", "true") == "true",
          "authMethod": "claude.ai"})
elif operation == "turn":
    log("turn:" + sys.argv[2])
    emit({"type": "system", "subtype": "init", "session_id": "native-fixture", "model": "claude-fixture"})
    emit({"type": "assistant", "session_id": "native-fixture", "message": {
        "id": "message-fixture", "content": [{"type": "text", "text": "Fixture answer"}],
    }})
    if mode == "hang":
        time.sleep(60)
    elif mode == "fail":
        print("private-fixture-token", file=sys.stderr, flush=True)
        sys.exit(7)
    elif mode == "structured_fail":
        emit({"type": "assistant", "session_id": "native-fixture",
              "error": "private-fixture-token", "message": {"id": "error-fixture", "content": []}})
        emit({"type": "result", "subtype": "error_during_execution", "session_id": "native-fixture",
              "is_error": True, "result": "", "errors": ["private-fixture-token"]})
    elif mode == "complete":
        emit({"type": "result", "subtype": "success", "session_id": "native-fixture",
              "is_error": False, "result": "Fixture answer", "usage": {
                  "input_tokens": 10, "output_tokens": 3,
              }, "total_cost_usd": 0.0})
