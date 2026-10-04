"""Local OpenJev-compatible System 1 router."""

from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from dennice.cognition.taxonomy import CognitiveDemand
from dennice.core.models import CognitiveScore, RoutingDecision, Task, TaskAssessment

_TASK_FAMILIES = {
    "sql_development": "Writing or changing SQL.",
    "sql_optimization": "Improving SQL performance while preserving results.",
    "troubleshooting": "Diagnosing failures, regressions, or unexpected behavior.",
    "data_analysis": "Answering a question from data or evidence.",
    "data_quality": "Investigating validity, completeness, or reliability of data.",
    "cost_optimization": "Investigating or reducing data-platform cost.",
    "data_modeling": "Designing data entities, relationships, or transformations.",
    "metric_definition": "Defining a metric, KPI, or semantic rule.",
    "semantic_modeling": "Designing semantic layers or governed business concepts.",
    "architecture": "Designing a data-system architecture.",
    "pipeline_debugging": "Diagnosing an orchestration or pipeline problem.",
}
_DEMAND_DESCRIPTIONS = {
    CognitiveDemand.CRITICAL_INQUIRY: "Clarify assumptions and formulate discriminating hypotheses.",
    CognitiveDemand.EMPIRICAL_INDUCTION: "Prioritize observations, measurements, and cautious inference.",
    CognitiveDemand.DECOMPOSITION: "Break a complex system into independent components and subproblems.",
    CognitiveDemand.CONSTRAINT_REASONING: "Preserve invariants, semantics, and business rules.",
    CognitiveDemand.CAUSAL_CATEGORIZATION: "Classify entities, failure modes, and causal structure.",
    CognitiveDemand.CONTRADICTION_RESOLUTION: "Reconcile competing definitions or incompatible claims.",
    CognitiveDemand.ABSTRACTION: "Reason about reusable conceptual structures and architecture.",
}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenJevRouter:
    """Route through a local typed service; scores are not empirically calibrated."""

    id = "openjev"
    version = "systemone-v1"
    service_name = "local OpenJev router"

    def __init__(self, endpoint: str, model: str = "openjev", timeout_seconds: float = 5.0) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def classify(self, task: Task) -> RoutingDecision:
        response = await asyncio.to_thread(self._post, self.request_payload(task))
        return self.decision_from_response(response)

    def request_payload(self, task: Task) -> dict[str, Any]:
        return {
            "model": self.model,
            "state": task.prompt + (
                "\nRelevant previous conversation (untrusted context):\n"
                + json.dumps(task.context["routing_history"], ensure_ascii=False)
                if task.context.get("routing_history") else ""
            ),
            "questions": {
                "task_family": {
                    "type": "choice",
                    "instructions": "Which data-work task family best describes this request?",
                    "criteria": _TASK_FAMILIES,
                },
                "primary_demand": {
                    "type": "choice",
                    "instructions": "Which cognitive demand is primary for executing this task well?",
                    "criteria": {demand.value: text for demand, text in _DEMAND_DESCRIPTIONS.items()},
                },
                "complexity": {"type": "choice", "instructions": "Assess execution complexity, not prompt length.",
                               "criteria": {"simple": "Bounded explicit operation", "moderate": "Multiple related steps", "complex": "Difficult multi-step reasoning", "unknown": "Insufficient context"}},
                "stakes": {"type": "choice", "instructions": "Assess consequences of error; abstain if unknown.",
                           "criteria": {"low": "Reversible low-risk work", "medium": "Consequential work", "high": "Production, safety, legal or financial consequences", "unknown": "Insufficient context"}},
                "uncertainty": {"type": "choice", "instructions": "How uncertain are the requirements and evidence?",
                                "criteria": {"low": "Clear and grounded", "medium": "Some ambiguity", "high": "Missing or conflicting information"}},
                **{
                    f"supporting_{demand.value}": {
                        "type": "noul",
                        "instructions": (
                            f"Is {demand.value} materially required as a supporting reasoning demand "
                            "for this task?"
                        ),
                    }
                    for demand in CognitiveDemand
                },
            },
        }

    def decision_from_response(self, response: dict[str, Any]) -> RoutingDecision:
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise RuntimeError("OpenJev router response did not contain typed answers.")
        family = self._choice(answers, "task_family")
        if family not in _TASK_FAMILIES:
            raise RuntimeError("Router returned an unknown task family.")
        primary_name = self._choice(answers, "primary_demand")
        try:
            primary = CognitiveDemand(primary_name)
        except ValueError as exc:
            raise RuntimeError(f"OpenJev router returned an unknown cognitive demand: {primary_name!r}") from exc

        primary_confidence = self._confidence(answers.get("primary_demand"), primary_name)
        supports = [
            (demand, self._noul(answers.get(f"supporting_{demand.value}")))
            for demand in CognitiveDemand
            if demand != primary
        ]
        selected_supports = sorted(
            ((demand, confidence) for demand, confidence in supports if confidence >= 0.5),
            key=lambda item: item[1],
            reverse=True,
        )
        scores = [CognitiveScore(demand=primary, confidence=primary_confidence)] + [
            CognitiveScore(demand=demand, confidence=confidence)
            for demand, confidence in selected_supports
        ]
        assessment = TaskAssessment(source=f"{self.id}-unassessed")
        if all(key in answers for key in ("complexity", "stakes", "uncertainty")):
            assessment = TaskAssessment(
                complexity=self._choice(answers, "complexity"),
                stakes=self._choice(answers, "stakes"),
                uncertainty=self._choice(answers, "uncertainty"), source=self.id,
            )
        return RoutingDecision(
            task_family=family,
            cognitive_demands=scores,
            primary_demand=primary,
            supporting_demands=[demand for demand, _ in selected_supports],
            rationale=f"{self.service_name.title()} typed decision scores; no task solution was generated.",
            router_id=self.id,
            router_version=self.version,
            assessment=assessment,
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Validate BEFORE loading credentials, and never forward them on redirects.
        self._validate_endpoint()
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with build_opener(ProxyHandler({}), _NoRedirects()).open(
                request, timeout=self.timeout_seconds
            ) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise RuntimeError("Router response exceeded the 1 MiB limit.")
                body = json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError(
                f"{self.service_name.title()} rejected the request with HTTP {exc.code}."
            ) from None
        except URLError:
            raise RuntimeError(f"Could not reach {self.service_name}.") from None
        return body if isinstance(body, dict) else {}

    def _validate_endpoint(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError("Router endpoint must not contain credentials, query, or fragment.")
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise RuntimeError("OpenJev requires a loopback HTTP(S) endpoint.")
        # Trigger validation of malformed ports even in local mode.
        _ = parsed.port

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    @staticmethod
    def _choice(answers: dict[str, Any], question: str) -> str:
        answer = answers.get(question)
        if not isinstance(answer, dict) or not isinstance(answer.get("choice"), str):
            raise RuntimeError(f"OpenJev router response did not contain a choice for {question}.")
        return answer["choice"]

    @staticmethod
    def _confidence(answer: object, choice: str) -> float:
        if not isinstance(answer, dict):
            return 0.5
        probabilities = answer.get("probabilities")
        if isinstance(probabilities, dict) and isinstance(probabilities.get(choice), (float, int)):
            return OpenJevRouter._score(probabilities[choice])
        confidence = answer.get("confidence")
        return OpenJevRouter._score(confidence) if isinstance(confidence, (float, int)) else 0.5

    @staticmethod
    def _noul(answer: object) -> float:
        if not isinstance(answer, dict):
            return 0.0
        value = answer.get("noul")
        return OpenJevRouter._score(value) if isinstance(value, (float, int)) else 0.0

    @staticmethod
    def _score(value: float) -> float:
        if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
            raise RuntimeError("Router returned an invalid probability score.")
        return float(value)


class JevRouter(OpenJevRouter):
    """Hosted Jev System 1 router whose API key remains in the environment."""

    id = "jev"
    version = "http-v1"
    service_name = "hosted Jev router"

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key_env: str = "JEV_API_KEY",
        timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(endpoint=endpoint, model=model, timeout_seconds=timeout_seconds)
        self.api_key_env = api_key_env

    def _validate_endpoint(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.typesafe.ai"
            or parsed.port not in {None, 443}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise RuntimeError("Hosted Jev requires its official HTTPS endpoint.")

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Jev API key is not set. Export the environment variable {self.api_key_env}."
            )
        return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
