"""Streaming API executors with bounded, explicitly enabled native tool loops."""

import asyncio
import json
import os
import httpx
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from dennice.core.config import ProviderConfig
from dennice.core.models import EventKind, RunEvent
from dennice.core.attachments import task_images, image_data
from dennice.core.skills import conversation_history, selected_skill_content


API_PROVIDERS = {"openai-api", "anthropic-api", "local"}
API_DEFAULTS = {
    "openai-api": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic-api": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "local": ("http://127.0.0.1:11434/v1", ""),
}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_complete_response(provider: str, result: dict) -> None:
    """A usable answer requires a terminal provider status, not just text."""
    if not isinstance(result, dict):
        raise RuntimeError("Provider returned an invalid response object.")
    if provider == "openai-api":
        complete = result.get("status") == "completed"
    elif provider == "anthropic-api":
        complete = result.get("stop_reason") in {"end_turn", "tool_use", "stop_sequence"}
    else:
        choices = result.get("choices") or []
        complete = bool(choices and choices[0].get("finish_reason") in {"stop", "tool_calls"})
    if not complete:
        raise RuntimeError("Provider response is incomplete.")


def connection(config: ProviderConfig):
    default_url, default_env = API_DEFAULTS[config.provider]
    base = (config.base_url or default_url).rstrip("/")
    parsed = urlparse(base)
    expected_host = urlparse(default_url).hostname
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Use a base URL without credentials, query parameters, or fragments.")
    if config.provider == "local":
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.scheme not in {"http", "https"}:
            raise RuntimeError("Local mode requires a loopback endpoint (localhost or 127.0.0.1).")
    elif parsed.scheme != "https" or parsed.hostname != expected_host or parsed.port not in {None, 443}:
        raise RuntimeError(f"{config.provider} requires its official HTTPS endpoint; credentials are not sent to other hosts.")
    env = config.api_key_env if config.api_key_env is not None else default_env
    key = os.environ.get(env, "") if env else ""
    if config.provider != "local" and not key:
        raise RuntimeError(f"Set {env or default_env} in your environment before using {config.provider}.")
    headers = {"Content-Type": "application/json"}
    if config.provider == "anthropic-api":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return base, headers


def request_json(config: ProviderConfig, path: str, body: dict | None = None):
    base, headers = connection(config)
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method="POST" if body is not None else "GET")
    try:
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        # Never echo a server response that could reflect credentials/prompts.
        raise RuntimeError(f"{config.provider} returned HTTP {error.code}. Check the key, model access, and endpoint.") from None
    except URLError:
        raise RuntimeError(f"Cannot connect to {config.provider}. Check the endpoint and server.") from None


async def load_api_models(config: ProviderConfig):
    models, cursor, seen = [], "", set()
    for _ in range(100):
        path = "/models"
        if config.provider == "anthropic-api":
            path += "?" + urlencode({"limit": 100, **({"after_id": cursor} if cursor else {})})
        result = await asyncio.to_thread(request_json, config, path)
        for model in result.get("data", []):
            model_id = model.get("id")
            if model_id and model_id not in seen:
                seen.add(model_id)
                name = model.get("display_name") or model_id
                models.append((f"{name} [{model_id}]" if name != model_id else model_id, model_id))
        if not result.get("has_more"):
            break
        next_cursor = result.get("last_id")
        if not next_cursor or next_cursor == cursor:
            raise RuntimeError("Model catalog pagination did not advance.")
        cursor = next_cursor
    else:
        raise RuntimeError("Model catalog exceeded the pagination limit.")
    if not models:
        raise RuntimeError("The provider returned no models. Load a local model or check API access.")
    return (*sorted(models), ("Custom model…", "custom"))


