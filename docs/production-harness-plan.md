# Dennice production harness: implementation plan

Status: implementation delivered through offline conformance and regression tests; production qualification remains incomplete. Prepared 2026-10-03 after repository inspection and an independent AI-engineering critique. Existing uncommitted implementation work is preserved. Live inference, deployment, credential access, commits, or pushes have not been requested for this milestone.

Implemented so far: hardened router transport; per-run snapshots; in-flight SQLite event checkpoints; durable sessions; cancellation/process cleanup; terminal-history isolation; Setup fixes; typed assessments; fixed/shadow/opt-in same-provider pool routing with pins; streaming API/local tool loops with explicit approvals; directory-scoped launch-local hook/MCP trust controls and slash commands; bounded verification and durable goals; native session linkage/events; owner-lock crash recovery and explicit reconciliation; unknown-aware usage accounting; session-scoped file exploration/editing/search; and offline evaluation/platform fixtures. Security fixtures cover authority, routing, trust, tool loops and context role separation. README documents current controls and limits. MCP currently exposes tools only, not resources/prompts; hooks have seven supported lifecycle events, not complete native-provider coverage. Advanced configuration uses slash commands/YAML, not dedicated Setup editors. Exact Claude interactive approval bridging, actual platform qualification, compliant integration review and held-out quality/cost results remain release gates. These changes do not establish competitor parity or measured routing benefit.

## 1. Goal and scope

Build a reliable terminal agent harness whose differentiator is System 1 assessment plus System 2 execution: classify the work, select useful philosopher-inspired reasoning instructions (PA), choose an eligible model and native effort level, execute with tools where supported, and verify the result. Optimize latency and resource use subject to quality, safety, and user constraints. Cost efficiency is an outcome to demonstrate, not a promise from choosing cheaper models.

Initial production workload: analytics/data engineering and repository development tasks. Broader domains need separate evaluations. Match essential harness behaviors rather than claim complete feature parity with Pi, Claude Code, Codex, and OpenCode.

Default decisions:

- Automatic model selection stays within the configured provider and an approved model pool. Cross-provider routing is a separately approved option because it changes data disclosure, authentication, billing, and capabilities.
- Explicit model and effort pins are hard constraints. An incompatible pin produces an explanation/choice, not a silent override.
- Quality-first is the default. A later balanced mode must disclose its measured trade-off. No fabricated confidence or model quality rankings.
- Model tiers are configurable roles, not hardcoded names or version assumptions. Catalog IDs and account eligibility determine actual candidates.
- Provider-owned loops and Dennice-owned loops are distinct. Unsupported capabilities remain visibly unsupported.
- No automatic subagent fan-out, permission escalation, paid-provider fallback, or external write authority follows from cognitive routing.

## 2. Historical baseline and original gaps

The table below records the baseline inspected before implementation, not today's
completion status. Current additions include native Codex app-server thread,
approval and observed-tool events; Claude native resume/effort/result validation;
owner-lock crash recovery and explicit effect reconciliation; partial/cumulative
usage ledgers; Windows CI configuration; offline outcome-report infrastructure;
session-scoped workspace directories; selectable chat; and bounded file search,
replacement previews and editing. See README and the recovery/platform documents
for current contracts. Exact Claude interactive approval bridging, real provider
compatibility, actual Windows qualification, and measured held-out quality/cost
results remain unverified. No competitor-parity claim follows from offline tests.

| Area | Repository evidence | Gap to close |
| --- | --- | --- |
| Cognitive routing | `core/harness.py`, `routing/openjev.py`, cognition registry/composer | Cognitive demands select PA, but no adaptive model/effort policy; follow-up context is not sent to OpenJev. |
| Model execution | `executors/codex.py`, `claude.py`, `api.py` | Fixed executor; CLI adapters do not expose a complete tool/approval/usage contract. API/local mode lacks an agent tool loop. Claude effort is guidance rather than native enforcement. |
| Durability | `runs/store.py`, `core/harness.py` | Trace JSON saved at finalization; not an in-flight event journal or crash-recovery system. |
| Isolation | `core/harness.py`, `tui/app.py` | Shared mutable configuration and `_last_trace`; sessions are in memory. Events need stable session ownership. |
| Security | `routing/openjev.py`, API transport, TUI terminal mode | Router transport lacks API adapter's equivalent host/redirect protections. `!` has independent user shell authority; command text currently enters later model history despite its stated isolation. |
| Evaluation | `benchmark/runner.py`, `benchmark/metrics.py` | Cognitive-label agreement is measured, not verified task success or adaptive-routing quality/cost. |

