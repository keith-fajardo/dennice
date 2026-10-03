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
  login, or Claude Code using your Claude subscription/login.

Dennice does not store provider credentials. The Codex adapter runs with a
read-only sandbox; the Claude adapter uses Claude Code print mode with plan
permissions. Both receive the prompt composed from the selected cognitive
policies.

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
  endpoint: https://thejevai.com/v1/systemone
  model: typesafe/jev-1.13
```

Use `default` for either setting when you want Codex to choose its configured
default. Model availability and supported effort levels depend on your Codex
account and selected model.

For hosted Jev, select **Jev · hosted** in Setup. Setup stores only the
environment-variable name (default `JEV_API_KEY`), never the secret. Export the
value in your shell before starting Dennice. The first shipped local router is
**OpenJev · local**, which uses only the configured local endpoint and needs no
API key. Both use the `/v1/systemone` typed-decision protocol rather than a task
solution. [Jev’s System One documentation](https://github.com/jev-ai/system-one-jev/blob/main/README.md)
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

Then select any System 2 executor (Mock, Codex, or Claude Code) and save. The
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

For Codex or Claude Code integrations, install the relevant CLI in Git Bash or
WSL and ensure `bash` is on `PATH`; Dennice invokes provider CLIs through Bash
on Windows. Claude Code officially supports Windows through WSL or Git for
Windows and can use a Claude Pro or Max subscription login. [Claude Code setup](https://docs.anthropic.com/en/docs/claude-code/getting-started)
The TUI itself remains functional in other terminals, though a modern Unicode
and true-color terminal such as Windows Terminal renders the mascot best.

The built-in benchmark is a small development fixture. Its task-family and
cognitive-demand annotations are deliberately separate; its deterministic
metrics evaluate only the explicit cognitive labels.