class APIExecutor:
    version = "api-v1"

    def __init__(self, config: ProviderConfig, *, broker=None, budgets=None, emit=None):
        self.config = config
        self.id = config.provider
        self.broker, self.budgets, self.emit = broker, budgets, emit

    def payload(self, request):
        if self.config.model in {"", "default", "v1", "custom"}:
            raise RuntimeError("Choose a specific API/local model in /model or /setup first.")
        if request.task.context.get("selected_skill") and not self.broker:
            raise RuntimeError("Filesystem skills require Codex or Claude Code; API/local executors have no local tools yet.")
        body = {"model": self.config.model}
        prompt = selected_skill_content(request.task) + request.task.prompt
        history = conversation_history(request.task)
        images = [image_data(path) for path in task_images(request.task)]
        if self.config.provider == "openai-api":
            body.update({"instructions": request.system_instructions, "input": prompt, "store": False})
            if images:
                body["input"] = [{"role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    *[{"type": "input_image", "image_url": f"data:{media};base64,{data}"} for media, data in images],
                ]}]
            if self.config.reasoning_effort:
                body["reasoning"] = {"effort": self.config.reasoning_effort.value}
            if history:
                current = body["input"] if isinstance(body["input"], list) else [{"role": "user", "content": prompt}]
                body["input"] = history + current
            return "/responses", body
        if self.config.provider == "anthropic-api":
            body.update({"system": request.system_instructions, "max_tokens": 4096,
                         "messages": [{"role": "user", "content": prompt}]})
            if images:
                body["messages"][-1]["content"] = [
                    {"type": "text", "text": prompt},
                    *[{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}} for media, data in images],
                ]
            if self.config.reasoning_effort:
                body["output_config"] = {"effort": self.config.reasoning_effort.value}
            body["messages"] = history + body["messages"]
            return "/messages", body
        body["messages"] = [{"role": "system", "content": request.system_instructions},
                            {"role": "user", "content": prompt}]
        if images:
            body["messages"][1]["content"] = [
                {"type": "text", "text": prompt},
                *[{"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}} for media, data in images],
            ]
        body["messages"] = body["messages"][:1] + history + body["messages"][1:]
        return "/chat/completions", body

    async def execute(self, run_id, request):
        path, body = self.payload(request)
        if self.budgets:
            async for event in self._agent_execute(run_id, path, body):
                yield event
            return
        result = await asyncio.to_thread(request_json, self.config, path, body)
        require_complete_response(self.config.provider, result)
        if self.config.provider == "openai-api":
            text = "".join(part.get("text", "") for item in result.get("output", [])
                           for part in item.get("content", []) if part.get("type") == "output_text")
        elif self.config.provider == "anthropic-api":
            text = "".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        else:
            choices = result.get("choices", [])
            text = choices[0].get("message", {}).get("content", "") if choices else ""
        if not text:
            raise RuntimeError("Provider returned no text response. Verify the selected model supports text chat.")
        yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": text})
        usage = result.get("usage") or {}
        if isinstance(usage, dict):
            if self.config.provider == "anthropic-api":
                input_tokens = usage.get("input_tokens")
                cached_tokens = usage.get("cache_read_input_tokens", 0)
                cache_created = usage.get("cache_creation_input_tokens", 0)
                context_input = input_tokens + cached_tokens + cache_created if all(
                    type(value) is int and value >= 0 for value in (input_tokens, cached_tokens, cache_created)
                ) else None
            else:
                input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
                output_details = usage.get("input_tokens_details") or {}
                cached_tokens = output_details.get("cached_tokens") if isinstance(output_details, dict) else None
                cache_created = None
                context_input = input_tokens if type(input_tokens) is int and input_tokens >= 0 else None
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
            context_tokens = context_input + output_tokens if context_input is not None and type(output_tokens) is int and output_tokens >= 0 else None
            yield RunEvent(run_id=run_id, kind=EventKind.USAGE, payload={
                "phase": "executor", "provider": self.config.provider, "model": self.config.model,
                "reported": bool(usage), "input_tokens": input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
                "output_tokens": output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
                "cached_input_tokens": cached_tokens if type(cached_tokens) is int and cached_tokens >= 0 else None,
                "cache_creation_input_tokens": cache_created if type(cache_created) is int and cache_created >= 0 else None,
                "context_tokens": context_tokens,
            })

    async def _agent_execute(self, run_id, path, body):
        definitions = self.broker.definitions() if self.broker else []
        if self.config.provider == "openai-api":
            body["include"] = ["reasoning.encrypted_content"]
            body["tools"] = [{"type": "function", **tool, "strict": False} for tool in definitions]
            if isinstance(body["input"], str):
                body["input"] = [{"role": "user", "content": body["input"]}]
            body["max_output_tokens"] = self.budgets.max_output_tokens
        elif self.config.provider == "anthropic-api":
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in definitions]
            body["max_tokens"] = self.budgets.max_output_tokens
        else:
            body["tools"] = [{"type": "function", "function": tool} for tool in definitions]
            body["max_tokens"] = self.budgets.max_output_tokens
        total_tokens = 0
        for step in range(self.budgets.max_model_calls):
            attempt_id = f"{run_id}:executor:{step}"
            yield RunEvent(run_id=run_id, kind=EventKind.MODEL_CALL_STARTED, payload={
                "attempt_id": attempt_id, "phase": "executor", "provider": self.config.provider,
                "model": self.config.model})
            result = None
            async for kind, value in stream_request(self.config, path, body):
                if kind == "text":
                    yield RunEvent(run_id=run_id, kind=EventKind.MODEL_STREAM, payload={"text": value})
                else:
                    result = value
            if result is None:
                raise RuntimeError("Provider stream ended without a completed response.")
            usage = result.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            input_key = next((key for key in ("input_tokens", "prompt_tokens") if key in usage), None)
            output_key = next((key for key in ("output_tokens", "completion_tokens") if key in usage), None)
            if input_key is None or output_key is None:
                yield RunEvent(run_id=run_id, kind=EventKind.USAGE, payload={
                    "input_tokens": None, "output_tokens": None, "reported": False,
                    "attempt_id": attempt_id, "phase": "executor", "provider": self.config.provider,
                    "model": self.config.model, "source": "unavailable",
                })
                raise RuntimeError(
                    "Provider omitted input/output usage; the token budget cannot be enforced, "
                    "so no further model or tool calls will run."
                )
            input_tokens = usage[input_key]
            output_tokens = usage[output_key]
            cache_read = usage.get("cache_read_input_tokens", 0) if self.config.provider == "anthropic-api" else usage.get("input_tokens_details", {}).get("cached_tokens", 0)
            cache_created = usage.get("cache_creation_input_tokens", 0) if self.config.provider == "anthropic-api" else 0
            if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (input_tokens, output_tokens, cache_read, cache_created)):
                raise RuntimeError("Provider returned invalid usage accounting.")
            if self.config.provider == "anthropic-api":
                input_tokens += cache_read + cache_created
            total_tokens += input_tokens + output_tokens
            yield RunEvent(run_id=run_id, kind=EventKind.USAGE, payload={
                "input_tokens": input_tokens if any(key in usage for key in ("input_tokens", "prompt_tokens")) else None,
                "output_tokens": output_tokens if any(key in usage for key in ("output_tokens", "completion_tokens")) else None,
                "reported": bool(usage), "step": step,
                "attempt_id": attempt_id, "phase": "executor", "provider": self.config.provider,
                "model": self.config.model,
                "cached_input_tokens": cache_read if "cache_read_input_tokens" in usage or "cached_tokens" in (usage.get("input_tokens_details") or {}) else None,
                "cache_creation_input_tokens": cache_created if "cache_creation_input_tokens" in usage else None,
                "input_includes_cache": True,
            })
            if total_tokens > self.budgets.max_total_tokens:
                raise RuntimeError("Total token budget exceeded; no further calls will be made.")
            if self.config.provider == "openai-api":
                content = result.get("output", [])
                calls = [(item["call_id"], item["name"], json.loads(item["arguments"]))
                         for item in content if item.get("type") == "function_call"]
                body["input"].extend(content)  # Preserve reasoning/tool items unchanged.
            elif self.config.provider == "anthropic-api":
                content = result.get("content", [])
                calls = [(item["id"], item["name"], item["input"])
                         for item in content if item.get("type") == "tool_use"]
                body["messages"].append({"role": "assistant", "content": content})
            else:
                choices = result.get("choices", [])
                content = choices[0]["message"] if choices else {}
                calls = [(item["id"], item["function"]["name"], json.loads(item["function"]["arguments"]))
                         for item in content.get("tool_calls", [])]
                body["messages"].append({"role": "assistant", **content})
            if not calls:
                return
            if total_tokens >= self.budgets.max_total_tokens:
                raise RuntimeError("Total token budget exhausted; requested tools were not executed.")
            results = []
            for call_id, name, arguments in calls:
                # Limit first; malformed arguments or denied calls do not trigger
                # model-driven retries of potentially completed external effects.
                if self.broker is None:
                    raise PermissionError("Provider requested a tool, but native tools are disabled.")
                output = await self.broker.invoke(name, arguments, call_id)
                if self.config.provider == "openai-api":
                    body["input"].append({"type": "function_call_output", "call_id": call_id, "output": output})
                elif self.config.provider == "anthropic-api":
                    results.append({"type": "tool_result", "tool_use_id": call_id, "content": output})
                else:
                    body["messages"].append({"role": "tool", "tool_call_id": call_id, "content": output})
            if results:
                body["messages"].append({"role": "user", "content": results})
        raise RuntimeError("Model-call budget exhausted; run stopped without claiming completion.")


