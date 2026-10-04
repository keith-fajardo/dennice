"""Workspace-local durable chat sessions; closing a tab archives its record."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from dennice.runs.store import LocalRunStore


@dataclass
class ChatMessage:
    role: str
    content: str
    images: list[str] = field(default_factory=list)


@dataclass
class ChatSession:
    id: str
    title: str
    messages: list[ChatMessage]
    input_history: list[str]
    pending_images: list[str] = field(default_factory=list)
    closed: bool = False
    goal_id: str | None = None
    working_directory: str = field(default_factory=lambda: str(Path.cwd().resolve()))
    file_filter: str = ""
    file_content_filter: str = ""
    file_replacement: str = ""
    context_summary: str = ""
    compacted_message_count: int = 0
    context_used_tokens: int | None = None
    context_window_tokens: int | None = None
    context_provider: str | None = None
    context_model: str | None = None

    @classmethod
    def new(cls, title: str, working_directory: str | None = None) -> ChatSession:
        return cls(f"session_{uuid4().hex}", title, [], [],
                   working_directory=working_directory or str(Path.cwd().resolve()))


class SessionStore:
    def __init__(self, path, default_directory: str | None = None) -> None:
        self._store = LocalRunStore(path)
        self.default_directory = default_directory or str(Path.cwd().resolve())

    def save(self, session: ChatSession) -> None:
        with self._store._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(session_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?) ON CONFLICT(session_id) "
                "DO UPDATE SET state=excluded.state",
                (session.id, json.dumps(asdict(session))),
            )

    def list(self, *, include_closed: bool = False) -> list[ChatSession]:
        if not (self._store.path / "runs.sqlite3").exists():
            return []
        with self._store._connection() as connection:
            exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
            rows = connection.execute("SELECT state FROM sessions ORDER BY rowid").fetchall() if exists else []
        sessions = []
        for row in rows:
            data = json.loads(row[0])
            if not data.get("working_directory"):
                data["working_directory"] = self.default_directory
            data["messages"] = [ChatMessage(**message) for message in data["messages"]]
            session = ChatSession(**data)
            if include_closed or not session.closed:
                sessions.append(session)
        return sessions
