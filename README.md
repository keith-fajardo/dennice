# Dennice

Dennice is a cognitive routing and evaluation harness for data analytics agents.
It classifies a task's cognitive demands before execution, resolves versioned
reasoning policies, and records reproducible local run traces.

## Development

```bash
pip install -e ".[dev]"
dennice
dennice classify "Why did Snowflake credits increase yesterday?" --json
dennice benchmark list
dennice benchmark run --mode router --json
```

The deterministic rule router remains the default for reproducible offline
tests. Press `Ctrl+S` in the TUI to configure the two layers independently:

- **System 1 cognitive router:** Rule (offline), Codex CLI, hosted Jev, or a
  local OpenJev-compatible typed-decision server.
- **System 2 executor:** Mock (offline), Codex CLI using your ChatGPT/Codex
  login, Claude Code using your Claude subscription/login, or GitHub Copilot CLI
  using your GitHub Copilot subscription/login.

Dennice does not store provider credentials. The Codex adapter runs with a
read-only sandbox by default. Both CLI adapters expose Read-only, Read-write,
and Plan profiles. Copilot also exposes these profiles with read/file-write
tools only (its Dennice adapter does not enable shell or MCP). Codex Plan combines a read-only sandbox with planning guidance
(not its interactive native Plan UI). Claude Plan uses native plan mode; its
other profiles restrict tools to Read/Glob/Grep, adding Edit/Write only for
Read-write. Bash and MCP tools are not enabled in these Claude profiles, and
unapproved operations are denied rather than silently bypassing permissions.
Claude restricted execution requires CLI 2.1.248 or newer; older installations
must be upgraded. Mock is available under **Advanced · offline testing** in Setup
and produces simulated responses only. Both receive the prompt composed from the selected cognitive
policies.

Within the TUI, use `Ctrl+N` or `/new` to create a session. Open sessions stay
available as workspace tabs; click a tab or use `/session <number>` to return to
one. Sessions and transcripts survive restarting Dennice in the same workspace.
Use `/sessions` to search open and archived sessions by title or message content.
The opening page also lists all saved sessions with clickable entries and a live
regex search over titles and messages. Up/Down and Enter work from that search
field; `Ctrl+A` selects its complete query.
Results update as you type; search supports case-insensitive regex (for example,
`snowflake|billing` or `cost.*spike`). Use Up/Down and Enter to open a result;
clear the search to show all sessions. Invalid or expensive patterns show an
error instead of freezing the picker. Search stays local.
Use `×` to archive an open tab without deleting its history, `/sessions` to open
an arrow-key/Enter picker containing open and archived sessions, and
`/rename <title>` to name the current session. `/config` prints the active
executor, model, effort, permission mode, and router.

The composer accepts multiple lines. Press `Ctrl+Enter` to submit, `Ctrl+A` to
select the complete draft, and use Up/Down at the top or bottom of the composer
to replay submitted tasks and slash commands. Prefix a direct local Bash command
with `!`, for example `! git status --short`; Dennice labels its captured output
as **Terminal** and keeps it out of agent conversation history.

Use `/model` to select a System 2 model or type `/model <name>` for a direct
override. Setup provides the same selector. Both screens automatically load
the configured provider's model catalog; use **Refresh available models** to
reload it. Claude labels show the CLI-reported version and resolved model ID,
not just an unversioned family alias. If discovery fails, a warning appears and
CLI aliases/custom entry remain available. That keeps model availability
account-specific rather than claiming that every OpenAI API model is usable by a
given ChatGPT subscription. In Setup, **Test executor** and **Test router**
independently check their respective unsaved choices and report the provider
tested and its result without changing `dennice.yaml`. Choosing Claude as the
executor leaves the router choice independent; Codex or Jev may still route
tasks before Claude executes them.

Press `Escape` to close Setup without saving changes.
Use Up/Down to navigate Setup controls and Left/Right to navigate button rows.
Open dropdowns retain their own arrow-key selection; text fields retain
Left/Right cursor editing. Claude catalog labels retain model names and resolved
IDs instead of displaying only model descriptions.

### Reliability milestone and implementation status

