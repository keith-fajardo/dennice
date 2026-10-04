import asyncio
from pathlib import Path

import pytest

from dennice.benchmark.dataset import BenchmarkDataset
from dennice.benchmark.runner import BenchmarkRunner
from dennice.core.config import DenniceConfig
from dennice.core.harness import Harness
from dennice.core.models import BenchmarkMode


def test_dataset_loads_and_router_mode_persists_results(tmp_path) -> None:
    source = Path(__file__).parents[1] / "benchmarks"
    dataset = BenchmarkDataset.load(source)
    assert dataset.items[0].id == "snowflake_cost_001"
    assert "q101" in dataset.evidence["snowflake_cost_001"]["query_history"]

    config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
    harness = Harness(config)
    result = asyncio.run(BenchmarkRunner(harness).run(dataset, BenchmarkMode.ROUTER))

    item = result.items[0]
    assert item.routing.primary_correct is True
    assert item.routing.multilabel_f1 == 0.8
    assert (tmp_path / "benchmarks" / f"{result.benchmark_run_id}.json").exists()
    trace = asyncio.run(harness.store.get(item.trace_run_id))
    assert "BENCHMARK EVIDENCE" in trace.task.prompt
    assert "q101" in trace.task.prompt and "payment_events" in trace.task.prompt
    assert "A newly deployed transformation caused" not in trace.task.prompt


def test_builtin_dataset_is_available_without_project_files() -> None:
    dataset = BenchmarkDataset.builtin()
    assert [item.id for item in dataset.items] == ["snowflake_cost_001"]
    assert dataset.evidence == BenchmarkDataset.load(Path(__file__).parents[1] / "benchmarks").evidence


def test_dataset_rejects_missing_and_escaping_evidence(tmp_path) -> None:
    dataset_root = tmp_path / "dataset"
    task_dir = dataset_root / "tasks"
    task_dir.mkdir(parents=True)
    task_file = task_dir / "item.yaml"
    task_file.write_text("id: item\ntask_family: analysis\nprompt: Inspect evidence.\n"
                         "gold_cognitive_demands:\n  primary: empirical_induction\n"
                         "environment:\n  report: fixtures/missing.txt\n")
    with pytest.raises(ValueError, match="Missing benchmark fixture"):
        BenchmarkDataset.load(dataset_root)
    task_file.write_text(task_file.read_text().replace("fixtures/missing.txt", "../outside.txt"))
    with pytest.raises(ValueError, match="Invalid benchmark fixture path"):
        BenchmarkDataset.load(dataset_root)
    outside = tmp_path / "outside.txt"
    outside.write_text("private fixture sentinel")
    fixtures = dataset_root / "fixtures"
    fixtures.mkdir()
    target = fixtures / "escape.txt"
    try:
        target.symlink_to(outside)
    except OSError:
        pass  # Windows runners may not permit creating symlinks.
    else:
        task_file.write_text(task_file.read_text().replace("../outside.txt", "fixtures/escape.txt"))
        with pytest.raises(ValueError, match="leaves its dataset root"):
            BenchmarkDataset.load(dataset_root)
        target.unlink()
    task_file.write_text(task_file.read_text().replace("../outside.txt", "fixtures/escape.txt"))
    target.write_text("x" * (BenchmarkDataset.MAX_FIXTURE_BYTES + 1))
    with pytest.raises(ValueError, match="exceeded"):
        BenchmarkDataset.load(dataset_root)


def test_base_mode_has_no_routing_score(tmp_path) -> None:
    dataset = BenchmarkDataset.load(Path(__file__).parents[1] / "benchmarks")
    config = DenniceConfig(runs={"path": str(tmp_path / "runs")})
    result = asyncio.run(BenchmarkRunner(Harness(config)).run(dataset, BenchmarkMode.BASE))
    assert result.items[0].routing.primary_correct is None
