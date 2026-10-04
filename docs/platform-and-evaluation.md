# Platform contracts and outcome evaluation

CI is configured to install Dennice and run the unit/fixture suite on Ubuntu, macOS and Windows with Python 3.11/3.12. It checks installed dependency consistency with `pip check`, the installed CLI entry point, and packaged policy/schema resources. Every shell step uses Bash; Windows runners provide Git Bash. Install Python 3.11+ and Git for Windows locally, then run `python -m pip install '.[dev]'` and `python -m pytest` in Git Bash. Provider CLI argument forwarding is quoted with Bash-compatible literal arguments. Cancellation scopes Windows `taskkill /PID … /T /F` to the subprocess PID owned by Dennice. Fixtures verify this contract without launching a provider.

Native descriptor-confined file tools require POSIX directory descriptors and `O_NOFOLLOW`. They deliberately fail closed on native Windows where these are unavailable. WSL/Linux is the supported option for these tools; Git Bash does not supply missing Python filesystem primitives. CLI providers retain their own platform/security behavior. User-approved shell, hook and MCP processes are not an OS sandbox. The CI matrix is configured, but remote matrix execution must be observed before claiming Windows integration has passed.

## Offline reporting

The seeded Snowflake item now ships with synthetic query, warehouse and dbt
evidence. `BenchmarkDataset` validates relative fixture paths and size, then
passes bounded UTF-8 contents to every arm as user-level task data. It does not
pass gold labels or the reference answer to the executor. The item remains a
development example scored only for routing labels; the presence of evidence
does not turn it into a held-out quality evaluation.

`dennice.benchmark.outcomes` provides `ablation_configs`, `outcome_record`, and `summarize_outcomes`. These functions do not make model calls, read credentials, run verification commands, or launch tools. They consume saved `RunTrace` objects and caller-supplied independent critical-failure labels and pricing snapshots. Serialize the returned summary with Python's `json.dumps` to export it.

For a price-free offline JSON report: `python -m dennice.benchmark.outcomes --arm user_selected path/to/run-trace.json`. The command reads only the named saved JSON files and writes a summary to stdout; no model call or task is executed. Use the Python functions for combined arms, supplied rate snapshots and independent critical-failure labels.

The configuration arms are user-selected, fixed-strong, fixed-cheap, routed, routed-without-PA, and fixed-without-PA. Exact strong/cheap IDs and routing pool capabilities must be explicitly supplied and verified with the provider. All arms retain the same tools, budgets, completion checks and pinned effort to isolate model and PA changes. Fixed arms still classify prompts for PA selection; their router cost is not zero. This is infrastructure, not a completed experiment.

Run a held-out corpus with independent checks, matched task IDs, isolated workspaces, identical permissions/budgets and repeated seeds when available. Keep examples used to tune routes out of the held-out corpus. Human or independent task-specific evaluation must supply critical-failure labels: a nonempty answer is not sufficient evidence. An interrupted run cannot count as verified completion even if a check once passed. Never run paid/live arms without explicit authorization and a budget.

Reports include verified-completion rate among independently assessed runs, unassessed counts, critical failures and unassessed critical cases, nearest-rank p50/p95 latency, unknown usage/cost counts and a known executor-token cost subtotal. Subtotals are explicitly partial, not full billing cost: router usage, cached-token discounts, external tools/services and subscription accounting are not included. Missing telemetry or prices stay `null`; they are never silently zero. Pairing requires matching item-ID multiplicities across arms, but does not establish statistical validity or identical environments. Do not claim quality improvement or cost savings from classification accuracy alone, small development examples, or an unpaired report.
