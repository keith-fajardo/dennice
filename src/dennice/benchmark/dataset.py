from __future__ import annotations

from pathlib import Path
from importlib.resources import files

import yaml

from dennice.benchmark.schema import BenchmarkItem


class BenchmarkDataset:
    def __init__(self, items: list[BenchmarkItem], path: str | Path) -> None:
        self.items = items
        self.path = Path(path)

    @classmethod
    def load(cls, path: str | Path) -> "BenchmarkDataset":
        root = Path(path)
        task_dir = root / "tasks" if (root / "tasks").is_dir() else root
        if not task_dir.exists():
            return cls([], root)
        items: list[BenchmarkItem] = []
        for task_file in sorted((*task_dir.glob("*.yaml"), *task_dir.glob("*.yml"), *task_dir.glob("*.json"))):
            with task_file.open(encoding="utf-8") as stream:
                raw = yaml.safe_load(stream)
            items.append(BenchmarkItem.model_validate(raw))
        return cls(items, root)

    @classmethod
    def builtin(cls) -> "BenchmarkDataset":
        """Load the small package-provided development set for first-run usability."""
        task_dir = files("dennice.benchmark.builtin_tasks")
        items = [
            BenchmarkItem.model_validate(yaml.safe_load(resource.read_text(encoding="utf-8")))
            for resource in sorted(task_dir.iterdir(), key=lambda entry: entry.name)
            if resource.name.endswith((".yaml", ".yml"))
        ]
        return cls(items, "package:dennice.benchmark.builtin_tasks")
