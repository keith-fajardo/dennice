from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.config import DenniceConfig, OpenJevConfig, ProviderConfig
from dennice.core.harness import Harness
from dennice.core.models import Task
from dennice.routing.factory import router_from_config
from dennice.routing.openjev import JevRouter, OpenJevRouter


def test_openjev_payload_uses_typed_system_one_questions() -> None:
    router = OpenJevRouter(endpoint="http://127.0.0.1:3000/v1/systemone", model="openjev-27b")
    payload = router.request_payload(Task(prompt="Why did Snowflake credit use rise?"))
    assert payload["model"] == "openjev-27b"
    assert payload["questions"]["task_family"]["type"] == "choice"
    assert payload["questions"]["primary_demand"]["type"] == "choice"
    assert payload["questions"]["supporting_decomposition"]["type"] == "noul"


def test_openjev_response_becomes_a_provider_neutral_routing_decision() -> None:
    router = OpenJevRouter(endpoint="http://127.0.0.1:3000/v1/systemone")
    decision = router.decision_from_response(
        {
            "answers": {
                "task_family": {"choice": "cost_optimization"},
                "primary_demand": {
                    "choice": "empirical_induction",
                    "probabilities": {"empirical_induction": 0.87},
                },
                "supporting_decomposition": {"noul": 0.82},
                "supporting_critical_inquiry": {"noul": 0.42},
            }
        }
    )
    assert decision.task_family == "cost_optimization"
    assert decision.primary_demand == CognitiveDemand.EMPIRICAL_INDUCTION
    assert decision.supporting_demands == [CognitiveDemand.DECOMPOSITION]
    assert decision.router_id == "openjev"


def test_harness_constructs_a_configured_local_openjev_router() -> None:
    config = DenniceConfig(
        router=ProviderConfig(provider="openjev", model="openjev"),
        openjev=OpenJevConfig(endpoint="http://127.0.0.1:3000/v1/systemone", model="openjev-27b"),
    )
    harness = Harness(config)
    assert isinstance(harness.router, OpenJevRouter)
    assert harness.router.model == "openjev-27b"
    assert isinstance(router_from_config(config.router, openjev=config.openjev), OpenJevRouter)


def test_harness_constructs_hosted_jev_router_without_serializing_a_secret(monkeypatch) -> None:
    monkeypatch.setenv("DENNICE_JEV_TEST_KEY", "not-in-config")
    config = DenniceConfig(
        router=ProviderConfig(provider="jev", model="configured"),
        jev={"api_key_env": "DENNICE_JEV_TEST_KEY", "model": "typesafe/jev-1.13"},
    )
    harness = Harness(config)
    assert isinstance(harness.router, JevRouter)
    assert harness.router._headers()["Authorization"] == "Bearer not-in-config"
    assert "not-in-config" not in str(config.model_dump())
