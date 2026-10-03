from __future__ import annotations

import json
from pathlib import Path

from dennice.core.models import RunSummary, RunTrace


class LocalRunStore:
    """Inspectable JSON trace store with one trace per run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _file(self, run_id: str) -> Path:
        return self.path / f"{run_id}.json"

    async def save(self, trace: RunTrace) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._file(trace.run_id).write_text(
            json.dumps(trace.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    async def get(self, run_id: str) -> RunTrace:
        path = self._file(run_id)
        if not path.exists():
            raise FileNotFoundError(f"No Dennice run trace found for {run_id!r} in {self.path}")
        return RunTrace.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_recent(self, limit: int = 20) -> list[RunSummary]:
        traces = []
        for path in self.path.glob("*.json") if self.path.exists() else []:
            traces.append(RunTrace.model_validate_json(path.read_text(encoding="utf-8")))
        traces.sort(key=lambda trace: trace.started_at, reverse=True)
        return [
            RunSummary(
                run_id=trace.run_id,
                started_at=trace.started_at,
                task_preview=trace.task.prompt[:80],
                task_family=trace.routing.task_family if trace.routing else None,
            )
            for trace in traces[:limit]
        ]
