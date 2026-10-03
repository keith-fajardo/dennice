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

The deterministic router remains the default. Press `s` in the TUI to open
**Setup** and choose the offline mock executor or the local Codex CLI executor.
The Codex option uses your existing `codex login` session and runs with a
read-only sandbox; Dennice never stores provider credentials.

For Codex, Setup also stores a model override and optional reasoning effort in
your local `dennice.yaml`:

```yaml
executor:
  provider: codex
  model: default
  reasoning_effort: high
```

Use `default` for either setting when you want Codex to choose its configured
default. Model availability and supported effort levels depend on your Codex
account and selected model.

### Windows

Dennice supports Python 3.11+ on Windows. In PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
dennice
```

For the best mascot rendering, use a modern Unicode and true-color terminal such
as Windows Terminal. The TUI remains functional in other terminals.

The built-in benchmark is a small development fixture. Its task-family and
cognitive-demand annotations are deliberately separate; its deterministic
metrics evaluate only the explicit cognitive labels.
