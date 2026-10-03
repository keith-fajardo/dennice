from enum import Enum


class CognitiveDemand(str, Enum):
    """Stable taxonomy of task reasoning requirements."""

    CRITICAL_INQUIRY = "critical_inquiry"
    EMPIRICAL_INDUCTION = "empirical_induction"
    DECOMPOSITION = "decomposition"
    CONSTRAINT_REASONING = "constraint_reasoning"
    CAUSAL_CATEGORIZATION = "causal_categorization"
    CONTRADICTION_RESOLUTION = "contradiction_resolution"
    ABSTRACTION = "abstraction"


# The policy implementation is deliberately separate from the taxonomy.
COGNITIVE_POLICY_MAP: dict[CognitiveDemand, str] = {
    CognitiveDemand.CRITICAL_INQUIRY: "socratic-v1",
    CognitiveDemand.EMPIRICAL_INDUCTION: "humean-v1",
    CognitiveDemand.DECOMPOSITION: "cartesian-v1",
    CognitiveDemand.CONSTRAINT_REASONING: "kantian-v1",
    CognitiveDemand.CAUSAL_CATEGORIZATION: "aristotelian-v1",
    CognitiveDemand.CONTRADICTION_RESOLUTION: "hegelian-v1",
    CognitiveDemand.ABSTRACTION: "platonic-v1",
}
