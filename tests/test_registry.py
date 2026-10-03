from dennice.cognition.registry import PackagePolicyRegistry
from dennice.cognition.taxonomy import CognitiveDemand


def test_package_registry_loads_versioned_policy() -> None:
    policy = PackagePolicyRegistry().resolve(CognitiveDemand.DECOMPOSITION)
    assert policy.id == "cartesian-v1"
    assert policy.cognitive_demand == CognitiveDemand.DECOMPOSITION
    assert "Break the problem" in policy.instructions
