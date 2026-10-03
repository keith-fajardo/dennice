import pytest
from pydantic import ValidationError

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import CognitiveScore, RoutingDecision


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=1.01)


def test_primary_must_be_scored() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(
            task_family="x",
            cognitive_demands=[CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=0.8)],
            primary_demand=CognitiveDemand.EMPIRICAL_INDUCTION,
        )
