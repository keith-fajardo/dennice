"""Intentionally narrow, deterministic development router for the first vertical slice."""

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import CognitiveScore, RoutingDecision, Task


class RuleRouter:
    id = "rule"
    version = "v1"

    async def classify(self, task: Task) -> RoutingDecision:
        text = task.prompt.lower()
        if any(word in text for word in ("snowflake", "credit", "cost", "warehouse")):
            if any(word in text for word in ("optimize", "reduce", "rewrite")):
                return self._decision(
                    "cost_optimization",
                    [("decomposition", 0.92), ("constraint_reasoning", 0.87), ("empirical_induction", 0.66)],
                )
            return self._decision(
                "cost_optimization",
                [("empirical_induction", 0.95), ("decomposition", 0.84), ("critical_inquiry", 0.63)],
            )
        if any(word in text for word in ("slow", "failing", "error", "debug", "investigate", "why did")):
            return self._decision(
                "troubleshooting",
                [("critical_inquiry", 0.91), ("empirical_induction", 0.84), ("decomposition", 0.78)],
            )
        if any(word in text for word in ("metric", "definition", "semantic")):
            return self._decision(
                "metric_definition",
                [("causal_categorization", 0.87), ("constraint_reasoning", 0.81)],
            )
        if any(word in text for word in ("architecture", "design", "model")):
            return self._decision(
                "architecture",
                [("abstraction", 0.84), ("causal_categorization", 0.74), ("constraint_reasoning", 0.68)],
            )
        return self._decision(
            "data_analysis",
            [("critical_inquiry", 0.65), ("empirical_induction", 0.61)],
        )

    def _decision(self, family: str, scores: list[tuple[str, float]]) -> RoutingDecision:
        demands = [CognitiveDemand(demand) for demand, _ in scores]
        return RoutingDecision(
            task_family=family,
            cognitive_demands=[
                CognitiveScore(demand=CognitiveDemand(demand), confidence=confidence)
                for demand, confidence in scores
            ],
            primary_demand=demands[0],
            supporting_demands=demands[1:],
            rationale="Deterministic development routing; replace with a validated router for research use.",
            router_id=self.id,
            router_version=self.version,
        )