These are design findings, not changes made by this plan. Security/isolation fixes should precede enabling autonomous routing.

## 3. Architecture

```text
User turn + relevant session state + explicit constraints
                    |
         Context/privacy boundary
                    |
   System 1: typed TaskAssessment (Jev/OpenJev/other)
                    |
   Deterministic RoutingPolicy + capability catalog
                    |
   RoutePlan: PA + provider/model/effort + verification plan
                    |
   System 2 adapter -> permitted tools -> evidence/checkpoint
                    |
     Verify -> finish / clarify / bounded escalation

Durable events, permissions, budgets, and cancellation span all steps.
```

### TaskAssessment: what the work requires

Versioned fields: task family, primary/supporting cognitive demands, complexity, stakes, uncertainty, required tools/modalities, relevant context references, missing information, and assessment provenance. Scores must be finite, validated, and explicitly uncalibrated until held-out calibration exists. Cognitive-label confidence is not the probability of successful execution.

Jev returns assessments, not executable instructions, URLs, permissions, arbitrary model IDs, or commands. Unknown/out-of-domain assessments may abstain. A follow-up such as “do the same in production” uses relevant prior decisions and production-risk metadata; it must not be classified from that sentence alone.

Build a bounded contextual brief with provenance. Keep secrets and unnecessary attachments out of router payloads. Router and executor disclosure are separate settings: a local executor does not imply a local router. Local-only mode disallows remote classification and execution. Classification may use a validated fast path or cache, but only after measurement; cache keys must include relevant context, assessment/policy versions, and constraint changes.

### RoutingPolicy: which eligible execution route to use

Filter candidates before ranking: authenticated availability, provider boundary, model pin, context length, input modalities, tool support, structured-output needs, native effort support, privacy policy, permissions, budget, and user-approved pool. Preserve unknown capabilities as unknown rather than guessing from names.

Rank remaining candidates using measured task-family performance and end-to-end latency/resource use. Start with auditable rules and configurable tiers; do not begin with an opaque learned optimizer. Store the assessment, candidate exclusions, selected route, effective settings, policy version, and a short user-readable rationale.

Illustrative behavior, subject to evaluation:

- Bounded, low-risk explicit work: eligible lightweight model, minimal effort, often no PA.
- Multi-step work: eligible balanced model with a small relevant PA selection.
- Ambiguous, high-stakes, or failed-verification work: clarify or use a stronger eligible route plus stricter verification/human approval as appropriate.

A stronger model cannot substitute for authority, missing evidence, or independent verification. If no candidate satisfies the constraints, stop and explain. Never silently switch a subscription user to a billable API.

### PA composition

Keep a versioned policy registry with provenance, evaluation results, and token cost. Permit no PA; cap supporting policies; resolve conflicts; preserve user instructions and safety hierarchy. PA provides concise task-appropriate procedures, not a requirement to reveal hidden reasoning.

The supplied Harb et al. paper evaluated fixed philosophical prompting in chemistry. Dennice's analytics adaptation, dynamic PA selection, and adaptive model routing are extensions requiring independent validation—not paper-established results.

### Provider adapters and loop ownership

Define a conformance contract: catalog/capabilities, native effort, streaming, usage, errors, interruption, approvals, tool events/results, attachments, and session resume. UI capabilities must reflect the adapter's actual implementation.

- Codex: evaluate its documented app-server thread/turn/model/approval interface for persistent provider-owned execution. Avoid relying on internal debug/catalog protocols. Treat experimental endpoints as experimental.
- Claude: review the chosen integration and authentication against current terms. An unmodified Claude Code binary with a user's own sign-in has documented conditions; offering Dennice's own Claude.ai login or intermediating subscription credentials is not equivalent. Agent SDK integrations should use permitted API/cloud authentication unless separately approved. Never collect provider session tokens.
- OpenAI/Anthropic API and local endpoints: implement a Dennice-owned streaming tool loop only for models with validated capabilities. Keep existing chat-only mode explicitly labeled until then.

Do not surround a provider's autonomous tool loop with a second unbounded loop. Re-routing occurs at observable, safe checkpoints; use structured handoffs when changing a model or adapter. Canonical session state remains in Dennice, but provider-native sessions retain their own identifiers and capability limits.

### Execution controller and verification

