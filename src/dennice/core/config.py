from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    provider: str
    model: str


class RoutingConfig(BaseModel):
    primary_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    supporting_threshold: float = Field(default=0.55, ge=0.0, le=1.0)


class BenchmarkConfig(BaseModel):
    path: str = "./benchmarks"


class RunsConfig(BaseModel):
    path: str = "./.dennice/runs"


class DenniceConfig(BaseModel):
    version: int = 1
    router: ProviderConfig = Field(default_factory=lambda: ProviderConfig(provider="rule", model="v1"))
    executor: ProviderConfig = Field(default_factory=lambda: ProviderConfig(provider="mock", model="v1"))
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    runs: RunsConfig = Field(default_factory=RunsConfig)

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
