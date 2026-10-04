from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class PermissionMode(str, Enum):
    """Explicit execution authority; adapters map only supported values."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    PLAN = "plan"


class ProviderConfig(BaseModel):
    provider: str
    model: str
    reasoning_effort: ReasoningEffort | None = None
    permission_mode: PermissionMode | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    claude_cli_auth: Literal["subscription", "api_key", "provider_default"] | None = None
    codex_cli_auth: Literal["chatgpt", "api_key", "provider_default"] | None = None

    @model_validator(mode="after")
    def validate_cli_auth(self):
        if self.claude_cli_auth is not None and self.provider != "claude":
            raise ValueError("claude_cli_auth applies only to the Claude Code CLI provider.")
        if self.codex_cli_auth is not None and self.provider != "codex":
            raise ValueError("codex_cli_auth applies only to the Codex CLI provider.")
        return self


class ModelCandidate(BaseModel):
    model: str = Field(min_length=1)
    tier: Literal["lightweight", "balanced", "strong"]
    efforts: list[ReasoningEffort] = Field(default_factory=list)
    vision: bool = False
    tools: bool = False
    context_tokens: int = Field(default=8192, gt=0)
    enabled: bool = True

    @field_validator("model")
    @classmethod
    def model_id_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model ID must not be blank.")
        return value


class RoutingConfig(BaseModel):
    primary_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    supporting_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    mode: Literal["fixed", "shadow", "auto"] = "fixed"
    model_pinned: bool = True
    effort_pinned: bool = True
    model_pool: dict[str, list[ModelCandidate]] = Field(default_factory=dict)
    pa_enabled: bool = True
    max_supporting_policies: int = Field(default=2, ge=0, le=6)

    @model_validator(mode="after")
    def validate_model_pool(self):
        if self.supporting_threshold > self.primary_threshold:
            raise ValueError("supporting_threshold must not exceed primary_threshold.")
        providers = {"codex", "claude", "copilot", "openai-api", "anthropic-api", "local"}
        unknown = sorted(set(self.model_pool) - providers)
        if unknown:
            raise ValueError(
                "Unknown executor provider in routing.model_pool: "
                f"{', '.join(unknown)}."
            )
        for provider, candidates in self.model_pool.items():
            seen = set()
            duplicates = set()
            for candidate in candidates:
                if candidate.model in seen:
                    duplicates.add(candidate.model)
                seen.add(candidate.model)
            if duplicates:
                raise ValueError(
                    f"Duplicate model IDs in routing.model_pool.{provider}: "
                    f"{', '.join(sorted(duplicates))}."
                )
        return self


class PrivacyConfig(BaseModel):
    local_only: bool = False
    router_history: bool = False


class BudgetConfig(BaseModel):
    max_seconds: float = Field(default=300, gt=0, le=3600)
    max_model_calls: int = Field(default=8, ge=1, le=100)
    max_tool_calls: int = Field(default=24, ge=0, le=500)
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    max_total_tokens: int = Field(default=100000, ge=1)


class ToolsConfig(BaseModel):
    enabled: bool = False
    root: str = "."
    output_limit: int = Field(default=24000, ge=100, le=100000)
    timeout_seconds: float = Field(default=30, gt=0, le=120)


class HookConfig(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=32)
    event: Literal["before_route", "after_route", "before_execution", "before_tool",
                   "after_tool", "before_verification", "turn_complete"]
    command: list[str] = Field(min_length=1)
    enabled: bool = False
    required: bool = True
    timeout_seconds: float = Field(default=5, gt=0, le=30)
    include_arguments: bool = False


class MCPServerConfig(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=32)
    transport: Literal["stdio", "http"] = "stdio"
    command: list[str] = Field(default_factory=list)
    url: str | None = None
    api_key_env: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
    approved_tools: list[str] = Field(default_factory=list)
    approved_resources: list[str] = Field(default_factory=list)
    approved_prompts: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class VerificationConfig(BaseModel):
    required_files: list[str] = Field(default_factory=list)
    commands: list[list[str]] = Field(default_factory=list)
    require_nonempty: bool = True


class BenchmarkConfig(BaseModel):
    path: str = "./benchmarks"


class RunsConfig(BaseModel):
    path: str = "./.dennice/runs"


class JevConfig(BaseModel):
    """Reference to a Jev credential without serializing the credential itself."""

    api_key_env: str = "JEV_API_KEY"
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-latest"
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=60.0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_default(cls, value: object) -> object:
        """Repair the previously shipped third-party endpoint/model pairing."""
        if isinstance(value, dict) and (
            value.get("endpoint") == "https://thejevai.com/v1/systemone"
            and value.get("model", "typesafe/jev-1.13") == "typesafe/jev-1.13"
        ):
            return {**value, "endpoint": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"}
        return value


class OpenJevConfig(BaseModel):
    """Connection settings for a local OpenJev-compatible System One server."""

    endpoint: str = "http://127.0.0.1:3000/v1/systemone"
    model: str = "openjev"
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)


class DenniceConfig(BaseModel):
    version: int = 1
    router: ProviderConfig = Field(default_factory=lambda: ProviderConfig(provider="rule", model="v1"))
    executor: ProviderConfig = Field(default_factory=lambda: ProviderConfig(provider="mock", model="v1"))
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    runs: RunsConfig = Field(default_factory=RunsConfig)
    jev: JevConfig = Field(default_factory=JevConfig)
    openjev: OpenJevConfig = Field(default_factory=OpenJevConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    hooks: list[HookConfig] = Field(default_factory=list)
    mcp: list[MCPServerConfig] = Field(default_factory=list)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DenniceConfig":
        config_path = Path(path) if path else Path("dennice.yaml")
        if not config_path.exists():
            return cls()
        with config_path.open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        return cls.model_validate(raw)

    def save(self, path: str | Path | None = None) -> Path:
        """Persist the complete configuration in a readable project YAML file."""
        config_path = Path(path) if path else Path("dennice.yaml")
        config_path.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        return config_path
