"""Minimal stdio JSON-RPC MCP peer for hostile discovery and result cases."""

import json
import sys


mode = sys.argv[1]


def reply(request, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "mcp-fault-fixture", "version": "1"},
        }
    elif method == "tools/list":
        schema = {"type": "object", "properties": {}}
        if mode == "external_schema":
            schema["properties"]["value"] = {"$ref": "https://example.invalid/schema.json"}
        result = {"tools": [{"name": "approved", "inputSchema": schema}]}
        if mode == "tool_cycle":
            result["nextCursor"] = "same"
    elif method == "resources/list":
        uri = "file:///secret" if mode == "unexpected_resource" else "file:///approved"
        result = {"resources": [{"name": "fixture", "uri": uri}]}
        if mode == "resource_cycle":
            result["nextCursor"] = "same"
    elif method == "resources/read":
        uri = "file:///secret" if mode == "substituted_resource" else "file:///approved"
        result = {"contents": [{"uri": uri, "text": "fixture content"}]}
    elif method == "prompts/list":
        arguments = [{"name": "subject"}, {"name": "subject"}] if mode == "duplicate_prompt_args" else []
        result = {"prompts": [{"name": "approved", "arguments": arguments}]}
        if mode == "prompt_cycle":
            result["nextCursor"] = "same"
    else:
        result = {}
    reply(request, result)
