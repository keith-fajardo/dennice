from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path

from dennice.core.models import EventKind, RunEvent, RunSummary, RunTrace
from dennice.runs.ownership import acquire, release
from dennice.core.accounting import summarize_usage


class LocalRunStore:
    """Transactional checkpoints/events with legacy JSON reads; no automatic replay."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._owned: dict[str, tuple[int, str]] = {}

    @staticmethod
    def _validate_id(run_id):
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."} or len(run_id) > 200:
            raise ValueError("Invalid run ID")

    def claim(self, run_id):
        self._validate_id(run_id)
        if run_id in self._owned:
            raise RuntimeError("This run is already claimed.")
        fd = acquire(self.path / "leases" / f"{run_id}.lock")
        if fd is None:
            raise RuntimeError("Run is owned by another active process.")
        token = uuid4().hex
        try:
            with self._connection() as db:
                db.execute("INSERT INTO run_owners VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET pid=excluded.pid, token=excluded.token",
                           (run_id, os.getpid(), token))
        except BaseException:
            release(fd)
            raise
        self._owned[run_id] = (fd, token)
        return token

    def release(self, run_id):
        owner = self._owned.pop(run_id, None)
        if owner:
            release(owner[0])

    @contextmanager
    def _connection(self):
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        database = self.path / "runs.sqlite3"
        fd = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        connection = sqlite3.connect(database, timeout=10)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("Run database schema is newer than this Dennice version")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, trace TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events "
                "(run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, "
                "PRIMARY KEY (run_id, sequence))"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS run_owners (run_id TEXT PRIMARY KEY, pid INTEGER NOT NULL, token TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=2")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def save(self, trace: RunTrace) -> None:
        self._validate_id(trace.run_id)
        transient = None
        if trace.run_id not in self._owned:
            transient = acquire(self.path / "leases" / f"{trace.run_id}.lock")
            if transient is None:
                raise RuntimeError("Cannot checkpoint a run owned by another live process.")
        try:
            with self._connection() as connection:
                self._save(connection, trace)
        finally:
            if transient is not None:
                release(transient)

    @staticmethod
    def _save(connection, trace):
        executor = dict(trace.config.get("executor", {}))
        if trace.route_plan:
            executor["model"] = trace.route_plan.effective_model
        trace.accounting = summarize_usage(trace.events,
            router=trace.config.get("router"), executor=executor)
        if any(event.run_id != trace.run_id for event in trace.events):
            raise ValueError("Run events must retain their originating run ID.")
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET "
            "trace=excluded.trace",
            (trace.run_id, trace.started_at.isoformat(), trace.model_dump_json()),
        )
        for index, event in enumerate(trace.events):
            encoded = event.model_dump_json()
            previous = connection.execute(
                "SELECT event FROM events WHERE run_id=? AND sequence=?",
                (trace.run_id, index),
            ).fetchone()
            if previous and previous[0] != encoded:
                raise ValueError("Persisted events are immutable")
            connection.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                (trace.run_id, index, encoded),
            )
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?", (trace.run_id,)
        ).fetchone()[0]
        if count != len(trace.events):
            raise ValueError("Persisted events cannot be removed")

    async def get(self, run_id: str) -> RunTrace:
        self._validate_id(run_id)
        if (self.path / "runs.sqlite3").exists():
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT trace FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if row:
                return RunTrace.model_validate_json(row[0])
        path = self.path / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No Dennice run trace found for {run_id!r} in {self.path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "status" not in raw:
            raw["status"] = (
                "failed" if raw.get("error") else "completed" if raw.get("completed_at") else "running"
            )
        return RunTrace.model_validate(raw)

    async def recover_stale(self):
        """Reconcile only registered runs whose process-lifetime lock is free."""
        recovered = []
        with self._connection() as db:
            rows = db.execute("SELECT runs.run_id, runs.trace FROM runs JOIN run_owners USING (run_id)").fetchall()
        for run_id, raw in rows:
            if RunTrace.model_validate_json(raw).status != "running" or run_id in self._owned:
                continue
            self._validate_id(run_id)
            fd = acquire(self.path / "leases" / f"{run_id}.lock")
            if fd is None:
                continue
            try:
                with self._connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT trace FROM runs WHERE run_id=?", (run_id,)).fetchone()
                    trace = RunTrace.model_validate_json(row[0])
                    if trace.status != "running":
                        continue
                    effects = self.effects(trace)
                    trace.status = "interrupted"
                    trace.completed_at = datetime.now(timezone.utc)
                    trace.error = "Owning process exited; inspect unresolved effects before continuing. Nothing was replayed."
                    trace.recovery = {"reason": "process_lock_released", "unresolved_effects": len(effects), "replay": False}
                    trace.events.append(RunEvent(run_id=run_id, kind=EventKind.RUN_INTERRUPTED, payload=trace.recovery))
                    self._save(db, trace)
                    recovered.append(run_id)
            finally:
                release(fd)
        return recovered

    @staticmethod
    def effects(trace):
        """Pending effect intents, not guesses that an interrupted action failed."""
        pending = {}
        legacy = {}
        for index, event in enumerate(trace.events):
            payload = event.payload
            kind = event.kind
            if kind in {EventKind.TOOL_STARTED, EventKind.HOOK_STARTED}:
                if kind == EventKind.TOOL_STARTED and payload.get("tool") in {"read_file", "list_files"}:
                    continue
                effect_id = payload.get("operation_id") or payload.get("effect_id") or f"legacy_{index}"
                key = (kind.value, payload.get("call_id"), payload.get("tool"), payload.get("name"), payload.get("event"))
                pending[effect_id] = {"operation_id": effect_id, "sequence": index,
                    "kind": kind.value, "details": payload, "state": "pending" if trace.status == "running" else "unknown"}
                legacy.setdefault(key, []).append(effect_id)
            elif kind in {EventKind.TOOL_COMPLETED, EventKind.HOOK_COMPLETED}:
                if payload.get("outcome_known") is False:
                    continue
                effect_id = payload.get("operation_id") or payload.get("effect_id")
                if not effect_id:
                    start_kind = EventKind.TOOL_STARTED if kind == EventKind.TOOL_COMPLETED else EventKind.HOOK_STARTED
                    key = (start_kind.value, payload.get("call_id"), payload.get("tool"), payload.get("name"), payload.get("event"))
                    matches = legacy.get(key, [])
                    effect_id = matches.pop(0) if matches else None
                pending.pop(effect_id, None)
            elif kind == EventKind.EFFECT_RECONCILED:
                pending.pop(payload.get("operation_id"), None)
        return list(pending.values())

    async def inspect_recovery(self, run_id):
        trace = await self.get(run_id)
        return {"run_id": run_id, "status": trace.status, "recovery": trace.recovery,
                "unresolved_effects": self.effects(trace), "replay": False}

    async def reconcile(self, run_id, operation_id, outcome, notes):
        """Explicit user evidence only; this never retries or grants authority."""
        self._validate_id(run_id)
        if outcome not in {"completed", "not_executed", "compensated"} or not notes.strip() or len(notes) > 4000:
            raise ValueError("Choose completed/not_executed/compensated and provide bounded evidence notes.")
        fd = acquire(self.path / "leases" / f"{run_id}.lock")
        if fd is None or run_id in self._owned:
            if fd is not None:
                release(fd)
            raise RuntimeError("Cannot reconcile a live run.")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT trace FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not row:
                    raise FileNotFoundError(run_id)
                trace = RunTrace.model_validate_json(row[0])
                if trace.status == "running":
                    raise RuntimeError("Recover/stop the run before reconciling effects.")
                if not any(item["operation_id"] == operation_id for item in self.effects(trace)):
                    raise ValueError("No unresolved effect with this operation ID.")
                trace.events.append(RunEvent(run_id=run_id, kind=EventKind.EFFECT_RECONCILED,
                    payload={"operation_id": operation_id, "outcome": outcome, "notes": notes,
                             "source": "explicit_user_resolution", "replay": False}))
                self._save(db, trace)
        finally:
            release(fd)
        return await self.inspect_recovery(run_id)

    async def list_recent(self, limit: int = 20) -> list[RunSummary]:
        if limit <= 0:
            return []
        traces = {}
        if (self.path / "runs.sqlite3").exists():
            with self._connection() as connection:
                rows = connection.execute("SELECT trace FROM runs").fetchall()
            for row in rows:
                trace = RunTrace.model_validate_json(row[0])
                traces[trace.run_id] = trace
        for path in self.path.glob("*.json") if self.path.exists() else []:
            trace = RunTrace.model_validate_json(path.read_text(encoding="utf-8"))
            traces.setdefault(trace.run_id, trace)
        ordered = sorted(traces.values(), key=lambda trace: trace.started_at, reverse=True)
        return [
            RunSummary(
                run_id=trace.run_id,
                started_at=trace.started_at,
                task_preview=trace.task.prompt[:80],
                task_family=trace.routing.task_family if trace.routing else None,
            )
            for trace in ordered[:limit]
        ]
