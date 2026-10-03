from dennice.cognition.registry import PackagePolicyRegistry
from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import CognitiveScore, RoutingDecision, Task
from dennice.prompting.composer import DefaultPromptComposer


def test_package_registry_loads_versioned_policy() -> None:
    policy = PackagePolicyRegistry().resolve(CognitiveDemand.DECOMPOSITION)
    assert policy.id == "cartesian-v1"
    assert policy.cognitive_demand == CognitiveDemand.DECOMPOSITION
    assert "Break the problem" in policy.instructions


def test_prompt_composer_includes_explicit_conversation_history() -> None:
    request = DefaultPromptComposer().compose(
        Task(
            prompt="What should I test next?",
            context={
                "conversation_history": [
                    {"role": "user", "content": "Investigate warehouse spend."},
                    {"role": "assistant", "content": "Start with daily credit usage."},
                ]
            },
        ),
        None,
        [],
    )
    assert "CONVERSATION HISTORY" in request.system_instructions
    assert "USER: Investigate warehouse spend." in request.system_instructions


def test_prompt_composer_makes_routing_an_execution_contract() -> None:
    decision = RoutingDecision(
        task_family="cost_optimization",
        cognitive_demands=[
            CognitiveScore(demand=CognitiveDemand.EMPIRICAL_INDUCTION, confidence=0.87),
            CognitiveScore(demand=CognitiveDemand.DECOMPOSITION, confidence=0.74),
        ],
        primary_demand=CognitiveDemand.EMPIRICAL_INDUCTION,
        supporting_demands=[CognitiveDemand.DECOMPOSITION],
    )
    policy = PackagePolicyRegistry().resolve(CognitiveDemand.EMPIRICAL_INDUCTION)
    request = DefaultPromptComposer().compose(Task(prompt="Investigate credit spend."), decision, [policy])
    assert "COGNITIVE ROUTING CONTRACT" in request.system_instructions
    assert "Primary cognitive demand: empirical_induction" in request.system_instructions
    assert "Supporting cognitive demands: decomposition" in request.system_instructions
    assert "it is not a conclusion" in request.system_instructions
