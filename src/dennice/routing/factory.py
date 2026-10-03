"""Construction of configured System 1 cognitive routers."""

from dennice.core.config import JevConfig, OpenJevConfig, ProviderConfig
from dennice.routing.base import CognitiveRouter
from dennice.routing.codex import CodexRouter
from dennice.routing.openjev import JevRouter, OpenJevRouter
from dennice.routing.rule import RuleRouter


def router_from_config(
    config: ProviderConfig,
    *,
    jev: JevConfig | None = None,
    openjev: OpenJevConfig | None = None,
) -> CognitiveRouter:
    """Create the configured router without coupling the harness to a provider."""
    if config.provider == "rule":
        return RuleRouter()
    if config.provider == "codex":
        return CodexRouter(model=config.model, reasoning_effort=config.reasoning_effort)
    if config.provider == "jev":
        if jev is None:
            raise ValueError("Jev requires DenniceConfig.jev connection settings.")
        return JevRouter(
            endpoint=jev.endpoint,
            model=jev.model,
            api_key_env=jev.api_key_env,
            timeout_seconds=jev.timeout_seconds,
        )
    if config.provider == "openjev":
        if openjev is None:
            raise ValueError("OpenJev requires DenniceConfig.openjev connection settings.")
        return OpenJevRouter(
            endpoint=openjev.endpoint,
            model=openjev.model,
            timeout_seconds=openjev.timeout_seconds,
        )
    raise ValueError(
        f"Unsupported router provider {config.provider!r}. Choose 'rule', 'codex', 'jev', or 'openjev'."
    )
