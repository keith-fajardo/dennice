from dennice.cognition.registry import PackagePolicyRegistry
from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import Task
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