State machine: assess -> select -> execute -> await approval/tool -> verify -> complete, clarify, or bounded escalation. Also record cancelled, interrupted, timed-out, failed, and awaiting-input states distinctly.

Define task-specific completion criteria before execution. Prefer executable tests, SQL fixtures/invariants, artifact checks, and user acceptance over a model grading itself. A verifier may report uncertain or unmet criteria; a plausible response is not proof of success. High-stakes actions need appropriate human gates.

Escalate for failed checks, conflicting evidence, newly discovered stakes, unavailable capabilities, or context exhaustion. Bound attempts, elapsed time, model tokens/spend, tool calls, and router/verification overhead. Use hysteresis to prevent route oscillation. Never replay a write/action whose external outcome is unknown.

Checkpoint handoff includes objective, decisions, evidence/artifact references, pending work, authority, completed side effects, and uncertainty. Model switches must not lose matched tool-call/results or grant additional permissions.

### Hooks

Add a versioned hook service with explicit lifecycle events: session start/resume, before/after assessment and route selection, before tool execution, tool success/failure, before/after verification, turn completion, cancellation, and goal-state changes. Examples include checking a patch before application, recording telemetry, and validating a completed artifact.

- Separate observational hooks from blocking validation hooks. Define typed inputs/results, deterministic order, timeouts, resource limits, and error policy. A required validation hook that fails or times out blocks the dependent action; optional telemetry failure is surfaced without silently changing execution.
- Hooks may deny an action or request user approval, but cannot grant permission, broaden the approved provider pool, bypass model pins, or override privacy/security policy. Any supported argument transformation is schema-validated and goes through permission checks again before execution.
- Require explicit trust/enablement for executable hooks. Project configuration cannot silently run arbitrary scripts when a repository is opened. Scope credentials and filesystem/network access; use argument arrays rather than interpolating untrusted event content into shell commands. Never expose secrets or private reasoning in event payloads by default.
- Persist hook identity/version, outcome, duration, and redacted diagnostics with originating session/run/tool IDs. Bound recursion; hooks do not trigger themselves indefinitely. Retries/recovery must not duplicate side-effecting hook execution; unresolved effects require reconciliation.
- Distinguish native provider hooks from Dennice-owned hooks. An adapter must declare which events it actually observes and whether it can block the action. Avoid double execution when a native hook and harness hook cover the same event; do not advertise enforcement after an action has already happened.
- Expose configuration and `/hooks` for listing, inspecting, and explicitly enabling/disabling hooks. Security policy cannot be disabled through this extension mechanism.

### MCP

Add an MCP connection manager and broker integration, not just a setup toggle. Planned support includes local stdio servers and remote Streamable HTTP servers, version/capability negotiation, namespaced tool discovery, and explicit resource/prompt access where supported. Validate protocol compatibility against the official specification during implementation.

- User-managed server configuration covers command/arguments or endpoint, enabled state, environment-variable references, authentication, and approved tools. Never start a project-supplied server or transmit credentials solely because its configuration was discovered.
- Setup and `/mcp` show server status, discovered capabilities, connection tests, and explicit enable/disable/reconnect controls. Connection tests must disclose server startup/network access and avoid arbitrary tool invocation or billable inference.
- Apply per-server/tool approval policy through the same authority broker as built-in tools. Schema validation and read/destructive annotations assist assessment but are not trusted proof of safety. Local stdio server processes themselves require scoped execution; tool approvals alone do not sandbox a malicious server.
- Separate server credentials from model-provider keys; scope remote authentication to the intended origin, validate redirects, redact logs, and respect local-only mode. Tool results, resources, and prompts are untrusted content, not instructions that grant authority.
- Bound discovery/results, concurrency, timeouts, and retries. Support cancellation, server crash handling, capability-list changes, and process cleanup. Never automatically retry a non-idempotent tool call with an unknown outcome.
- Keep native provider MCP ownership separate from Dennice-owned API/local MCP execution. Advertise only tools the effective model/adapter can actually use; avoid duplicate connections and approvals. Discovery does not authorize sending private context to a server.
- Record tool-call/result provenance and approval decisions in the durable event journal. Test malicious server content, tool name collisions, changed schemas, credential leakage, disconnects, and interrupted side effects using local fixtures.

### Sessions, tools, skills, and goals

