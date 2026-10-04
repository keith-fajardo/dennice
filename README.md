# Dennice

Dennice is a local-first AI harness for analytics and engineering work. It makes three decisions explicit before an agent acts: what kind of reasoning the task needs, which approved model should execute it, and what evidence is needed to call the work complete.

Use it when a single general-purpose chat prompt is not enough. Dennice classifies the task, attaches concise reasoning guidance, can choose a model and reasoning effort from a controlled pool, and preserves an inspectable local record of the run.

## What Dennice offers

- **Cognitive routing.** A System 1 router identifies a task family plus primary and supporting reasoning demands.
- **PA policies.** Versioned philosophical-assistant policies give the executor task-appropriate investigative guidance without requiring private reasoning traces.
- **Model routing.** Fixed, shadow, and automatic modes select only from an approved pool inside the active provider.
- **Provider choice.** Run work through Codex, Claude Code, GitHub Copilot, OpenAI API, Anthropic API, a local OpenAI-compatible server, or an offline mock executor.
- **Controlled execution.** Permission profiles, per-action approvals, tool controls, hooks, and MCP integration keep authority explicit.
- **Evidence and recovery.** Local run traces capture routing, policies, effective model, approvals, tool events, usage, verification, and interrupted work.

Dennice does not make an answer correct by itself. Its routing rules and provider capability declarations must be evaluated on representative work before they are used for consequential decisions.

## Install and run

Dennice requires Python 3.11 or later.

```bash
git clone https://github.com/keith-fajardo/dennice.git
cd dennice
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

On Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
```

Create an offline project configuration and launch the terminal interface:

```bash
dennice init
dennice
```

`dennice init` creates `dennice.yaml`, benchmark folders, and `.dennice/runs/`. Its initial configuration uses the offline Rule router and Mock executor, so it makes no provider request.

You can also use the CLI directly:

```bash
dennice classify "Why did Snowflake credits increase yesterday?" --json
dennice run "Investigate why Snowflake credits increased yesterday."
dennice benchmark list
dennice benchmark run --mode router --json
```

In the TUI, use `Ctrl+S` to configure the router and executor independently, save, then submit a task.

Setup's **Test executor (live)** starts one bounded provider turn and may consume provider quota or credits. It uses an empty temporary workspace and a 30-second ceiling (or a lower configured limit). API requests are capped at 128 output tokens and one model call; native CLIs may make internal calls that Dennice cannot count, and usage reports can cross the 4,096-token stop threshold before the turn is interrupted. Claude's built-in tools are disabled for this check. **Test router** also contacts the selected router unless it is the offline Rule router.

## How a run works

```text
Task and allowed context
        │
        ▼
System 1: task family and cognitive assessment
        │
        ├──► optional model and effort selection
        │
        ▼
PA policy selection and prompt composition
        │
        ▼
System 2: selected provider executor and permitted tools
        │
        ▼
Verification, trace, and session state
```

The router identifies a task family, a primary cognitive demand, optional supporting demands, and an execution assessment. The primary demand selects the main PA policy; supporting demands can add a bounded number of supplementary policies.

| Cognitive demand | What it guides |
|---|---|
| `critical_inquiry` | Clarifying assumptions and discriminating hypotheses |
| `empirical_induction` | Evidence, measurements, and cautious inference |
| `decomposition` | Separating a task into independently testable parts |
| `constraint_reasoning` | Requirements, invariants, and business rules |
| `causal_categorization` | Entities, failure modes, and causal structure |
| `contradiction_resolution` | Conflicting definitions or claims |
| `abstraction` | Reusable concepts and architecture |

PA policies shape the execution prompt. They do not grant permissions, execute tools, select a provider, or prove a conclusion.

## Configure a provider

System 1 and System 2 are separate. For example, OpenJev can classify the task while Claude Code executes it.