Run checkpoints and append-only events are stored transactionally in
`.dennice/runs/runs.sqlite3` (or the configured runs directory). Legacy JSON
traces remain readable and are not removed. Each event is committed before it
is displayed; incomplete runs remain inspectable after a crash. They are not
automatically resumed or replayed. Run configuration/context is snapshotted at
start, so changing settings does not alter an in-flight execution.

Press `Ctrl+X` to stop an active model run or terminal command. Cancellation
does not undo external effects. Sessions retain partial responses and cancelled
runs retain a cancellation event. A second execution is rejected while one is
active rather than silently cancelling it. POSIX subprocesses use owned process
groups; Windows cleanup uses `taskkill /T` and still needs Windows CI validation.

The database contains plaintext prompts, transcripts, image-path references,
configuration references, and output. Unix creation modes are private, but this
is not encryption or a Windows ACL guarantee. Treat traces as sensitive data.
Hosted Jev requires the official HTTPS host; OpenJev requires loopback HTTP(S).
Router redirects and implicit proxies are disabled, responses are size-bounded,
and invalid probability scores are rejected before execution.

The [production-harness plan](docs/production-harness-plan.md) is partially
implemented: fixed/shadow/opt-in automatic routing, API tool loops, hooks, MCP
management, completion checks and bounded goals are available. They are not a
claim of production qualification or measured quality/cost improvement. Fixed
routing remains the default. Native session linkage, observed CLI tool events,
crash-effect reconciliation, usage accounting, staged hook/MCP setup editors and
Claude exact file-edit approval bridging are implemented. Live bridge compatibility,
held-out outcome results, compliant integration review, and
actual cross-platform qualification remain release gates.

Codex execution uses the documented local `codex app-server` stdio protocol
(conformance fixtures target CLI 0.160.0): native threads resume within the same
Dennice session/workspace, PA instructions update each turn, command/file
approval requests require explicit consent in Read-write, and unknown privilege
requests fail closed. Catalog refresh uses `model/list`, not an undocumented
debug command. Claude uses documented native `--resume`, `--effort` and
`--max-turns`; unsupported effort/model combinations are reported by the provider.
Claude retains its restricted file-only tool profiles. Read-write uses a run-local,
authenticated loopback bridge with documented `PreToolUse` command hooks; Edit/Write
are not pre-authorized and require an exact-call confirmation. Bridge startup
failure stops the run; transport failure denies the tool. It does not enable Bash,
MCP, or arbitrary project hooks, and does not handle interactive question tools.
Current invocation requires Claude Code 2.1.257+ for per-turn prompt snapshot refresh.
Observed native tool/token limits can stop further
work but do not guarantee an action has not already happened. Provider-owned
hooks/MCP and subagent internals are not equivalent to Dennice's API tool broker.

Interrupted runs are detected using process-lifetime locks, not elapsed-time
guesses, and no action is replayed automatically. Use `/recovery` to list runs,
`/recovery inspect <run-id>` to inspect uncertain effects, and
`/recovery reconcile <run-id> <operation-id> <completed|not_executed|compensated> <evidence notes>`
to record explicit user evidence. Interrupted native threads require inspection
and a new session before another execution. See the
[recovery/accounting contract](docs/recovery-and-accounting.md) and
[platform/evaluation qualification](docs/platform-and-evaluation.md).
Native hook/MCP capabilities and remaining release evidence are listed in the
[extension qualification checklist](docs/extension-qualification.md).

### API and local execution

Setup also offers **OpenAI API**, **Anthropic API**, and **Local**. These are
independent from ChatGPT/Claude subscription logins and the System 1 router.
Hosted API usage has separate API billing. Save only the environment-variable
name in Setup, never the key itself:

```bash
export OPENAI_API_KEY="your-openai-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

Choose the API provider and refresh its model catalog, then select a specific
model. Model catalogs can include models that do not support text/vision or the
chosen effort level; provider errors are shown rather than silently substituting
a different model. Connection tests generate a small response and may incur usage.
Hosted keys are sent only to their official HTTPS API hosts; redirects are refused.

For **Local**, start an OpenAI-compatible server and enter its `/v1` base URL:
Ollama commonly uses `http://127.0.0.1:11434/v1`; LM Studio commonly uses
`http://127.0.0.1:1234/v1`. Load a model in that server, refresh, and select it.
No key is used by default; an optional local key must be explicitly configured.
Local mode accepts loopback endpoints only, not remote hosts.

