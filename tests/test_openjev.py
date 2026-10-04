from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.config import DenniceConfig, JevConfig, OpenJevConfig, ProviderConfig
from dennice.core.harness import Harness
from dennice.core.models import Task
from dennice.routing.factory import router_from_config
from dennice.routing.openjev import JevRouter, OpenJevRouter
import pytest


def test_openjev_payload_uses_typed_system_one_questions() -> None:
    router = OpenJevRouter(endpoint="http://127.0.0.1:3000/v1/systemone", model="openjev-27b")
    payload = router.request_payload(Task(prompt="Why did Snowflake credit use rise?"))
    assert payload["model"] == "openjev-27b"
    assert payload["questions"]["task_family"]["type"] == "choice"
    assert payload["questions"]["primary_demand"]["type"] == "choice"
    assert payload["questions"]["supporting_decomposition"]["type"] == "noul"


def test_legacy_jev_default_migrates_to_official_typesafe_endpoint() -> None:
    config = JevConfig.model_validate({
        "endpoint": "https://thejevai.com/v1/systemone",
        "model": "typesafe/jev-1.13",
        "api_key_env": "TYPESAFE_API_KEY",
    })
    assert config.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert config.model == "jev-latest"
    assert config.api_key_env == "TYPESAFE_API_KEY"


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


@pytest.mark.parametrize("endpoint", [
    "https://evil.example/v1/systemone", "http://api.typesafe.ai/v1/systemone",
    "https://api.typesafe.ai:444/v1/systemone", "https://key@api.typesafe.ai/v1/systemone",
    "https://api.typesafe.ai/v1/systemone?key=secret",
])
def test_hosted_endpoint_rejected_before_key_lookup(endpoint, monkeypatch):
    router = JevRouter(endpoint, "jev")
    monkeypatch.setattr(router, "_headers", lambda: pytest.fail("credentials loaded"))
    with pytest.raises(RuntimeError, match="official HTTPS"):
        router._post({})


def test_local_router_rejects_remote_endpoint():
    with pytest.raises(RuntimeError, match="loopback"):
        OpenJevRouter("http://remote.example/v1/systemone")._post({})


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_router_rejects_invalid_scores(score):
    with pytest.raises(RuntimeError, match="invalid probability"):
        OpenJevRouter._score(score)


def test_router_blocks_redirects_and_limits_response_size(monkeypatch):
    from dennice.routing import openjev
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            assert limit == 1_048_577
            return b"x" * limit
    class Opener:
        def open(self, request, timeout):
            return Response()
    def build(*handlers):
        redirect = next(h for h in handlers if isinstance(h, openjev._NoRedirects))
        assert redirect.redirect_request(None, None, 302, "", {}, "https://evil.example") is None
        return Opener()
    monkeypatch.setattr(openjev, "build_opener", build)
    with pytest.raises(RuntimeError, match="1 MiB"):
        OpenJevRouter("http://127.0.0.1/v1/systemone")._post({})
