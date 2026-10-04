# Extension implementation and qualification

The following code is implemented and covered by offline tests. This is not a
production certification or evidence of improved model quality/cost.
The [2026-10-04 local qualification record](local-qualification-2026-10-04.md)
separates observed evidence from release gates that remain open.

| Surface | Supported scope | Important boundary |
| --- | --- | --- |
| Setup hook/MCP editors | Staged JSON arrays, disabled examples, validation, explicit save/cancel | No automatic trust, script launch, connection, credential lookup or tool call |
| API/local MCP | Allowlisted tools, exact resource URIs, named prompts and declared string arguments | Read-write plus exact-call approval; server processes themselves are trusted, not sandboxed |
| MCP discovery | Capability negotiation, bounded pagination, no content fetch during discovery | No sampling, subscriptions, resource templates, automatic links or server-to-client permission escalation |
| Claude pre-tool gate | Restricted Read/Glob/Grep/Edit/Write; exact edit approval and configured before-tool hooks | No Bash, interactive question tools, native MCP injection, project hooks or persistent grants |
| Claude post-tool hooks | Observed result notifications, deduplicated by call ID | An after-tool denial stops further work, not an action already performed |
| Codex pre-tool hooks | Native command/file approval requests | No interception guarantee for tools that do not request approval |
| Codex post-tool hooks | Observed item completion, deduplicated by item ID | Does not replace Codex's native sandbox/approval semantics |

Claude Code must support `--restricted`, inline `--settings`, `PreToolUse`
decision output and `--system-prompt-snapshot off`. The invocation requires
2.1.257 or later; version alone is not compatibility qualification. CLI failures
are reported, not retried with weaker permissions. The bridge is run-local,
loopback-only and authenticated with an ephemeral random token. The hook helper
uses the current Python executable, isolation (`-I`), an absolute package path,
bounded stdin/response sizes and no project imports. Transport failure exits 2,
which blocks the tool. Edit/Write are not listed in `--allowedTools`; `dontAsk`
does not grant unattended file-edit permissions. Managed administrator policies
still apply and must be included in deployment qualification.

Bridge listener startup failure aborts execution. This development sandbox does
not allow binding a listener, so current tests exercise authenticated HTTP parsing
and decision/transport contracts without opening sockets. A real local listener
and real CLI round-trip remain an explicit live qualification gate. Filesystem
checks reject parent traversal, sensitive names, symlinks and hardlinked files,
and gated Grep requires an explicit regular file rather than a directory-wide
content scan that could include hidden credentials. Glob patterns must be relative.
The actual read/edit is provider-owned: this is not an OS-level TOCTOU sandbox.

The local Codex stdio fixture in `tests/test_codex_stdio_integration.py`
exercises the executor and JSON-RPC transport together. It covers auth
preflight, approval/denial, session resume, and unfinished-turn interruption.
It also guards against interrupting a completed turn. This does not replace
qualification against an installed Codex CLI.

The local Claude CLI-shaped subprocess fixture in
`tests/test_claude_stdio_integration.py` covers auth preflight, completed and
incomplete terminal results, native resume, and deterministic child cleanup
when an event stream closes. It checks both plan and write-profile cleanup;
the write-profile test substitutes a gate because the development sandbox
cannot bind the live bridge. It does not replace real Claude hook and
approval qualification.

The local stdio qualification fixtures in `tests/test_mcp_stdio.py` and
`tests/test_mcp_stdio_faults.py` exercise the SDK client transport against real
subprocesses. They cover tool-only, resource-only and prompt-only capability
shapes; allowlisted calls; pagination cycles; unexpected and substituted
resource URIs; external schemas and duplicate prompt arguments; oversized
results; tool errors; server disconnect; and a timed-out call without retry.
The MCP client preserves the original operation error if SDK transport cleanup
also fails. This is local macOS evidence only, not a result for the configured
CI matrix or a real project server.

## Required release evidence

1. On an explicitly authorized local test workspace, verify Claude Read-write
   Deny leaves the file unchanged; Approve edits only the displayed target; bridge
   failure denies the edit; cancellation closes owned processes/listeners; resumed
   turns receive new PA instructions. Never use private files as fixtures.
2. Verify Codex native approval, denial, resume and cancellation against the
   supported installed CLI. Test native MCP independently rather than assuming
   Dennice-owned MCP approval policy applies to it.
3. Exercise a purpose-built MCP fixture server: tool-only, resource-only,
   prompt-only, pagination cycles, unexpected URIs, oversized results, schema
   errors, timeout and server disconnect. Do not automatically replay uncertain
   operations or launch a project's configured server as a test fixture.
4. Observe the configured Linux/macOS/Windows Git Bash CI matrix. Native Windows
   descriptor-confined file tools remain unsupported and fail closed; WSL is the
   supported option. Record this limitation rather than labeling every tool
   Windows-compatible.
5. Run authorized held-out comparisons with independent completion checks and
   critical-failure grading, matched tasks, controlled permissions/workspaces and
   an explicit spending budget. Report missing router/service cost as unknown.
6. Verify the user's own provider account terms and live billing behavior for
   the selected personal-local-use scope. The
   [2026-10-04 primary-document mapping](provider-integration-review-2026-10-04.md)
   is complete for this scope; fixture tests do not establish live auth or
   billing. OpenAI API calls were excluded at the user's request. Any hosted,
   shared-credential, or distributed CLI deployment requires a fresh review.
   Delegating authentication to user-owned CLIs is not a legal/compliance
   certification.

No model inference, configured project hooks/MCP servers, remote CI dispatch,
subscription authentication changes or paid evaluation runs were performed to
implement these extensions. CLI version/help/schema checks and one failed
no-inference Codex app-server handshake are recorded separately.

## Protocol references

- [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Claude hook input, output and decision control](https://code.claude.com/docs/en/hooks)
- [Codex app-server approvals](https://learn.chatgpt.com/docs/app-server#approvals)
- [MCP resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)
- [MCP prompts](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts)
