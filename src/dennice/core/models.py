"""Provider-neutral domain models for the harness."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dennice.cognition.taxonomy import CognitiveDemand


class Task(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    prompt: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CognitiveScore(BaseModel):
    demand: CognitiveDemand
    confidence: float = Field(ge=0.0, le=1.0)


class RoutingDecision(BaseModel):
    task_family: str = Field(min_length=1)
    cognitive_demands: list[CognitiveScore] = Field(min_length=1)
    primary_demand: CognitiveDemand
    supporting_demands: list[CognitiveDemand] = Field(default_factory=list)
    rationale: str | None = None
    router_id: str = "unknown"
    router_version: str = "unknown"

    @model_validator(mode="after")
    def validate_demand_membership(self) -> "RoutingDecision":
        demands = [score.demand for score in self.cognitive_demands]
        if len(demands) != len(set(demands)):
            raise ValueError("cognitive_demands may not contain duplicate demands")
        if self.primary_demand not in demands:
            raise ValueError("primary_demand must appear in cognitive_demands")
        if self.primary_demand in self.supporting_demands:
            raise ValueError("primary_demand cannot also be supporting")
        if len(self.supporting_demands) != len(set(self.supporting_demands)):
            raise ValueError("supporting_demands may not contain duplicates")
        if any(demand not in demands for demand in self.supporting_demands):
            raise ValueError("supporting_demands must appear in cognitive_demands")
        return self


class ReasoningPolicy(BaseModel):
    id: str = Field(min_length=1)
    name: str
    cognitive_demand: CognitiveDemand
    description: str
    instructions: str
    version: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionRequest(BaseModel):
    task: Task
    system_instructions: str
    policies: list[ReasoningPolicy] = Field(default_factory=list)
    executor_id: str = "mock"


class EventKind(str, Enum):
    RUN_STARTED = "run_started"
    ROUTING_STARTED = "routing_started"
    ROUTING_COMPLETED = "routing_completed"
    POLICY_SELECTED = "policy_selected"
    EXECUTION_STARTED = "execution_started"
    MODEL_STREAM = "model_stream"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class RunEvent(BaseModel):
    run_id: str
    kind: EventKind
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    output: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int = 0


class RunTrace(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    schema_version: int = 1
    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    task: Task
    routing: RoutingDecision | None = None
    policies: list[ReasoningPolicy] = Field(default_factory=list)
    executor_id: str
    prompt_composer_version: str = "v1"
    config: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] | None = None
    events: list[RunEvent] = Field(default_factory=list)
    result: ExecutionResult | None = None
    error: str | None = None


class RunSummary(BaseModel):
    run_id: str
    started_at: datetime
    task_preview: str
    task_family: str | None = None


class BenchmarkMode(str, Enum):
    BASE = "base"
    ORACLE = "oracle"
    ROUTER = "router"
    SELF_ROUTE = "self-route"
    WRONG_POLICY = "wrong-policy"
