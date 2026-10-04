from __future__ import annotations

from importlib.resources import files
from pathlib import Path, PurePosixPath

import yaml

from dennice.benchmark.schema import BenchmarkItem


class BenchmarkDataset:
    MAX_FIXTURE_BYTES = 64 * 1024
    MAX_ITEM_EVIDENCE_BYTES = 128 * 1024

    def __init__(self, items: list[BenchmarkItem], path: str | Path,
                 evidence: dict[str, dict[str, str]] | None = None) -> None:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("Benchmark item IDs must be unique")
        self.items = items
        self.path = Path(path)
        self.evidence = evidence or {}

    @staticmethod
    def _parts(value: str) -> tuple[str, ...]:
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
                or any(part in {"", "."} for part in path.parts)):
            raise ValueError(f"Invalid benchmark fixture path: {value!r}")
        return path.parts

    @classmethod
    def _read_fixture(cls, resource) -> str:
        if not resource.is_file():
            raise ValueError(f"Missing benchmark fixture: {resource}")
        with resource.open("rb") as stream:
            data = stream.read(cls.MAX_FIXTURE_BYTES + 1)
        if len(data) > cls.MAX_FIXTURE_BYTES:
            raise ValueError(f"Benchmark fixture exceeded {cls.MAX_FIXTURE_BYTES} bytes: {resource}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"Benchmark fixture is not UTF-8 text: {resource}") from error

    @classmethod
    def _evidence_for(cls, item: BenchmarkItem, root) -> dict[str, str]:
        evidence, total = {}, 0
        for label, relative in item.environment.items():
            parts = cls._parts(relative)
            resource = root.joinpath(*parts)
            if isinstance(root, Path) and not resource.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Benchmark fixture leaves its dataset root: {relative}")
            content = cls._read_fixture(resource)
            total += len(content.encode("utf-8"))
            if total > cls.MAX_ITEM_EVIDENCE_BYTES:
                raise ValueError(f"Benchmark item {item.id} exceeded its evidence size limit")
            evidence[label] = content
        return evidence

    @classmethod
    def load(cls, path: str | Path) -> "BenchmarkDataset":
        root = Path(path)
        task_dir = root / "tasks" if (root / "tasks").is_dir() else root
        if not task_dir.exists():
            return cls([], root)
        fixture_root = root.parent if task_dir == root and root.name == "tasks" else root
        items: list[BenchmarkItem] = []
        for task_file in sorted((*task_dir.glob("*.yaml"), *task_dir.glob("*.yml"), *task_dir.glob("*.json"))):
            with task_file.open(encoding="utf-8") as stream:
                raw = yaml.safe_load(stream)
            items.append(BenchmarkItem.model_validate(raw))
        evidence = {item.id: cls._evidence_for(item, fixture_root) for item in items}
        return cls(items, root, evidence)

    @classmethod
    def builtin(cls) -> "BenchmarkDataset":
        """Load the small package-provided development set for first-run usability."""
        task_dir = files("dennice.benchmark.builtin_tasks")
        items = [
            BenchmarkItem.model_validate(yaml.safe_load(resource.read_text(encoding="utf-8")))
            for resource in sorted(task_dir.iterdir(), key=lambda entry: entry.name)
            if resource.name.endswith((".yaml", ".yml"))
        ]
        evidence = {item.id: cls._evidence_for(item, task_dir) for item in items}
        return cls(items, "package:dennice.benchmark.builtin_tasks", evidence)