- Durable session/run/turn/tool IDs; immutable execution configuration snapshot per turn. Events remain tied to the originating session when tabs change. Return per-run traces, not shared last-trace state.
- Transactional event journal (proposed SQLite with migrations), append during execution, resume/checkpoint support, retention/export, private storage and redaction. Crash recovery reconciles pending effects rather than rerunning them automatically.
- Tool broker for scoped read/search/patch/shell, then MCP. Enforce paths including symlinks, sandbox/network policy, server trust, credential scoping, timeouts, output/resource limits, and auditable approvals. Denied/unsupported sandbox enforcement must fail closed.
- Skill discovery is not execution authorization. Record origin/version/trust, validate dependencies and provider compatibility; skill and tool content cannot override permissions.
- `!` is explicitly user-initiated terminal authority, distinct from agent permissions. Label it clearly; keep command text/output out of future model context unless the user intentionally includes them. Provide cancellation and cross-platform process cleanup.
- `/goal` is a bounded controller over this execution system, not infinite recursive prompting. Support objective, completion criteria, status, pause/resume/cancel, budgets, and honest blocked/awaiting-input states. Durable session history, `/new`, `/resume`, `/rename`, tabs, multiline input, and interrupt behavior must work independently of goal mode.
- Terminal status shows configured and effective model, effort, permission mode, routing state, tool activity, and actual/estimated usage. Show approvals and meaningful progress without exposing private reasoning.
- Windows support requires tests under Git Bash and a documented supported environment; shell availability alone does not establish sandbox/process parity. Disable write-capable automation where authority cannot be enforced.

## 4. Implementation sequence and acceptance gates

Evaluation and adversarial fixtures begin in phase 0 and continue in every phase.

| Phase | Deliverables | Acceptance gate |
| --- | --- | --- |
| 0: baseline and boundaries | Inventory existing features; adapter capability matrix; threat model; representative task suite; safe router transport; terminal-history disclosure fix; integration/auth review. | Preserve existing work; no credential forwarding on redirects; local-only cannot egress; tests document current limitations. |
| 1: reliable core | Session service, immutable per-turn config, durable event store, stable event ownership, cancellation/process cleanup, usage accounting and adapter contracts. | Restart preserves sessions; switching/closing tabs never misroutes output; cancellation ends child work; unknown-effect recovery never blindly retries. |
| 2: System 1 and routing policy | Context-aware assessment schema; optional/versioned PA; capability-backed deterministic candidate selection; explainability; pins/privacy constraints. Shadow mode only. | Schema/adversarial tests pass; pins and boundaries respected; unavailable model/effort rejected; decisions logged without changing execution. |
| 3: bounded automatic routing | Opt-in model/effort routing within one approved provider; structured handoff; bounded escalation at safe checkpoints; recommendation/fixed/auto controls. | End-to-end vertical slice on read-oriented analytics/repo tasks beats the chosen efficiency baseline while meeting preregistered quality gates. |
| 4: production execution | Supported provider event/approval/resume adapters; native API/local tool loop; scoped broker; typed hook events and blocking validation; independent verification; bounded goals. | Tool/approval/interrupt contracts tested; hook failures cannot bypass required checks; no duplicate writes/hooks on retries/crashes; completion backed by evidence; unsupported modes remain labeled. |
| 5: extensibility and terminal polish | MCP connection manager and tool integration; hook configuration/UI; MCP/hook/skill trust controls; durable session UX, native model/effort menus, attachments/context controls, Windows packaging and Git Bash CI. | Adapter-specific capability disclosures; hook/MCP/skill injection and lifecycle tests; no unapproved project-script/server execution; installation and lifecycle tests on Windows/macOS/Linux. |
| 6: release qualification | Held-out evaluations, canary rollout, migration/rollback, compatibility docs, observability and support runbooks. | Publish results and known limits; enable default auto-routing only when gates hold; fixed-route rollback remains available. |

First implementation milestone: phase 0 + phase 1, not more model-picker UI. First adaptive milestone: a complete assessment -> route -> execute -> verify slice with shadow/opt-in controls. Broader tooling and cross-provider optimization should not delay testing the core hypothesis, nor bypass foundational safety gates.

Proposed module boundaries: extend `core/models.py`/configuration with typed assessment and route plans; separate routing policy/capabilities from router transport; add session/event and execution-controller services; strengthen executor adapters; add a tool/approval broker, hook service, and MCP connection manager; keep Textual as presentation and user-intent handling. Reuse existing taxonomy, policies, configuration and test fixtures; avoid a wholesale rewrite of the large TUI module in one change.

