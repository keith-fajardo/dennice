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

The initial release is intentionally offline: it uses a deterministic router and
mock executor to validate the architecture before model or database adapters are added.

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