API/local execution supports streaming chat, recent history, cognitive policies,
vision inputs where supported, and an opt-in native tool loop (`/tools on`).
Read-only/Plan exposes bounded workspace reads; Read-write additionally exposes
exact replacement, argument-array commands, and approved MCP tools. Each mutation,
command, and MCP call needs a separate user confirmation; Deny is the default.
Native filesystem tools fail closed on platforms without descriptor-relative
no-symlink support (including native Windows). Commands, hooks, and stdio servers
are explicitly trusted user processes, **not OS-sandboxed**. CLI executors retain
their own tool loops; Dennice cannot intercept all their actions. Its MCP broker
is available to API/local loops, not injected into native CLI loops.

### Local skills

Use `/skills` to browse Claude and Codex local skills with arrow keys. Selecting
one prepares `/skill <qualified-key> ` in the composer; add your task and submit.
You can also type `/skill <name> <task>` when the name is unambiguous.

Discovery reads project `.claude/skills` and `.agents/skills` up to the repository
root, personal `~/.claude/skills` and `~/.agents/skills`, and legacy
`$CODEX_HOME/skills` (default `~/.codex/skills`). Names are source/scope-qualified
to avoid collisions. Claude `user-invocable: false` and disabled Codex entries
are excluded. This selector is not an exhaustive plugin/enterprise skill catalog.