| Provider | Role | Authentication |
|---|---|---|
| Rule | Offline System 1 router | None |
| Codex | Router or executor | Existing local Codex CLI login; ChatGPT mode by default |
| Jev | Hosted System 1 router | Environment-variable API key |
| OpenJev | Local System 1 router | None; loopback endpoint |
| Claude Code | Executor | Existing Claude Code login |
| GitHub Copilot CLI | Executor | Existing Copilot login |
| OpenAI API / Anthropic API | Executor | Provider API key in environment |
| Local | Executor | Loopback OpenAI-compatible server |
| Mock | Offline executor | None |

Dennice stores only environment-variable names, never provider secrets. The API adapters use separately billed API credentials. CLI adapters use the provider CLI's active authentication, which may itself be a subscription or API key.

### Codex, Claude Code, and Copilot

Install and authenticate the provider’s own CLI before choosing it in Setup. Dennice invokes that local tool and does not collect subscription credentials.

[Codex app-server's account status](https://learn.chatgpt.com/docs/app-server) distinguishes ChatGPT and API-key authentication. Dennice checks `account/read` before Codex executor turns and System 1 classification, defaulting each to ChatGPT authentication. If the CLI reports API-key or another provider mode, the model call stops before inference. Choose **API key** or **Provider default / other** in the respective Setup section only when you intend that mode. The YAML levers are `executor.codex_cli_auth` and `router.codex_cli_auth`, each set to `chatgpt | api_key | provider_default`. Dennice records only the executor's authentication mode in its run trace; this preflight does not prove the eventual billing record.

[Claude Code's authentication documentation](https://code.claude.com/docs/en/authentication) says environment variables can select cloud providers, gateways, and credentials ahead of the saved login. Dennice checks `claude auth status` before each Claude run and defaults to requiring subscription authentication (`claude.ai` login or its subscription OAuth token). API key/token variables, custom endpoints, cloud-provider selectors and credentials, and alternate Anthropic profiles/federation settings stop the subscription-mode run before model inference. Select **API key** or **Provider default / other** in Setup only when you intend that CLI authentication and its billing. The equivalent YAML lever is `executor.claude_cli_auth: subscription | api_key | provider_default`; `provider_default` explicitly accepts the CLI's current non-subscription method. Dennice records only the normalized method, not the CLI's status JSON or credentials. This preflight does not prove the provider's eventual billing record.

These adapters currently target local, user-operated installations. [OpenAI's app-server documentation](https://learn.chatgpt.com/docs/app-server) distinguishes local/open-source authentication from commercial or hosted use, and [Anthropic's Claude Code guidance](https://code.claude.com/docs/en/legal-and-compliance) sets conditions for products that run its binary. Review the [provider integration mapping](docs/provider-integration-review-2026-10-04.md) before distributing or hosting Dennice.

- Codex defaults to a read-only sandbox. Workspace-write must be explicitly selected.
- Claude Code uses a restricted plan profile by default. Its write profile exposes only the allowed file tools, and exact edits require approval.
- Copilot uses a narrow read or file-write allowlist. Dennice does not enable shell or MCP tools for the Copilot adapter.
- Copilot runs through the user's stored GitHub Copilot CLI login (the CLI may fall back to the authenticated `gh` account if no Copilot login is stored). Dennice removes `COPILOT_PROVIDER_*`, `COPILOT_MODEL`, and any saved BYOK registry from that subprocess, and supplies an empty temporary provider registry. It stops before startup if `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` has a non-empty value, because those variables can override the saved Copilot account. Unset them, sign in with `copilot login`, and verify the CLI's active account before testing. GitHub's individual-account terms allow AI inputs and outputs to be used for model improvement unless the account opts out in its settings; Dennice's local OTel content-capture setting does not change GitHub's policy. Check the current [provider integration mapping](docs/provider-integration-review-2026-10-04.md) and account privacy setting before sending workspace content.
- Copilot token totals are read from a private, temporary local OpenTelemetry file with message-content capture disabled. A budgeted run fails if the installed CLI does not produce valid usage counters; Copilot credits and provider cost multipliers are not currency and are not included.

Refresh the provider’s model catalog in Setup before choosing a model. Availability, quota, and supported effort levels remain account-specific. Claude labels include the CLI-reported resolved ID, such as `claude-sonnet-5-5`.

### API and local execution

Set API credentials in the environment before starting Dennice:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

Choose the API provider in Setup, refresh its catalog, and select a specific model. For a local provider, enter an OpenAI-compatible `/v1` endpoint, such as:

```text
Ollama:    http://127.0.0.1:11434/v1
LM Studio: http://127.0.0.1:1234/v1
```

API/local execution can use Dennice’s native tool loop when `/tools on` is set. Local endpoints must be loopback. Hosted API keys are sent only to official provider HTTPS hosts, and redirects are refused.

### Local OpenJev router

OpenJev is a local System 1 decision service, not the chat model. It receives the task and typed classification questions, then returns task-family and cognitive-demand scores.

```yaml
router:
  provider: openjev
  model: openjev
openjev:
  endpoint: http://127.0.0.1:8771/v1/systemone
  model: openjev
```

The endpoint must be loopback HTTP(S). See the [OpenJev documentation](https://huggingface.co/openjev/openjev) for serving requirements.

## Automatic model and effort routing

Automatic routing chooses only from models explicitly approved for the active provider. It does not change provider, login, permission mode, or tool authority. The provider CLI still determines whether its own active authentication uses a subscription or API billing; check that separately as described above.

| Mode | Result |
|---|---|
| `fixed` | Use the configured executor model and effort. |
| `shadow` | Record the recommended route, but use the configured model and effort. |
| `auto` | Run the eligible model and effort from the approved pool. |

The TUI status line keeps the configured model and effort visible. When a run selects different values, `Run route` (or `Last route` afterward) shows the effective model and effort. The `Ctx` meter uses the reported context for that route.

To enable automatic routing, define candidates with their known capabilities. This Codex example must be adapted to models your account supports:

```yaml
routing:
  mode: auto
  model_pinned: false
  effort_pinned: false
  # PA policies attach only when cognitive scores meet these thresholds.
  # Scores are routing signals, not calibrated success probabilities.
  primary_threshold: 0.8
  supporting_threshold: 0.55
  model_pool:
    codex:
      - model: gpt-6-luna
        tier: lightweight
        context_tokens: 32000
        efforts: [low, medium, high]
      - model: gpt-5.6-terra
        tier: strong
        context_tokens: 32000
        efforts: [low, medium, high]
```

The built-in route policy uses low effort for lightweight work, medium effort for balanced work, and high effort for strong work. A simple low-stakes task can use a lightweight candidate; a moderate task uses balanced when available; complex, high-stakes, or uncertain work uses a strong candidate. If no compatible candidate exists, Auto stops before execution rather than guessing a capability or switching providers.

Candidates declare context size and tool/vision support. Dennice does not infer those capabilities from a model name. `model_pinned: true` restricts Auto to the current model; `effort_pinned: true` preserves the current effort. The current policy does not automatically select `xhigh`; choose it manually until an explicit policy is added.

Manage the same settings in the TUI:

```text
/routing fixed|shadow|auto
/routing pin-model|unpin-model|pin-effort|unpin-effort
/pool add <exact-model-id> lightweight|balanced|strong [tools] [vision] [context=8192] [efforts=low,medium,high]
/pool remove <exact-model-id>
```

Classifier confidence is not a probability that execution will succeed. The primary score must meet `primary_threshold` before any PA is attached; each supporting policy must also meet `supporting_threshold`.

## Use the terminal interface

The TUI stores sessions, transcripts, workspace context, and traces in the current project directory.

| Action | Command or shortcut |
|---|---|
| New session | `Ctrl+N` or `/new` |
| Open Setup | `Ctrl+S` |
| Stop active work | `Ctrl+X` |
| Show active configuration | `/config` |
| Switch session | `/session <number>` |
| Search sessions | `/sessions` |
| Rename session | `/rename <title>` |
| Choose model | `/model <name>` or Setup |
| Choose effort | `/effort <low|medium|high|xhigh|default>` |
| Choose permissions | `/permissions <read-only|read-write|plan>` |
| Set workspace directory | `/cwd "path"` |
| Attach image | `/attach "/absolute/path/image.png"` or `Ctrl+V` |
| Remove pending images | `/clear-images` |
| Compact earlier context | `/compact` |
| Start a bounded goal | `/goal <objective>` |
| Inspect recovery | `/recovery list` |

The composer accepts multiple lines; press `Ctrl+Enter` to submit. Direct local terminal commands begin with `!`, for example `! git status --short`. Dennice captures them as terminal output and keeps them out of the model conversation history.

### Sessions, workspace files, and images

Sessions survive restart in the same workspace. Local session search supports case-insensitive regular expressions such as `snowflake|billing`. Each session keeps its own workspace directory.

The file explorer provides bounded search, preview, explicit save, undo/redo, and regex replacement. It excludes linked, credential-like, binary, and oversized files. Deletes, moves, and writes require visible user actions; Dennice never silently bulk-overwrites a workspace. Secure file browsing and editing require POSIX semantics, including WSL; native Windows file editing fails closed.

You can attach up to eight PNG, JPEG, GIF, or WebP images of up to 10 MB each. Clipboard snapshots are stored under `.dennice/attachments/` and are not uploaded until a task is submitted. Codex receives native image attachments; Claude Code is directed to read the image file; API/local providers receive image content where supported. The System 1 router currently receives text rather than image pixels.

### Skills

Use `/skills` to browse local Claude and Codex skills, then `/skill <qualified-key> <task>` to run one. Skill text remains user-level context: it cannot expand permissions, automatically run scripts, or bypass approval.

## Hooks and MCP

Dennice has its own lifecycle hooks and MCP connector. They complement rather than replace Claude Code’s provider-native extension system.

Supported hook events are `before_route`, `after_route`, `before_execution`, `before_tool`, `after_tool`, `before_verification`, and `turn_complete`. Hooks receive bounded metadata and can allow or deny the harness lifecycle operation. They cannot grant permissions, override routing pins, or intercept every provider-native tool action.

MCP is available to API/local tool loops, not injected into the native Codex, Claude Code, or Copilot loops. MCP tools, resources, and prompts must be allowlisted; every access requires approval. MCP content is treated as untrusted data, never as system instructions.

Tools, hooks, and MCP are disabled by default:

```yaml
tools:
  enabled: false
  root: .
hooks:
  - name: validate
    event: before_execution
    command: [python, scripts/validate.py]
    enabled: false
    required: true
mcp:
  - name: example
    transport: stdio
    command: [python, scripts/mcp_server.py]
    enabled: false
    approved_tools: [lookup]
```

Use `/tools on`, `/hooks list|trust|enable|disable <name>`, and `/mcp list|trust|enable|disable|test <name>`. Trust is tied to the current launch and workspace; changing server settings invalidates it. Opening configuration does not launch a process.

## Verification, traces, and recovery

Dennice records the task, configuration snapshot, routing decision, PA policies, effective model route, approvals, tool events, usage, output, and verification result in `.dennice/runs/runs.sqlite3` by default.

```bash
dennice inspect <run-id>
```

`dennice run` exits nonzero for failed, timed-out, cancelled, or interrupted runs. Both commands label partial output as unverified and show the stopping reason; `dennice inspect --json` includes the complete trace.

When a native CLI fails, Dennice records its exit status or a bounded error category. It withholds raw provider stderr and error messages from the trace because they may contain prompt text or credentials. Inspect the provider CLI locally for detailed diagnostics.

Run data contains plaintext prompts, transcripts, image paths, configuration references, and output. Treat the runs directory as sensitive; local permissions are not encryption.

Interrupted work is never replayed automatically because an external action may already have happened. Use `/recovery inspect <run-id>` and `/recovery reconcile <run-id> <operation-id> <completed|not_executed|compensated> <evidence>` to record verified outcomes.

Goals add an objective and bounded retries:

```text
/goal Verify the dashboard query against fixture data
```

Configure meaningful completion checks first:

```yaml
verification:
  required_files: [report.md]
  commands:
    - [python, -m, pytest, tests/test_report.py]
```

A nonempty answer receives an `unverified` run status unless an independent file or command check is configured and passes. Verification commands require approval and are not sandboxed. Budgets limit elapsed time, model calls, tool calls, output tokens, and reported total tokens, but provider usage may remain unknown.

For Codex, `budgets.max_total_tokens` defaults to `100000` and counts cumulative reported input plus output tokens for a run. The status line shows a separate `Run` meter against this limit, then keeps it as `Last run` when execution stops. `≥` means some usage is unknown; `pending` and `unknown` mean no usable provider report has arrived. The `Ctx` indicator shows the latest model request against the model's context window, not the remaining run budget. Native usage reports arrive after model activity, so a run can cross the limit before Dennice interrupts it. Raise the limit in `dennice.yaml` only when the task warrants the additional usage:

```yaml
budgets:
  max_total_tokens: 200000
```

API, Claude, Codex, and Copilot adapters fail a budgeted run when the token counts needed to enforce the cap are missing. Copilot emits usage only after its CLI invocation completes, so Dennice can report an over-limit result but cannot interrupt an internal Copilot model call at the exact token boundary. Copilot usage metering uses a temporary local OTel file and explicitly disables prompt/response content capture.

## Privacy and operational boundaries

- The router cannot choose a provider, grant permission, execute a tool, or create arbitrary routes.
- Codex classification runs its CLI in a disposable directory with a read-only sandbox; it receives the task text and opt-in routing history, not the project working directory.
- Auto-routing stays within the active provider’s approved pool.
- Set `privacy.router_history: true` before prior conversation turns are shared with the router.
- `privacy.local_only: true` rejects remote/CLI providers and unsandboxed hooks or MCP processes. It does not sandbox arbitrary local processes.
- Hosted routers reject redirects and implicit proxies; OpenJev accepts loopback endpoints only.
- Cancelling stops ongoing work but cannot undo an external side effect.

Review the selected provider, model, permission profile, tool settings, and workspace directory before authorizing work with side effects.

## Known limits

- The Rule router is a narrow deterministic development classifier. Use Codex, Jev, or OpenJev for broader task classification.
- The Rule router lacks a full assessment for most tasks, so automatic routing treats them conservatively.
- OpenJev’s current typed request does not ask whether tools or vision are needed. Attached images are still detected by the route policy.
- Cognitive scores are uncalibrated, so PA thresholds need held-out evaluation before they can be treated as quality controls.
- Model capability declarations, context sizes, and effort support must be verified with the provider and account.
- Current benchmarks measure routing labels, not general task quality, cost savings, or provider parity.
- Provider-native sessions and tools remain provider-specific. Dennice cannot intercept every action inside a native provider loop.
- Full platform qualification, held-out routing evaluations, and some provider integration evidence remain release gates.

See the [production-harness plan](docs/production-harness-plan.md), [recovery and accounting](docs/recovery-and-accounting.md), [platform and evaluation](docs/platform-and-evaluation.md), [extension qualification](docs/extension-qualification.md), and [provider integration review](docs/provider-integration-review-2026-10-04.md) for implementation boundaries and release criteria.

## Develop and test

```bash
pytest -q
ruff check src tests
```

The bundled Snowflake benchmark is a synthetic development fixture. `dennice init` copies its three evidence files into `benchmarks/fixtures/`; benchmark runs validate and pass their bounded contents as untrusted task data. Missing or escaping fixture paths fail before a model call. The gold answer and cognitive labels are never added to the task prompt. This example scores routing labels only; use controlled representative tasks and independent completion checks to evaluate routing, PA policies, model selection, cost, or latency.

## Windows notes

The TUI works in Windows terminals. For Codex, Claude Code, and Copilot, install and authenticate the provider CLI in Git Bash or WSL and ensure `bash` is on `PATH`; Dennice invokes those CLIs through Bash on Windows.

Claude Code supports Windows through WSL or Git for Windows. GitHub Copilot CLI is installed with `npm install -g @github/copilot` and authenticated with `copilot login`. Provider access remains account and organization dependent.
