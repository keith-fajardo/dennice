"""Offline outcome reporting: never starts models, tools, or graders.

Completion evidence must be supplied independently. Missing telemetry is
unknown, not a zero-cost or successful run. Rates are caller-supplied snapshots.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from dennice.core.config import DenniceConfig
from dennice.core.models import RunTrace


@dataclass(frozen=True)
class TokenRates:
    input_per_million: float
    output_per_million: float

    def __post_init__(self):
        if any(not math.isfinite(value) or value < 0 for value in
               (self.input_per_million, self.output_per_million)):
            raise ValueError("Rates must be finite and nonnegative")


def ablation_configs(config: DenniceConfig, *, strong_model: str, cheap_model: str):
    """Comparable same-provider settings; returns plans without executing them.

    Each arm uses the same task corpus, budgets, tools and verification.
    Caller must choose supported model IDs and approved routed candidates.
    """
    arms = {}
    for name, model, route, pa in (
        ("user_selected", config.executor.model, "fixed", True),
        ("fixed_strong", strong_model, "fixed", True),
        ("fixed_cheap", cheap_model, "fixed", True),
        ("routed", config.executor.model, "auto", True),
        ("routed_without_pa", config.executor.model, "auto", False),
        ("fixed_without_pa", config.executor.model, "fixed", False),
    ):
        candidate = config.model_copy(deep=True)
        candidate.executor.model = model
        candidate.routing.mode = route
        candidate.routing.pa_enabled = pa
        candidate.routing.model_pinned = route == "fixed"
        # Keep effort identical across arms: isolate model and PA effects.
        candidate.routing.effort_pinned = True
        arms[name] = candidate
    return arms


def outcome_record(trace: RunTrace, *, arm: str, item_id: str,
                   rates: dict[tuple[str, str], TokenRates] | None = None,
                   critical_failure: bool | None = None):
    verification = trace.verification or {}
    verified = None
    if verification.get("independent_checks") and isinstance(verification.get("passed"), bool):
        verified = trace.status == "completed" and verification["passed"]
    duration = None
    if trace.completed_at:
        duration = max(0.0, (trace.completed_at - trace.started_at).total_seconds())
    executor = trace.config.get("executor", {})
    provider = executor.get("provider", "unknown")
    model = trace.route_plan.effective_model if trace.route_plan else executor.get("model", "unknown")
    counts = (trace.result.input_tokens, trace.result.output_tokens) if trace.result else (None, None)
    usage_known = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts)
    rate = (rates or {}).get((provider, model))
    # Executor usage excludes router, cached-token discounts and extra services.
    # Report its scoped cost, never claim this equals the end-to-end invoice.
    cost = ((counts[0] * rate.input_per_million + counts[1] * rate.output_per_million) / 1_000_000
            if usage_known and rate else None)
    return {"item_id": item_id, "arm": arm, "run_id": trace.run_id,
            "provider": provider, "model": model, "status": trace.status,
            "verified_completion": verified, "critical_failure": critical_failure,
            "latency_seconds": duration, "usage_known": usage_known,
            "input_tokens": counts[0], "output_tokens": counts[1],
            "executor_token_cost": cost,
            "cost_scope": "executor token-rate estimate without cache discounts; excludes router and other services"}


def summarize_outcomes(records: Iterable[dict]):
    records = list(records)
    arms = {}
    for name in sorted({record["arm"] for record in records}):
        rows = [record for record in records if record["arm"] == name]
        checked = [row["verified_completion"] for row in rows if row["verified_completion"] is not None]
        critical = [row["critical_failure"] for row in rows if row["critical_failure"] is not None]
        latencies = sorted(row["latency_seconds"] for row in rows if row["latency_seconds"] is not None)
        costs = [row["executor_token_cost"] for row in rows if row["executor_token_cost"] is not None]
        def percentile(fraction):
            return latencies[max(0, math.ceil(fraction * len(latencies)) - 1)] if latencies else None
        arms[name] = {"runs": len(rows), "verified_completion_rate": sum(checked) / len(checked) if checked else None,
                      "completion_unassessed": len(rows) - len(checked),
                      "critical_failures": sum(critical), "critical_unassessed": len(rows) - len(critical),
                      "usage_unknown": sum(not row["usage_known"] for row in rows),
                      "cost_unknown": len(rows) - len(costs),
                      "known_executor_token_cost_subtotal": sum(costs) if costs else None,
                      "latency_p50": percentile(.5), "latency_p95": percentile(.95)}
    item_counts = [Counter(row["item_id"] for row in records if row["arm"] == arm) for arm in arms]
    # Compare replicate counts as well as IDs. Set comparison can incorrectly
    # call runs paired when one arm is missing a repeat for an item.
    return {"arms": arms, "paired_item_sets": not item_counts or all(
                counts == item_counts[0] for counts in item_counts),
            "warning": "No quality/cost improvement is established by this report alone. Unassessed cases and router/service cost remain unknown."}


def main():
    """Export records from explicitly named saved trace JSON files to stdout."""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Offline outcome summary; no inference or tools")
    parser.add_argument("traces", nargs="+", help="Saved RunTrace JSON paths")
    parser.add_argument("--arm", required=True, help="Experiment arm label for these traces")
    args = parser.parse_args()
    rows = []
    for filename in args.traces:
        trace = RunTrace.model_validate_json(Path(filename).read_text(encoding="utf-8"))
        rows.append(outcome_record(trace, arm=args.arm, item_id=trace.task.id))
    print(json.dumps({"records": rows, "summary": summarize_outcomes(rows)}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
