"""Check the local Codex CLI's authentication mode before model inference."""


async def check_codex_auth(client, expected_mode: str) -> str:
    if expected_mode not in {"chatgpt", "api_key", "provider_default"}:
        raise ValueError("Unknown Codex CLI authentication mode")
    response = await client.request("account/read", {"refreshToken": False}, timeout=8)
    if not isinstance(response, dict):
        raise RuntimeError("Codex authentication status was unavailable; sign in with `codex login` and retry.")
    account = response.get("account")
    kind = account.get("type") if isinstance(account, dict) else None
    if kind not in {"chatgpt", "apiKey"}:
        if account is None and response.get("requiresOpenaiAuth") is False:
            kind = "other_provider"
        else:
            raise RuntimeError("Codex authentication status was unavailable; sign in with `codex login` and retry.")
    expected = {"chatgpt": "chatgpt", "api_key": "apiKey"}.get(expected_mode)
    if expected and kind != expected:
        raise RuntimeError(
            f"Codex authentication is {kind}, but codex_cli_auth expects {expected_mode}. "
            "Choose the matching authentication mode in Setup before running a model turn."
        )
    return kind
