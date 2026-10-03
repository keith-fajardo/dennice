"""Construction of configured, provider-specific System 2 executors."""

from dennice.core.config import ProviderConfig
from dennice.executors.base import Executor
from dennice.executors.codex import CodexExecutor
from dennice.executors.mock import MockExecutor


def executor_from_config(config: ProviderConfig) -> Executor:
    """Create the configured executor without leaking provider details into the core."""
    if config.provider == "mock":
        return MockExecutor()
    if config.provider == "codex":
        return CodexExecutor(model=config.model, reasoning_effort=config.reasoning_effort)
    raise ValueError(
        f"Unsupported executor provider {config.provider!r}. Choose 'mock' or 'codex' in Dennice Setup."
    )
