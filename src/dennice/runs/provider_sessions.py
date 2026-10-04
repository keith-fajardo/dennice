"""Opaque native thread linkage. Never store authentication material."""

import json

from dennice.runs.store import LocalRunStore


class ProviderSessionStore:
    def __init__(self, path):
        self.store = LocalRunStore(path)

    def get(self, session_id, provider, cwd):
        with self.store._connection() as connection:
            self._schema(connection)
            row = connection.execute(
                "SELECT state FROM provider_sessions WHERE session_id=? AND provider=? AND cwd=?",
                (session_id, provider, cwd),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, session_id, provider, cwd, state):
        with self.store._connection() as connection:
            self._schema(connection)
            connection.execute(
                "INSERT INTO provider_sessions VALUES (?,?,?,?) ON CONFLICT(session_id,provider,cwd) "
                "DO UPDATE SET state=excluded.state", (session_id, provider, cwd, json.dumps(state)),
            )

    @staticmethod
    def _schema(connection):
        connection.execute("CREATE TABLE IF NOT EXISTS provider_sessions "
                           "(session_id TEXT, provider TEXT, cwd TEXT, state TEXT NOT NULL, "
                           "PRIMARY KEY(session_id,provider,cwd))")
