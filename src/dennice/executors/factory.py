"""Construction of configured, provider-specific System 2 executors."""

from dennice.core.config import ProviderConfig
from dennice.executors.base import Executor
from dennice.executors.api import APIExecutor, API_PROVIDERS
from dennice.executors.claude import ClaudeExecutor
from dennice.executors.copilot import CopilotExecutor
from dennice.executors.codex import CodexExecutor
from dennice.executors.codex_appserver import CodexAppServerExecutor
from dennice.executors.mock import MockExecutor


def executor_from_config(config: ProviderConfig) -> Executor:
    """Create the configured executor without leaking provider details into the core."""
    if config.provider == "mock":
        return MockExecutor()
    if config.provider in API_PROVIDERS:
        return APIExecutor(config)
    if config.provider == "codex":
        return CodexAppServerExecutor(
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
            cli_auth_mode=config.codex_cli_auth or "chatgpt",
        )
    if config.provider == "claude":
        return ClaudeExecutor(
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
            cli_auth_mode=config.claude_cli_auth or "subscription",
        )
    if config.provider == "copilot":
        return CopilotExecutor(
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
        )
    raise ValueError(
        f"Unsupported executor provider {config.provider!r}. Choose Mock, Codex, Claude, Copilot, or an API/local provider in Dennice Setup."
    )
