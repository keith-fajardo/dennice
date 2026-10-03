from __future__ import annotations

from importlib.resources import files

from dennice.cognition.taxonomy import COGNITIVE_POLICY_MAP, CognitiveDemand
from dennice.core.models import ReasoningPolicy


_POLICY_METADATA = {
    CognitiveDemand.CRITICAL_INQUIRY: ("socratic-v1", "Socratic questioning"),
    CognitiveDemand.EMPIRICAL_INDUCTION: ("humean-v1", "Empirical investigation"),
    CognitiveDemand.DECOMPOSITION: ("cartesian-v1", "Systematic decomposition"),
    CognitiveDemand.CONSTRAINT_REASONING: ("kantian-v1", "Constraint preservation"),
    CognitiveDemand.CAUSAL_CATEGORIZATION: ("aristotelian-v1", "Causal categorization"),
    CognitiveDemand.CONTRADICTION_RESOLUTION: ("hegelian-v1", "Contradiction resolution"),
    CognitiveDemand.ABSTRACTION: ("platonic-v1", "Conceptual abstraction"),
}


class PackagePolicyRegistry:
    """Registry backed by package resources; future registries may layer project overrides."""

    def __init__(self, mapping: dict[CognitiveDemand, str] | None = None) -> None:
        self._mapping = mapping or COGNITIVE_POLICY_MAP
        self._policies = self._load_defaults()

    def _load_defaults(self) -> dict[str, ReasoningPolicy]:
        result: dict[str, ReasoningPolicy] = {}
        policy_dir = files("dennice.cognition.policies")
        for demand, (policy_id, name) in _POLICY_METADATA.items():
            body = policy_dir.joinpath(f"{demand.value}.md").read_text(encoding="utf-8").strip()
            result[policy_id] = ReasoningPolicy(
                id=policy_id,
                name=name,
                cognitive_demand=demand,
                description=body.splitlines()[0],
                instructions=body,
                version="v1",
                source="package:dennice.cognition.policies",
            )
        return result

    def resolve(self, demand: CognitiveDemand) -> ReasoningPolicy:
        return self._policies[self._mapping[demand]]

    def resolve_many(self, demands: list[CognitiveDemand]) -> list[ReasoningPolicy]:
        return [self.resolve(demand) for demand in demands]
