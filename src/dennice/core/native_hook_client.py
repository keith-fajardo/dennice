"""Isolated stdlib-only Claude command hook. Transport failures deny execution.

Invoked by absolute path with Python -I: never import from the session directory.
"""
import http.client
import json
import os
import sys


def main():
    try:
        port = int(os.environ["DENNICE_HOOK_PORT"])
        token = os.environ["DENNICE_HOOK_TOKEN"]
        if not 0 < port < 65536 or len(token) != 64:
            raise ValueError()
        data = sys.stdin.buffer.read(32001)
        if not data or len(data) > 32000:
            raise ValueError()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=110)
        try:
            connection.request("POST", "/tool", body=data,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read(8001)
            if response.status != 200 or len(body) > 8000:
                raise ValueError()
            decision = json.loads(body)
            if decision.get("hookSpecificOutput", {}).get("permissionDecision") not in {"allow", "deny"}:
                raise ValueError()
            print(json.dumps(decision))
        finally:
            connection.close()
        return 0
    except Exception:
        # Claude treats hook exit 2 as a blocking denial, unlike HTTP failure.
        print("Dennice approval bridge unavailable; tool execution denied.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
