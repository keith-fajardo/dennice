# Local qualification record — 2026-10-04

This is evidence from one macOS development sandbox, not a production release
certificate. No paid model inference, credential values, project hook, or
configured project MCP server was used for this record. Authentication status
was checked without recording account identity or token material.

| Check | Observation | Scope |
| --- | --- | --- |
| Python suite | `python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning`: 332 passed on Python 3.12.8 (65.46s), re-run against the current worktree on 2026-10-04. The full suite also passed on 3.12.11 before the latest Copilot auth guard (329 tests); after that change, all 11 Copilot runtime tests passed on 3.12.11 | Local macOS; warnings promoted to errors. The 3.12.11 runs used a temporary venv and the locally installed 3.12.8 dependency set via an isolated-mode-visible `.pth`; this checks patch-version compatibility, not a fresh dependency resolution |
| API completion | JSON fallback and streamed OpenAI terminal events reject incomplete responses even when partial answer text exists | Mock HTTP transport; no paid provider call |
| API token-budget telemetry | Missing input/output usage is represented as unknown and stops the tool loop before any requested action | Mock stream response; no provider network call |
| TUI failure display | Pilot tests render the spinner in the chat pane and show cumulative run usage separately from latest-request context after a simulated token-budget failure | Headless terminal layout; no real provider turn |
| Auto-route display | A pilot test shows the effective model/effort and uses its reported context even when the configured model differs; route state survives a session reload | Mock route and usage events; no real provider turn |
| Package install | Built a wheel without network access, installed it in a temporary virtual environment, and ran `dennice --help`, `dennice init`, and an offline Mock run | Local wheel and bundled policy/schema resources |
| Current wheel smoke | Rebuilt the current source as a wheel without build isolation or dependency resolution, installed it to a temporary target, ran the installed `dennice --help`, initialized a temporary project, verified bundled policy/schema/benchmark fixture resources, and completed an offline Mock run | Local macOS and existing system dependencies; no network or model call |
| CLI benchmark smoke | Ran `dennice benchmark run --mode router --json` from an isolated temporary project configured with the Rule router and Mock executor; the packaged development item completed | Local CLI and synthetic fixture only; no external provider or model call |
| Benchmark evidence | The seeded Snowflake item now has three synthetic fixtures. Local and installed-wheel dataset loaders validate them, and a run trace shows the evidence in the user task while the gold answer stays out | Development item only; no held-out task-success result or paid model call |
| Outcome pairing and cost scope | Repeated task IDs must have matching counts across arms for `paired_item_sets`; cost descriptions explicitly exclude cache discounts and router/service costs | Offline synthetic report fixtures only; no measured quality or invoice validation |
| MCP stdio | `tests/test_mcp_stdio.py` and `tests/test_mcp_stdio_faults.py` check capability shapes, allowlists, calls, pagination cycles, unexpected/substituted URIs, schema errors, oversized output, timeout without replay, and disconnect against isolated server processes | Local subprocess transport; no configured project server |
| MCP connection-test display | TUI fixture confirms the `/mcp test` summary distinguishes approved tools/resources/prompts and states that no tool or content was fetched | Simulated connection status; no configured project server |
| Copilot Setup disclosure | TUI test confirms the account-override and individual-plan input/output privacy note appears only when GitHub Copilot is selected | Headless Textual pilot; no provider account or model call |
| Claude Code | Installed version 2.1.289; local help lists `--restricted`, `--settings`, `--system-prompt-snapshot`, `--effort`, and `--tools ""`. A no-inference `auth status` check returned `loggedIn: false` and `authMethod: none` | No signed-in account is available in this environment; no bridge listener or model turn |
| Claude model pool | Anthropic's current model documentation confirms `claude-sonnet-5-5` and `claude-opus-5-5`; it schedules `claude-haiku-4-5-20251001` retirement no later than 2026-10-15. That Haiku candidate was removed from the ignored local Auto pool | Official ID/lifecycle documentation only; account availability and Claude Code CLI acceptance still need the user's Setup refresh and live check |
| Auto capability flags | The ignored local pool now declares tool and image support for its Codex, Claude, and Copilot candidates. A route-policy test and a no-provider-call check select eligible models for a task requiring tools and an image | Capability declarations are checked against published model/CLI documentation; actual account entitlement and each local CLI's runtime behavior still need live qualification |
| Routing config validation | Rejects unknown provider-pool keys, duplicate model IDs, blank candidate IDs, and supporting thresholds above primary thresholds | Offline Pydantic validation tests; no provider calls |
| Copilot output stream | Incremental UTF-8 decoding preserves a multibyte character split across OS pipe chunks; CLI diagnostic sentinels remain withheld | Local subprocess fixture; no Copilot login or model call |
| Copilot budget telemetry | Local file OTel totals provide input/output tokens and inference-call counts; missing/over-limit usage fails a budgeted run; content capture is disabled | Mock CLI JSONL fixture based on GitHub's documented OTel span fields; live CLI exporter still needs the user's qualification |
| Copilot provider isolation | Parent-process BYOK URL/key/type, custom registry path, and model override are not inherited; the CLI receives a private empty provider registry. `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, and `GITHUB_TOKEN` account overrides stop the run before process startup | Local simulated subprocess with OpenAI and GitHub-token sentinels; no live provider or API call |
| Copilot spawn cleanup | An OS permission failure while starting the CLI removes the temporary telemetry directory and propagates the spawn error | Isolated subprocess-launch fixture; no Copilot login or model call |
| Run timeout | A timed-out executor closes, partial output remains visible, and the trace is marked `timed_out` with a final failure event | Deterministic executor fixture; no provider process or model call |
| Hook spawn failure | A trusted hook that cannot be launched produces matched start/completion events with a safe failure category and unknown outcome | Mock subprocess launch failure; no configured project hook executed |
| Verification and shell command timeout | The shared command runner's timeout kills its owned POSIX process group, including a spawned descendant; focused regression test passes | Local POSIX subprocess fixture; Windows behavior still needs hosted CI |
| Codex | Installed `codex-cli 0.160.0`; generated app-server schemas include the request fields and approval methods used by the adapter | Protocol metadata only; no model turn |
| Codex app-server startup | A no-inference `initialize` attempt exited with status 1; direct CLI stderr reported `Operation not permitted (os error 1)` | The sandbox prevents a real local round-trip here; adapter behavior remains unqualified |
| Codex usage budget | A completed app-server turn with no valid cumulative usage update now fails instead of claiming the configured token budget was enforced | Mock app-server protocol regression; no model turn |
| CI matrix | Workflow configures Ubuntu, macOS, and Windows on Python 3.11/3.12, with CLI/package-resource smoke checks including all three bundled benchmark evidence files and promotes unraisable-exception warnings to errors. Subprocess fixtures pass Windows `taskkill` cleanup through and accept the documented Git Bash command wrapper | Remote runs on this worktree have not been observed; GitHub CLI authentication is invalid here, and no local Docker daemon is available |
| CI dependency check | Workflow now runs `python -m pip check` in each clean matrix environment after installation | Added to workflow; remote execution remains unobserved |
| Provider integration | The user selected personal local use; current primary documentation is mapped to the code's local CLI and direct API authentication paths in the [provider review](provider-integration-review-2026-10-04.md). OpenAI API calls were excluded at the user's request | No real credential, live billing check, or provider approval is evidenced here |
| Claude CLI auth boundary | Fixture tests verify subscription-default preflight, provider/endpoint override rejection before spawn, subscription OAuth and API-key-helper method mapping, API-key environment rejection, explicit API/other modes, and Setup persistence | Mock status process only; no real credential or billing check |
| Claude usage budget | A completed native run without valid input/output token telemetry now fails instead of claiming the configured per-run token budget was enforced | Mock Claude stream regression; no model call |
| Codex auth boundary | Fixture tests verify ChatGPT-default `account/read` preflight for executor and router, API-key mismatch rejection before inference, explicit API/other modes, and separate Setup persistence | Mock app-server response only; no real credential or billing check |
| Codex local stdio lifecycle | A child-process JSON-RPC fixture exercises executor initialization, auth check, native approval/denial, session resume, completed turn, and interrupted turn; the fixture exposed and verified a fix for an unnecessary interrupt after completion | Real local transport with a simulated app-server; no Codex binary or model turn |
| Claude local stdio lifecycle | A child-process CLI fixture exercises auth preflight, terminal completion, session resume, incomplete result, and deterministic child cleanup when a plan or write-profile event stream closes; it exposed a missing nested-stream close, now fixed | Real local subprocess with a simulated CLI; the write-profile test uses a simulated gate because this sandbox cannot bind the live bridge |
| Native diagnostic privacy | Failure fixtures put a sentinel in Claude, Copilot and Codex CLI stderr/structured errors and app-server messages; assertions verify it is absent from returned errors and provider events | Local simulated processes and protocol responses; detailed provider diagnostics are intentionally withheld from Dennice traces |
| Setup diagnostic privacy | Unclassified executor/router exceptions expose only their exception type and a safe remediation hint; exact adapter-authored `RuntimeError` messages remain visible | Simulated exception sentinels; no provider connection |

Release evidence still needed: real Claude bridge denial/approval/cancellation and
resume checks; real Codex app-server approval, denial, resume and cancellation;
observed cross-platform CI; and held-out task evaluations with independent
completion and critical-failure checks. Provider integration sources have been
reviewed for personal local use; the user's specific account terms, privacy
settings, and actual billing remain unverified.
The user authorized up to $25 for release evaluation, but excluded OpenAI API
calls, and no paid model call has run. See
[extension qualification](extension-qualification.md) for the full gates.

## Manual app checks awaiting user results

The user will run live connection checks in their own app environment. In
`Ctrl+S` Setup, test each intended local executor—Codex, Claude Code, and
GitHub Copilot—with **Test executor (live)**. Refresh the available model
catalog where Setup offers it; for Copilot, select an account-supported model
from its choices. Choose the intended CLI authentication mode for Codex and
Claude. Before Copilot testing, verify `copilot login` points to the intended
personal account, unset `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, and `GITHUB_TOKEN`,
and review the account's Copilot input/output privacy setting. Each live check
sends one short request and may use that provider account's quota. For routers,
select Rule and use **Test router** as the offline
control, then test Codex, Jev, or OpenJev only if that router is configured for
use. Jev needs its own credential. Do not select OpenAI API for this
qualification.

Record the provider, exact model ID, authentication mode, pass/fail detail,
and approximate time for each check. Share no keys or account identifiers.
For a failed full run, inspect `Ctrl+D` and record the stopping reason. These
connection checks do not close the real approval, cancellation, resume, or
held-out quality gates above.
