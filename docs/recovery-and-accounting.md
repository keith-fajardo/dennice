# Recovery and accounting contract

## Process ownership, not time or PID guesses

Every new Harness run claims an advisory lock before its first durable event.
POSIX uses `flock`; Windows uses a one-byte process-lifetime file lock. A second
process cannot claim/checkpoint that live run or mark it interrupted. PID metadata
is diagnostic only: age, PID reuse, or the fact that another window opened do not
prove that a process is dead. Locks remain on disk and are never unlinked while
another opener could hold their inode; descriptor close/process exit releases them.

`await store.recover_stale()` changes only registered `running` runs whose locks
can be acquired. It appends an interruption event, preserves original events and
output, and never starts a model, tool, hook, command, or MCP server. Legacy traces
without ownership registration are not automatically declared dead. Recovery does
not kill orphaned external processes or prove whether an interrupted action happened.
The database migrates version 1 to version 2 without deleting sessions, goals,
traces or event rows; newer schemas fail closed. Append-only event checks remain
transactional, including during recovery and reconciliation.

## Unknown effects and explicit reconciliation

Native tools, hooks and verification commands persist unique operation intent
markers before launching. Native provider observed tool events accept provider
effect IDs; their markers describe observation, not a promise of pre-action
permission enforcement. Read/list operations are not treated as mutable effects.
A matching completion event closes the intent. Any start without completion remains
pending while live and **unknown** after failure/cancellation/interruption.

`await store.inspect_recovery(run_id)` returns unresolved operation IDs and provenance.
After inspecting external evidence, the user may call
`await store.reconcile(run_id, operation_id, outcome, notes)`, where outcome is
`completed`, `not_executed` or `compensated`. Evidence notes are mandatory and bounded.
This appends an immutable explicit-user resolution; it does not retry anything,
change the run to success, reverse an action, or grant permission. Live runs and
duplicate/unknown operation resolutions are rejected. A required hook failure is
an observed failed hook outcome, not proof its script made no modifications.
Timed-out/cancelled hook outcomes remain unknown even after process cleanup. Hook
metadata omits tool arguments by default; a validation hook may explicitly opt in
with `include_arguments: true` (this changes its trust fingerprint). Inputs are
bounded and do not include full prompts/private reasoning by default.

## Usage telemetry and budgets

Every checkpoint derives `trace.accounting` from durable model-attempt/usage events.
It separates router and executor records, retains provider/model/source and attempt
IDs, replaces cumulative snapshots rather than summing them twice, and validates
nonnegative integer tokens plus finite reported cost/currency. Missing fields and
interrupted requests remain null/unknown. Known-token subtotals are reported separately
from complete totals. A deterministic rule classifier is explicitly `no_model_call`,
not fabricated provider-reported zero usage. Hooks/router/tools have observed elapsed
spans; incomplete spans remain visible.

Cache-read/write tokens are retained separately. Anthropic raw input counts exclude
both cache categories; normalized totals add them once. OpenAI cached input is
already a subset of total input and is not added again. Adapters explicitly mark
whether their input total includes caches, following the
[Anthropic cache usage contract](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

`trace.result` contains executor-only totals, null when incomplete. Goal accounting
charges known router/executor subtotals and applies remaining budgets on continuation.
Provider usage can arrive only after a call, so token limits stop subsequent calls
and are not an exact preflight spending guarantee. No money estimate is derived from
model names or subscription quota. Missing router/native-provider usage and service
cost keep end-to-end totals unknown. Reported token/cost fields are not guarantees of
an invoice's completeness; cloud services invoked by user-approved commands require
their own billing records. This is truthful telemetry, not a claim that unreported
provider usage can be reconstructed.

Offline experiment reporting and Windows fixture/CI boundaries are documented in
[platform-and-evaluation.md](platform-and-evaluation.md). Actual held-out evaluation
and Windows hosted CI execution remain release gates until their results exist.
