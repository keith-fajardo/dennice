import asyncio

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import Task
from dennice.routing.rule import RuleRouter


def test_rule_router_routes_snowflake_investigation() -> None:
    result = asyncio.run(RuleRouter().classify(Task(prompt="Investigate why Snowflake credits increased yesterday.")))
    assert result.task_family == "cost_optimization"
    assert result.primary_demand == CognitiveDemand.EMPIRICAL_INDUCTION
    assert result.supporting_demands == [CognitiveDemand.DECOMPOSITION, CognitiveDemand.CRITICAL_INQUIRY]
