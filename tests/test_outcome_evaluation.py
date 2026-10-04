from datetime import datetime, timedelta, timezone

import pytest

from dennice.benchmark.outcomes import TokenRates, ablation_configs, outcome_record, summarize_outcomes
from dennice.core.config import DenniceConfig
from dennice.core.models import ExecutionResult, RunTrace, Task


def trace(**kwargs):
    start = datetime.now(timezone.utc)
    return RunTrace(run_id="test", started_at=start, completed_at=start + timedelta(seconds=2),
                    task=Task(prompt="test"), executor_id="test", config={"executor": {"provider": "local", "model": "example"}}, **kwargs)


def test_unknown_usage_and_nonempty_answer_are_not_success_or_zero_cost():
    row = outcome_record(trace(status="completed"), arm="selected", item_id="1")
    assert row["verified_completion"] is None
    assert row["executor_token_cost"] is None
    summary = summarize_outcomes([row])["arms"]["selected"]
    assert summary["completion_unassessed"] == summary["usage_unknown"] == summary["cost_unknown"] == 1
    assert summary["verified_completion_rate"] is None
    assert summary["known_executor_token_cost_subtotal"] is None


def test_report_independent_check_results_and_scoped_cost():
    result = ExecutionResult(output="done", executor_id="local", input_tokens=1000, output_tokens=500)
    row = outcome_record(trace(status="completed", result=result,
                               verification={"passed": True, "independent_checks": True}),
                         arm="routed", item_id="a", rates={("local", "example"): TokenRates(2, 4)}, critical_failure=False)
    assert row["verified_completion"] is True
    assert row["executor_token_cost"] == .004
    summary = summarize_outcomes([row])["arms"]["routed"]
    assert summary["latency_p95"] == 2
    assert summary["verified_completion_rate"] == 1
    row2 = dict(row, arm="fixed", item_id="b", critical_failure=True)
    assert not summarize_outcomes([row, row2])["paired_item_sets"]


def test_partial_or_cancelled_run_cannot_be_verified_complete():
    row = outcome_record(trace(status="cancelled", verification={"passed": True, "independent_checks": True}), arm="routed", item_id="x")
    assert row["verified_completion"] is False


def test_ablation_plans_are_isolated_and_do_not_execute():
    config = DenniceConfig()
    config.executor.model = "selected"
    arms = ablation_configs(config, strong_model="strong", cheap_model="cheap")
    assert arms["fixed_strong"].executor.model == "strong"
    assert arms["routed"].routing.mode == "auto"
    assert not arms["routed_without_pa"].routing.pa_enabled
    assert config.executor.model == "selected" and config.routing.mode == "fixed"
    assert all(arm.budgets == config.budgets and arm.verification == config.verification for arm in arms.values())
    with pytest.raises(ValueError):
        TokenRates(float("nan"), 1)