## 5. Evaluation: demonstrate the routing benefit

Compare fixed strong, fixed inexpensive, and user-selected baselines on identical tasks/tools/context. Ablate no PA, fixed PA, routed PA; fixed model versus routed model; and combined routing. Include an oracle diagnostic, not a production baseline. Separate tuning, calibration, and untouched test sets.

Measure verified task success, critical failures, user corrections, per-family results, time to verified completion, p50/p95 latency, total model/router/verifier tokens, retries, tool calls, and cost per verified successful task. Distinguish provider-reported usage from estimates, API money from subscription quota, and local hardware/latency from “free.” Include PA overhead and routing overhead.

Test ambiguous follow-ups, long context, vision, tool-result injection, invalid assessments, provider outages, authentication/effort mismatch, rate limits, interrupted writes, process crashes, corrupt/migrated stores, concurrent tabs, and platform-specific permission enforcement.

Hook/MCP release tests additionally cover deterministic hook order, timeout/fail-closed behavior, changed-argument reauthorization, recursive triggers, recovery without duplicated effects, untrusted repository configuration, server reconnect/capability changes, and native-versus-harness ownership. Include their overhead in end-to-end performance measurements.

Before rollout, preregister workload-specific thresholds and uncertainty intervals. A proposed starting discussion is non-inferiority to a fixed strong baseline within a small agreed margin, with efficiency improvement; choose the margin and sample size before inspecting outcomes. Require zero observed unauthorized-action failures in the release security suite, while acknowledging that passing a suite is not proof of universal security. Critical/high-stakes categories need separate gates, not an aggregate average that hides failures.

Rollout: shadow -> recommendation -> opt-in bounded auto -> default only after evidence. Preserve deterministic rollback and version all assessment prompts, PA, routing rules, adapters, and eval datasets.

## 6. Independent critique and decisions incorporated

The requested `critique_sub_agent_ai_engineer` reviewed the repository and proposed architecture read-only. Its main objections and their resolution:

1. A model picker is not a production harness: durability, authority, loop ownership, and adapter conformance now precede automatic routing.
2. Jev must not control executable routes/permissions: typed assessment is separated from deterministic policy and capability filtering.
3. Router transport/context gaps: phase 0 hardens endpoints/redirects and phase 2 adds privacy-aware follow-up context.
4. Shared state and final-only traces: phase 1 establishes immutable run ownership and an in-flight journal.
5. CLI/native API behavior is not equivalent: capability matrix and separate loop implementations are release requirements.
6. Current benchmarks cannot substantiate quality gains: outcome-based evaluation and PA/model ablations are mandatory.
7. Subscription auth is not a subprocess loophole: integration-specific terms review is a release gate; use approved documented paths.
8. Routing every tiny turn can be inefficient: validate fast paths, caching, and total overhead before claiming savings.

User-requested addition after critique: hooks are now an explicit subsystem, and MCP has a connection/tool lifecycle specification. These extend the critique's authority and observability recommendations; they were not separately named hook requirements in the original review.

Remaining product decisions before their respective phases: quality/latency tolerance by workload; approved model pools and privacy defaults; initial tool/sandbox scope; any cross-provider consent; and compliant Claude integration choice. Recommended defaults above allow foundational work without assuming these expansions.

## 7. Primary references and research limits

- [Codex app-server](https://learn.chatgpt.com/docs/app-server): documented threads, turns, model capabilities, events, and approval surfaces; individual experimental methods require explicit compatibility handling.
- [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) and [legal/authentication conditions](https://code.claude.com/docs/en/legal-and-compliance): agent integration and authentication boundaries; distinguish native user sign-in from third-party credential intermediation.
- [Pi upstream README](https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/README.md): an intentionally extensible harness with interactive/programmatic surfaces; not a mandate to copy a union of every competitor's features.
- [OpenCode permissions](https://opencode.ai/docs/permissions/): granular permission controls and loop safeguards are useful behavioral benchmarks, not defaults to copy unquestioningly.
- Supplied paper: Harb et al., *The ballad of LLM agents: philosophical reasoning for chemistry*, [DOI](https://doi.org/10.1088/2632-2153/ae792d). Its fixed-prompt chemistry evidence does not validate Dennice's adaptive multi-turn orchestration.

Success means demonstrably better quality-preserving orchestration on a declared workload, with reliable user control—not an unsupported claim to surpass all harnesses or guarantee optimal model choice.
