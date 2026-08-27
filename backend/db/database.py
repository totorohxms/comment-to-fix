"""Connection management + migration runner.

SQLite so the schema in migrations/ is actually executed, not decoration.
Swap the DSN for Postgres in production; the repo layer only speaks SQL.
Set CTF_DB=:memory: for an ephemeral run.
"""

import sqlite3
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Single connection is fine: uvicorn runs one event loop and our
        # statements are short. check_same_thread=False covers startup code.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def migrate(self) -> list[str]:
        """Apply pending migrations in filename order. Returns names applied."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
        )
        done = {r["version"] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(f.name.split("_", 1)[0])
            if version in done:
                continue
            self.conn.executescript(f.read_text())
            self.conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, f.name, int(time.time() * 1000)),
            )
            self.conn.commit()
            applied.append(f.name)
        return applied

    # thin statement helpers used by repos
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()