async def stream_request(config, path, body):
    """Cancellable SSE transport with bounded event size and JSON fallback."""
    base, headers = connection(config)
    provider = config.provider
    request = {**body, "stream": True}
    if provider == "local":
        request["stream_options"] = {"include_usage": True}
    result = None
    completed = False
    blocks, local_tools = {}, {}
    local_text = ""
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=60) as client:
        async with client.stream("POST", base + path, headers=headers, json=request) as response:
            if response.status_code != 200:
                raise RuntimeError(f"{provider} returned HTTP {response.status_code}.")
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4_000_000:
                        raise RuntimeError("API response exceeded size limit.")
                result = json.loads(raw)
                require_complete_response(provider, result)
                if provider == "openai-api":
                    text = "".join(p.get("text", "") for item in result.get("output", []) for p in item.get("content", []) if p.get("type") == "output_text")
                elif provider == "anthropic-api":
                    text = "".join(p.get("text", "") for p in result.get("content", []) if p.get("type") == "text")
                else:
                    choices = result.get("choices", [])
                    text = choices[0].get("message", {}).get("content") or "" if choices else ""
                if text:
                    yield "text", text
                yield "result", result
                return
            async for line in bounded_stream_lines(response):
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                data = json.loads(line[5:])
                kind = data.get("type")
                if kind in {"error", "response.failed", "response.incomplete"}:
                    raise RuntimeError("Provider stream failed or ended incompletely.")
                if provider == "openai-api":
                    if kind == "response.output_text.delta":
                        yield "text", data["delta"]
                    elif kind == "response.completed":
                        result = data["response"]
                        require_complete_response(provider, result)
                        completed = True
                elif provider == "anthropic-api":
                    if kind == "message_start":
                        result = data["message"]
                    elif kind == "content_block_start":
                        blocks[data["index"]] = data["content_block"]
                    elif kind == "content_block_delta":
                        block, delta = blocks[data["index"]], data["delta"]
                        if delta["type"] == "text_delta":
                            block["text"] = block.get("text", "") + delta["text"]
                            yield "text", delta["text"]
                        elif delta["type"] == "input_json_delta":
                            block["_json"] = block.get("_json", "") + delta["partial_json"]
                        elif delta["type"] == "thinking_delta":
                            block["thinking"] = block.get("thinking", "") + delta["thinking"]
                        elif delta["type"] == "signature_delta":
                            block["signature"] = block.get("signature", "") + delta["signature"]
                    elif kind == "message_delta" and result is not None:
                        result.update(data.get("delta", {}))
                        result["usage"] = {**result.get("usage", {}), **data.get("usage", {})}
                    elif kind == "message_stop" and result is not None:
                        if result.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence"}:
                            raise RuntimeError("Anthropic response stopped incompletely.")
                        completed = True
                        for block in blocks.values():
                            if "_json" in block:
                                block["input"] = json.loads(block.pop("_json"))
                        result["content"] = [blocks[key] for key in sorted(blocks)]
                else:
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        if delta.get("content"):
                            local_text += delta["content"]
                            yield "text", delta["content"]
                        for tool in delta.get("tool_calls", []):
                            entry = local_tools.setdefault(tool["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            entry["id"] = tool.get("id") or entry["id"]
                            for key in ("name", "arguments"):
                                entry["function"][key] += tool.get("function", {}).get(key, "")
                        if choices[0].get("finish_reason"):
                            if choices[0]["finish_reason"] not in {"stop", "tool_calls"}:
                                raise RuntimeError("Local response stopped incompletely.")
                            result = {"choices": [{"finish_reason": choices[0]["finish_reason"],
                                "message": {"content": local_text, "tool_calls": list(local_tools.values())}}]}
                            completed = True
                    if data.get("usage") and result is not None:
                        result["usage"] = data["usage"]
    if result is not None and completed:
        yield "result", result


async def bounded_stream_lines(response):
    """Bound buffering before line decoding, including malicious unterminated lines."""
    pending = bytearray()
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > 8_000_000:
            raise RuntimeError("API stream exceeded aggregate size limit.")
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, remainder = pending.partition(b"\n")
            if len(line) > 1_000_000:
                raise RuntimeError("API stream event exceeded size limit.")
            pending = bytearray(remainder)
            yield line.rstrip(b"\r").decode("utf-8")
        if len(pending) > 1_000_000:
            raise RuntimeError("API stream event exceeded size limit.")
    if pending:
        yield pending.decode("utf-8")
