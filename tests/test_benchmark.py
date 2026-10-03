import asyncio
from pathlib import Path

from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode


def test_dataset_loads_and_router_mode_persists_results(tmp_path) -> None:
    source = Path(__file__).parents[1] / "benchmarks"
    dataset = BenchmarkDataset.load(source)
    assert dataset.items[0].id == "snowflake_cost_001"

    config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
    result = asyncio.run(BenchmarkRunner(Harness(config)).run(dataset, BenchmarkMode.ROUTER))

    item = result.items[0]
    assert item.routing.primary_correct is True
    assert item.routing.multilabel_f1 == 0.8
    assert (tmp_path / "benchmarks" / f"{result.benchmark_run_id}.json").exists()


def test_builtin_dataset_is_available_without_project_files() -> None:
    dataset = BenchmarkDataset.builtin()
    assert [item.id for item in dataset.items] == ["snowflake_cost_001"]


def test_base_mode_has_no_routing_score(tmp_path) -> None:
    dataset = BenchmarkDataset.load(Path(__file__).parents[1] / "benchmarks")
    config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
    result = asyncio.run(BenchmarkRunner(Harness(config)).run(dataset, BenchmarkMode.BASE))
    assert result.items[0].routing.primary_correct is None
