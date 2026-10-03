from __future__ import annotations

import json
from pathlib import Path

from dennice.benchmark.schema import BenchmarkRunResult


class LocalBenchmarkStore:
    """Local JSON storage for aggregate benchmark outcomes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def save(self, result: BenchmarkRunResult) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        target = self.path / f"{result.benchmark_run_id}.json"
        target.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