Codex, Claude Code, and Copilot CLI are told to read the selected manifest and relative resources.
API/local tools-enabled mode includes the explicitly selected, validated manifest
(up to 64 KB) as user context; referenced resources outside the approved workspace
are unavailable, and scripts are never automatically launched. No files are copied,
modified, or installed. Skills remain user-level instructions and do not bypass
permissions. A skill from the other provider may depend on tools/integrations
the selected executor lacks; it must report those limitations. Only select skills
you trust. Provider-native automatic discovery continues independently.
[Codex skills](https://learn.chatgpt.com/docs/build-skills) ·
[Claude skills](https://code.claude.com/docs/en/skills)

### Image context and rendered chat

Press `Ctrl+V` in the composer to attach a clipboard image, or use
`/attach "/absolute/path/image.png"`. Up to eight PNG/JPEG/GIF/WebP images
(10 MB each) can be attached. `/clear-images` removes pending attachments without
deleting source files. Clipboard snapshots are saved under the ignored
`.dennice/attachments/` directory, with no upload until you submit your message.
They remain on disk until you remove them; session tabs keep recent image context.

Codex receives native image attachments; Claude Code is directed to read the
image files; API/local providers receive image content blocks. A vision-capable
model is required. The cognitive router currently receives text only, not image
contents. Images in recent conversation context are sent to the currently selected
executor when you submit a follow-up, including after a provider switch.

Some terminals intercept `Ctrl+V`; clipboard image access also depends on the OS
and desktop session (especially WSL/SSH). Use `/attach` if image paste is unavailable.
Assistant responses render Markdown headings, emphasis, code blocks, links, and
tables; your messages and direct terminal output stay plain text.
Click an HTTP/HTTPS link to open it in your browser; other URI schemes are blocked.
Scroll long conversations using the mouse wheel/scrollbar. When the transcript is
focused, Page Up/Page Down and Home/End navigate it. Streaming updates follow the
bottom only when you are already there, so they do not interrupt reading earlier text.
Drag over chat text to select it, then use `Ctrl+C` while the transcript is focused.
`Ctrl+Shift+C` or **Copy response** copies the selection, or the latest assistant
response if nothing is selected. Clipboard delivery uses the terminal's clipboard
protocol; terminal/SSH settings may require permission. A persistent animated
status line remains visible after interim replies and displays observed tool work.

### Workspace files and editing

Each session persists its working directory and file-search/replace queries.
New sessions and legacy sessions without this field default to Dennice's launch
directory. `/cwd` shows the current directory; `/cwd "path with spaces"` changes
it explicitly (stop active execution first). Switching/reopening a session restores
its tree and queries. **Open folder…** in the explorer lets you browse with arrow
keys/Enter or enter a path, then confirm the folder for just the active session.
New sessions still default to the launch directory until you select a different folder.
File actions, `!` commands, CLI execution, API tools,
verification, hooks and stdio MCP use that session's directory, snapshotted per
execution. Hook/MCP trust granted through the UI is directory-scoped: another
directory requires fresh explicit trust. Git repositories show their branch or
detached-HEAD state; branch inspection never checks out or modifies anything.
The bottom status line also shows the configured executor/model, effort and
permission mode, updating when settings change and wrapping on narrow terminals.
A missing saved directory disables file actions rather than falling back silently.

The session view has a hierarchical file tree. Use arrow keys to navigate,
single-click to select, and double-click a file to open it in Edit mode. Right-click
a file for **Rename**, **Copy path**, **Copy relative path**, **Duplicate**, or
**Delete**; Esc closes the options. **New file** creates an empty file in the
selected folder (or workspace root), without overwriting an existing path.
Drag a file onto a folder to move it; Dennice confirms the move and refuses
destination overwrites. **Delete** requires confirmation and permanently removes
the selected regular file (directories are not deleted from the explorer).

The sidebar's **Explorer** and **Git Changes** tabs are scoped to the active
session directory. Git Changes refreshes automatically and separates working-tree
changes from staged changes. Select a text file in either list for an in-session,
read-only side-by-side comparison (working changes compare index to working tree;
staged changes compare `HEAD` to the index). Git status and diff viewing do not
stage, commit, or otherwise modify the repository.

The editor opens inside the session area, not in a popup. It includes live regex
find, **Replace**, **Replace all in buffer**, undo/redo, and explicit Save.
Replacements stay in the buffer until saved; unsaved edits must be saved or
explicitly discarded before switching files or sessions. Use Ctrl+F for find,
Enter/F3 or Next to advance, Shift+F3 or Previous to go back, and Esc or Close
to return to the conversation.

Content matches in the explorer show
highlighted snippets and line numbers beneath each file (up to three matching
lines); double-clicking a snippet opens the file at that line. **Find** and
**Replace** sit side by side; **Refresh** is below them. Replace searches readable
workspace text and previews replacements in a selected file's editor. Searches
are bounded (256 files, 4 MB, time/match limits); partial results are labeled.
The editor also has file-local regex find/replace (`Ctrl+H`), including `\1`
backreferences. Replacements stay in the buffer until `Ctrl+Shift+S` saves;
`Ctrl+Z` undoes and `Ctrl+Shift+Z` redoes. There is no silent bulk overwrite.

Only project-scoped, non-linked UTF-8 text files up to 512 KB are editable.
Credential-like paths, symlinks, hardlinks and binary files are excluded. Saves
check for external changes, stage in the same directory, and rename atomically
while preserving file mode; this is not a multi-file transaction or filesystem
compare-and-swap against a malicious concurrent writer. Secure file browsing and
editing currently requires POSIX (including WSL); native Windows fails closed.

You can update the executor from the composer with `/effort
<low|medium|high|xhigh|default>` and `/permissions
<read-only|read-write|plan>` (`workspace-write` remains a compatibility alias).

For Codex, Setup also stores a model override and optional reasoning effort in
your local `dennice.yaml`:

```yaml
executor:
  provider: codex
  model: default
  reasoning_effort: high
router:
  provider: openjev
  model: openjev
openjev:
  endpoint: http://127.0.0.1:3000/v1/systemone
  model: openjev
jev:
  api_key_env: JEV_API_KEY
  endpoint: https://api.typesafe.ai/v1/systemone
  model: jev-latest
```

Use `default` for either setting when you want Codex to choose its configured
default. Model availability and supported effort levels depend on your Codex
account and selected model.

For hosted Jev, select **Jev · hosted** in Setup. Setup stores only the
environment-variable name (default `JEV_API_KEY`), never the secret. Export the
value in your shell before starting Dennice. The first shipped local router is
**OpenJev · local**, which uses only the configured local endpoint and needs no
API key. Both use the `/v1/systemone` typed-decision protocol rather than a task
solution. [TypeSafe’s official Jev quickstart](https://docs.typesafe.ai/introduction/quickstart)
and [OpenJev’s documentation](https://huggingface.co/openjev/openjev) describe
that contract.

### Local System 1 router (OpenJev-compatible)

Dennice can run its cognitive router entirely locally. This is a **System 1
decision layer**, not the chat/execution model: Dennice sends a task plus typed
`choice` and `noul` questions, receives task-family and cognitive-demand
scores, then gives the resulting policy configuration to the selected executor.

Any server implementing Jev's `POST /v1/systemone` contract can be used. For
example, the lightweight [lookski/openjev](https://github.com/lookski/openjev)
server can be started from Git Bash, macOS, or Linux as follows:

```bash
git clone https://github.com/lookski/openjev.git
cd openjev
python -m pip install -e .
openjev serve --port 8771
```

In Dennice, press `Ctrl+S`, select **OpenJev · local**, and enter:

```text
Endpoint: http://127.0.0.1:8771/v1/systemone
Model:    openjev
```

Then select any System 2 executor (Mock, Codex, Claude Code, or GitHub Copilot CLI) and save. The
default Dennice endpoint is `http://127.0.0.1:3000/v1/systemone`, matching the
official OpenJev model-card helper setup; change it to `:8771` for the example
above. That official setup uses a local vLLM server plus a System One helper and
is appropriate when you want to serve its model directly. [Its serving guide](https://huggingface.co/openjev/openjev/blob/main/serve/SERVE.md?code=true)
has the complete commands and resource requirements.

Before configuring Dennice, you can verify an OpenJev-compatible server with:

```bash
curl -s http://127.0.0.1:8771/v1/systemone \
  -H 'Content-Type: application/json' \
  -d '{"model":"openjev","state":"Snowflake spend increased yesterday.","questions":{"needs_evidence":{"type":"noul","instructions":"Does this request require evidence gathering?"}}}'
```

Expect a JSON response with an `answers` object. Keep local servers bound to
`127.0.0.1` unless you deliberately configure authentication and network
protection; task text is sent to the configured router endpoint.

### Windows

Dennice supports Python 3.11+ on Windows. In PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
dennice
```

For Codex, Claude Code, or GitHub Copilot integrations, install and authenticate
the relevant CLI in Git Bash or WSL and ensure `bash` is on `PATH`; Dennice
invokes provider CLIs through Bash on Windows. Claude Code officially supports
Windows through WSL or Git for Windows and can use a Claude Pro or Max
subscription login. [Claude Code setup](https://docs.anthropic.com/en/docs/claude-code/getting-started)
GitHub Copilot CLI is installed with `npm install -g @github/copilot` and
authenticated with `copilot login`; it uses the signed-in Copilot subscription,
not an API key. [Copilot CLI setup](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)
Copilot model access is account/organization-dependent. Dennice's list includes
documented model IDs and a custom-ID option; the CLI validates actual access.
The TUI itself remains functional in other terminals, though a modern Unicode
and true-color terminal such as Windows Terminal renders the mascot best.

The built-in benchmark is a small development fixture. Its task-family and
cognitive-demand annotations are deliberately separate; its deterministic
metrics evaluate only the explicit cognitive labels.

### Adaptive routing and authority controls

`/routing fixed|shadow|auto` selects behavior. Shadow records a recommendation
without changing execution; Auto executes only within the configured executor
provider's explicitly approved pool (for example, a Copilot selection cannot
route a turn to Codex or Claude). Pins are preserved until `/routing unpin-model` or
`/routing unpin-effort`; `/model` and `/effort` selections pin their settings again.
Example (replace the ID and capabilities with ones your account actually supports):

```text
/pool add exact-model-id strong tools context=32000 efforts=low,medium,high
/routing shadow
```

`/pool` lists candidates; `/pool remove <id>` removes one. Optional `vision` and
`tools` flags, `context=<tokens>` and `efforts=<comma-separated levels>` are operator
declarations, not guessed from names. An empty/incompatible Auto pool stops before
execution instead of silently overriding pins or switching billing providers.
The initial rules select conservative tiers from complexity, stakes and uncertainty;
these are not learned/calibrated quality rankings. PA is capped and can be disabled
with `routing.pa_enabled: false`. Router history disclosure is off by default;
enable `privacy.router_history` only if prior user/assistant turns may be shared.
`privacy.local_only: true` rejects remote/CLI providers and unsandboxed hook/MCP
processes; it does not turn arbitrary processes into a network sandbox.

### Hooks and MCP

Use Setup → Configure hooks / Configure MCP to edit a schema-validated JSON array,
add disabled examples, or remove entries. Ctrl+S applies changes to the setup draft;
Save configuration persists them, while Cancel discards the draft. Editors never
launch or trust anything. Alternatively configure entries in `dennice.yaml`, disabled by default. Merely opening project
configuration never authorizes launching its scripts or servers:

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
    approved_resources: ["file:///approved/document"]
    approved_prompts: [summarize]
```

`/hooks list|trust|enable|disable <name>` and
`/mcp list|trust|enable|disable|test <name>` manage entries. List takes no name.
Trust shows exact launch settings and needs explicit confirmation, lasts only this
app launch, and expires when execution settings change. Enablement alone is not
trust. `/mcp test` connects only the named enabled/trusted server, discovers approved
tools and allowlisted resources/prompts and invokes none; connection itself starts
the process or contacts the URL. Discovery never reads resource content or gets a prompt.
HTTP servers use `transport: http`, `url` (HTTPS or loopback HTTP), and an optional
`api_key_env` reference. Stdio `env` maps target names to source environment-variable
names. Credential values are not saved or displayed. The tested MCP SDK is the
1.x line. API/local tool loops expose exact allowlisted tools, resource URIs and
prompt names. Every access requires user approval; pagination, arguments, timeouts
and returned content are bounded. Resource templates, subscriptions, sampling and
automatic fetching of returned links are not supported. Server annotations are not
trusted proof of read-only behavior, so MCP operations are not exposed under
Read-only/Plan. Prompt messages are serialized untrusted tool content, never
promoted to system instructions. Changing allowlists invalidates launch-local trust.

Supported hooks: `before_route`, `after_route`, `before_execution`, `before_tool`,
`after_tool`, `before_verification`, `turn_complete`. Each receives bounded metadata
JSON on stdin and returns `{}` or `{"decision":"allow"}` / `{"decision":"deny"}`.
Required hook failure/timeout blocks execution. Hooks cannot grant permission or
change routing pins; start/outcome events are journaled. Native Claude's restricted
file calls use `before_tool` when enabled (file edits also require the bridge), and
observed native results use `after_tool`. Codex `before_tool` runs at native approval
requests only; actions which do not request approval cannot be blocked by that hook.
Codex observed tool completion uses `after_tool`. These observations are not proof
of pre-action enforcement; duplicate completion notifications do not repeat hooks.
Session/cancellation hooks and unrestricted native-provider interception remain unsupported.

### Bounded goals and completion evidence

`/goal <objective>` creates a durable session goal; `/goal status|pause|resume|cancel`
controls it. Configure `verification.required_files` and/or `verification.commands`
before starting. File existence checks prove only existence; choose commands with
meaningful task-specific assertions. Each verification command requires approval
and is not sandboxed. A nonempty answer alone never completes a goal. Goals stop
for user input after uncertain failures or possible effects; they never blindly
replay writes. Finished/cancelled/exhausted goals cannot resume under a fresh budget.

`budgets` bounds elapsed time, model calls, tool calls, output tokens and reported
total tokens; goals additionally cap runs. Token limits stop further calls after
reported usage, not an exact preflight spending guarantee. Provider/router usage
can be unknown; no cost or success metrics are fabricated. Run traces record the
assessment, effective route, approval/tool/hook events, usage and completion checks.

`/compact` summarizes older turns locally for future requests while preserving
the visible transcript and retaining the six most recent messages. The status
line shows provider-reported context remaining when the executor supplies both
usage and its window size; otherwise it shows a rough text estimate and labels
the window as unknown unless an exact model limit was explicitly configured.
The estimate excludes provider system prompts, tool schemas, and image tokens.
