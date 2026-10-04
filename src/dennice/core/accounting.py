"""Auditable usage ledger; unavailable telemetry is never fabricated zero."""

import math

from dennice.core.models import EventKind


def _count(value):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Usage token counts must be nonnegative integers or unknown.")
    return value


def summarize_usage(events, *, router=None, executor=None):
    records = {}
    for index, event in enumerate(events):
        payload = event.payload
        if event.kind == EventKind.ROUTING_STARTED:
            attempt = "router"
            records[attempt] = {"phase": "router", "provider": (router or {}).get("provider", payload.get("router", "unknown")),
                "model": (router or {}).get("model"), "source": "unavailable", "input_tokens": None,
                "output_tokens": None, "cached_input_tokens": None, "reported": False,
                "cost_amount": None, "currency": None}
            if payload.get("router") == "rule":
                records[attempt].update(source="no_model_call", input_tokens=0, output_tokens=0, cost_amount=0)
        elif event.kind == EventKind.MODEL_CALL_STARTED:
            attempt = payload["attempt_id"]
            records[attempt] = {"phase": payload.get("phase", "executor"), "provider": payload.get("provider"),
                "model": payload.get("model"), "source": "awaiting_provider_telemetry",
                "reported": False, "input_tokens": None, "output_tokens": None,
                "cached_input_tokens": None, "cost_amount": None, "currency": None}
        elif event.kind == EventKind.USAGE:
            phase = payload.get("phase", "executor")
            # Cumulative snapshots replace, never sum, the same attempt ID.
            attempt = payload.get("attempt_id") or ("router" if phase == "router" else f"{phase}_cumulative" if payload.get("cumulative") else f"usage_{index}")
            reported = bool(payload.get("reported"))
            counts = {key: _count(payload.get(key)) if reported else None
                      for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens")}
            known_input = counts["input_tokens"]
            if payload.get("input_includes_cache") is False:
                components = [counts[key] for key in ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens")]
                known = [value for value in components if value is not None]
                known_input = sum(known) if known else None
                counts["input_tokens"] = sum(components) if all(value is not None for value in components) else None
            cost = payload.get("cost_amount") if reported else None
            if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost < 0):
                raise ValueError("Reported cost must be finite and nonnegative.")
            records[attempt] = {"phase": phase, "provider": payload.get("provider", (executor or {}).get("provider")),
                "model": payload.get("model", (executor or {}).get("model")), "source": payload.get("source", "provider_reported" if reported else "unavailable"),
                "reported": reported, **counts, "cost_amount": cost,
                "known_input_tokens": known_input,
                "input_includes_cache": True,
                "scope": payload.get("scope", "provider response usage"),
                "currency": payload.get("currency") if cost is not None else None}
    if any(event.kind == EventKind.EXECUTION_STARTED for event in events) and not any(item["phase"] == "executor" for item in records.values()):
        records["executor"] = {"phase": "executor", "provider": (executor or {}).get("provider"), "model": (executor or {}).get("model"),
            "source": "unavailable", "reported": False, "input_tokens": None, "output_tokens": None,
            "cached_input_tokens": None, "cost_amount": None, "currency": None}
    rows = [{"attempt_id": key, **row} for key, row in records.items()]
    def total(key):
        return sum(row[key] for row in rows) if rows and all(row[key] is not None for row in rows) else None
    def subtotal(key):
        values = [row.get("known_input_tokens", row[key]) if key == "input_tokens" else row[key] for row in rows]
        values = [value for value in values if value is not None]
        return sum(values) if values else None
    currencies = {row["currency"] for row in rows if row["cost_amount"] is not None and row["source"] != "no_model_call"}
    cost_complete = bool(rows) and all(row["cost_amount"] is not None for row in rows) and len(currencies) <= 1 and None not in currencies
    spans = {}
    elapsed = {}
    for event in events:
        if event.kind in {EventKind.ROUTING_STARTED, EventKind.TOOL_STARTED, EventKind.HOOK_STARTED}:
            key = event.payload.get("operation_id") or event.payload.get("effect_id") or event.payload.get("call_id") or event.kind.value
            spans[key] = (event.timestamp, "router" if event.kind == EventKind.ROUTING_STARTED else "hooks" if event.kind == EventKind.HOOK_STARTED else "tools")
        elif event.kind in {EventKind.ROUTING_COMPLETED, EventKind.TOOL_COMPLETED, EventKind.HOOK_COMPLETED}:
            key = event.payload.get("operation_id") or event.payload.get("effect_id") or event.payload.get("call_id") or EventKind.ROUTING_STARTED.value
            start = spans.pop(key, None)
            if start:
                elapsed[start[1]] = elapsed.get(start[1], 0) + max(0, (event.timestamp - start[0]).total_seconds())
    return {"records": rows, "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "known_input_tokens_subtotal": subtotal("input_tokens"), "known_output_tokens_subtotal": subtotal("output_tokens"),
        "unknown_attempts": sum(row["input_tokens"] is None or row["output_tokens"] is None for row in rows),
        "cost_amount": total("cost_amount") if cost_complete else None,
        "currency": next(iter(currencies), None) if cost_complete else None,
        "cost_complete": cost_complete,
        "observed_elapsed_seconds": elapsed, "incomplete_spans": len(spans),
        "scope": "all observed model attempts; tools/hooks/verification elapsed time is separate; unavailable token/cost telemetry remains unknown"}


def phase_tokens(accounting, phase="executor"):
    rows = [row for row in accounting["records"] if row["phase"] == phase]
    return tuple(sum(row[key] for row in rows) if rows and all(row[key] is not None for row in rows) else None
                 for key in ("input_tokens", "output_tokens"))
