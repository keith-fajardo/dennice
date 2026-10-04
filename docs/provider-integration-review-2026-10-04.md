# Provider integration review — 2026-10-04

This is a technical mapping of Dennice's current authentication paths to the
providers' published documentation for **personal local use**: the user
installs and runs Dennice and provider CLIs on their own machine, and uses
their own provider subscriptions or API keys. It is not approval for a hosted
or commercial distribution. Recheck the linked documents at release.

| Dennice path | Current implementation | Published boundary and release consequence |
| --- | --- | --- |
| Claude Code CLI | Starts the user's installed `claude` binary; Dennice does not offer Claude account login, read a Claude session token, or replace the binary's authentication flow. | Personal local use follows the user's own CLI sign-in path. Anthropic distinguishes that from a developer offering Claude.ai login or intermediating subscription credentials; preinstalling or distributing the binary may change the applicable terms. [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) |
| Claude API | Sends requests to the official Messages endpoint using a key named by an environment variable. | API-key authentication is the documented direct API path. Shared or hosted deployments need a credential owner, workspace, billing, and secret-management design; Dennice currently assumes a user-managed local environment. [Claude API authentication](https://platform.claude.com/docs/en/manage-claude/authentication) |
| Codex app-server | Starts the user's installed `codex app-server`, identifies Dennice in `initialize`, checks `account/read` before each executor turn, and uses the existing local CLI authentication. It does not call account/login methods or manage OAuth tokens. | OpenAI documents local/open-source app-server authentication but says that authentication path has not been permitted for commercial or hosted services. A commercial or hosted Dennice offering needs a different approved integration path and its own authentication review. Enterprise integrations should also address the documented client identity process. [Codex app-server authentication and client identity](https://learn.chatgpt.com/docs/app-server) |
| GitHub Copilot CLI | Starts the user's installed `copilot` binary and delegates GitHub authentication to that CLI. Dennice removes Copilot BYOK provider overrides and the saved BYOK registry, does not read credentials, and fails before startup if an environment token can override the stored account. | GitHub documents Copilot CLI access for all Copilot plans, with organization policy required for organization-provided seats. On a local desktop, OAuth is the recommended sign-in; `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` can override the CLI's stored login, and `gh` auth is a fallback. Verify the CLI is signed into the intended personal account before testing. Individual GitHub terms permit use of AI-feature inputs/outputs for model development unless the user opts out in account settings; disabling local OTel message-content capture does not change that provider-side data policy. [Copilot CLI authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli), [GitHub Terms of Service, section J](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service), [individual Copilot plans](https://docs.github.com/copilot/managing-copilot/managing-copilot-as-an-individual-subscriber/managing-copilot-free) |
| OpenAI API | Sends requests to the official Responses endpoint using a key named by an environment variable. | Direct API use follows the API documentation and the credential owner's agreement. This is separate from a user's Codex/ChatGPT subscription. [OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication) |

The Claude Agent SDK is not used by this repository. Anthropic documents it as
an application integration using API-key authentication and disallows third-party
Claude.ai login without prior approval. Replacing the CLI adapter with the SDK
would change the authentication and commercial review, not merely the Python
interface. [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)

Billing boundary: Dennice inherits the parent environment when launching
`claude -p`. Anthropic documents that several environment variables can
override the saved login or route requests through a cloud provider or gateway.
Dennice's subscription mode rejects these inherited endpoint, provider, and
credential overrides before inference. It accepts `claude.ai` login and the
documented subscription OAuth token, while API-key mode accepts the API key
and API-key-helper auth methods. Provider-default mode explicitly accepts other
CLI auth. Local tests cover these methods with a mock CLI; user settings files
and live billing behavior remain unverified.
[Claude Code environment variables](https://code.claude.com/docs/en/env-vars),
[CLI authentication-status command](https://code.claude.com/docs/en/cli-reference)

The Claude Sonnet and Opus pool IDs match Anthropic's current published IDs.
The local Auto pool no longer includes Haiku 4.5 because Anthropic lists its
retirement as no later than 2026-10-15. The user should refresh Claude Code's
model catalog in Setup and run its live check to confirm account-specific CLI
availability. [Anthropic model IDs and lifecycle](https://platform.claude.com/docs/en/models/overview)

The ignored local routing pool now marks tool and image support for its
configured Codex, Claude, and Copilot candidates. This matches published
capabilities for GPT-5.6 Terra, Claude Sonnet/Opus 5.5, Gemini 3.7 Flash, and
the local provider CLI tool surfaces. These flags affect candidate eligibility;
Dennice's permission modes and provider approvals still control access.
Catalog availability remains account-specific. [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[Claude models](https://platform.claude.com/docs/en/models/overview),
[Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash),
[Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)

Codex's documented `account/read` response distinguishes ChatGPT from API-key
authentication. Dennice defaults to requiring ChatGPT for executor turns and
System 1 classification; an API-key or other-provider mode requires a separate
explicit Setup/YAML choice for each. Fixture tests confirm that a mismatch
stops before `thread/start` or router inference. They do not verify a real
account or eventual billing. Router classification starts a temporary app-server
only for this preflight, so it incurs local startup time.
[Codex app-server account API](https://learn.chatgpt.com/docs/app-server)

The selected scope is personal local use. The user installs each CLI, owns the
subscription or API key, and pays for model usage; Dennice does not initiate or
intermediate provider login. Any later distribution, shared credential, hosted
service, or binary bundling changes this assessment and requires a new review.
This source review does not establish the user's selected GitHub privacy setting,
actual CLI account, organization entitlements, live billing, or whether a specific
workspace may be submitted to a provider. Confirm those in the account before
live testing. No real account, credential, or paid model call was used for this
review.
