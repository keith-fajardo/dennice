import json

from typer.testing import CliRunner

from dennice.benchmark.dataset import BenchmarkDataset
from dennice.cli.app import app


def test_classify_json_is_machine_readable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["classify", "Why did Snowflake credits increase?", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task_family"] == "cost_optimization"
    assert payload["primary_demand"] == "empirical_induction"


def test_benchmark_list_json(tmp_path, monkeypatch) -> None:
    benchmark_dir = tmp_path / "benchmarks" / "tasks"
    benchmark_dir.mkdir(parents=True)
    benchmark_dir.joinpath("task.yaml").write_text(
        "id: task_001\ntask_family: data_analysis\nprompt: Check a metric.\n"
        "gold_cognitive_demands:\n  primary: empirical_induction\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["benchmark", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "task_001"


def test_init_creates_seed_benchmark(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "benchmarks" / "tasks" / "snowflake_cost_001.yaml").exists()
    for name in ("query_history.csv", "warehouse_history.csv", "run_results.json"):
        assert (tmp_path / "benchmarks" / "fixtures" / name).is_file()
    dataset = BenchmarkDataset.load(tmp_path / "benchmarks")
    assert "q101" in dataset.evidence["snowflake_cost_001"]["query_history"]


def test_benchmark_run_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["benchmark", "run", "--mode", "oracle", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "oracle"
    assert payload["aggregate_routing"]["multilabel_f1"] == 1.0
