from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from dennice import __version__
from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode

app = typer.Typer(help="Dennice cognitive routing and evaluation harness.", no_args_is_help=False)
benchmark_app = typer.Typer(help="Run and inspect cognitive-routing benchmarks.")
app.add_typer(benchmark_app, name="benchmark")


def _json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Launch the Textual TUI when Dennice is invoked without a command."""
    if ctx.invoked_subcommand is None:
        from dennice.tui.app import DenniceApp

        DenniceApp().run()


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def init() -> None:
    """Create an editable project configuration without overwriting files."""
    target = Path("dennice.yaml")
    if target.exists():
        raise typer.BadParameter("dennice.yaml already exists; refusing to overwrite it.")
    target.write_text(
        "version: 1\nrouter:\n  provider: rule\n  model: v1\nexecutor:\n  provider: mock\n  model: v1\n",
        encoding="utf-8",
    )
    for directory in (Path("benchmarks/tasks"), Path("benchmarks/fixtures"), Path("policies"), Path(".dennice/runs")):
        directory.mkdir(parents=True, exist_ok=True)
    seed = Path("benchmarks/tasks/snowflake_cost_001.yaml")
    if not seed.exists():
        from importlib.resources import files

        seed.write_text(
            files("dennice.benchmark.builtin_tasks")
            .joinpath("snowflake_cost_001.yaml")
            .read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    typer.echo("Initialized Dennice project configuration.")


@app.command()
def classify(
    task: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    decision = asyncio.run(Harness.from_config().classify(task))
    if json_output:
        _json(decision.model_dump(mode="json"))
        return
    typer.echo(f"Task family\n{decision.task_family}\n\nCognitive routing")
    for score in decision.cognitive_demands:
        label = "PRIMARY" if score.demand == decision.primary_demand else "SUPPORTING" if score.demand in decision.supporting_demands else ""
        typer.echo(f"{score.demand.value:<28} {score.confidence:.2f}  {label}")


@app.command()
def run(
    task_or_file: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the persisted trace as JSON.")] = False,
) -> None:
    candidate = Path(task_or_file)
    task = candidate.read_text(encoding="utf-8") if candidate.is_file() else task_or_file
    trace = asyncio.run(Harness.from_config().run(task))
    if json_output:
        _json(trace.model_dump(mode="json"))
        return
    typer.echo(f"Run {trace.run_id}\n")
    typer.echo(trace.result.output if trace.result else trace.error or "No result")


@app.command()
def inspect(
    run_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    trace = asyncio.run(Harness.from_config().store.get(run_id))
    if json_output:
        _json(trace.model_dump(mode="json"))
        return
    typer.echo(f"Run: {trace.run_id}\nTask: {trace.task.prompt}\n")
    if trace.routing:
        typer.echo(f"Task family: {trace.routing.task_family}\nPrimary: {trace.routing.primary_demand}")
    typer.echo("\n" + (trace.result.output if trace.result else trace.error or "No result"))


@benchmark_app.command("list")
def benchmark_list(
    path: Annotated[str | None, typer.Option("--path", help="Benchmark directory.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    config = DenniceConfig.load()
    dataset = BenchmarkDataset.load(path or config.benchmark.path)
    if not dataset.items and path is None:
        dataset = BenchmarkDataset.builtin()
    if json_output:
        _json([item.model_dump(mode="json") for item in dataset.items])
        return
    if not dataset.items:
        typer.echo(f"No benchmark tasks found in {dataset.path}")
        return
    for item in dataset.items:
        typer.echo(f"{item.id:<28} {item.split:<12} {item.task_family}")


@benchmark_app.command("run")
def benchmark_run(
    mode: Annotated[BenchmarkMode, typer.Option("--mode")] = BenchmarkMode.ROUTER,
    path: Annotated[str | None, typer.Option("--path", help="Benchmark directory.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    config = DenniceConfig.load()
    dataset = BenchmarkDataset.load(path or config.benchmark.path)
    if not dataset.items and path is None:
        dataset = BenchmarkDataset.builtin()
    result = asyncio.run(BenchmarkRunner(Harness(config)).run(dataset, mode))
    if json_output:
        _json(result.model_dump(mode="json"))
        return
    aggregate = result.aggregate_routing
    typer.echo(f"Benchmark run: {result.benchmark_run_id}\nMode: {result.mode.value}\nTasks: {len(result.items)}")
    if aggregate.multilabel_f1 is not None:
        typer.echo(
            f"Primary accuracy: {aggregate.primary_accuracy:.2f}\n"
            f"Multilabel F1: {aggregate.multilabel_f1:.2f}"
        )
