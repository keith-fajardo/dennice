from dennice.cognition.taxonomy import COGNITIVE_POLICY_MAP, CognitiveDemand


def test_taxonomy_has_seven_demands_and_independent_policy_map() -> None:
    assert len(CognitiveDemand) == 7
    assert set(COGNITIVE_POLICY_MAP) == set(CognitiveDemand)
    assert COGNITIVE_POLICY_MAP[CognitiveDemand.DECOMPOSITION] == "cartesian-v1"
