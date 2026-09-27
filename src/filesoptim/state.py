"""Persistent memory of already processed files, so nothing is re-compressed twice."""

from __future__ import annotations

import sqlite3
import time
import weakref
from pathlib import Path
from types import TracebackType

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    signature TEXT NOT NULL,
    outcome TEXT NOT NULL,
    updated REAL NOT NULL
)
"""


class StateDB:
    """SQLite store keyed by absolute path, valid while size, mtime and settings are unchanged."""

    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(":memory:" if path is None else str(path))
        self._conn.execute(SCHEMA)
        # Closed even if the owner forgets to (e.g. after an exception).
        self._finalizer = weakref.finalize(self, self._conn.close)

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.absolute())

    def lookup(self, path: Path, size: int, mtime_ns: int, signature: str) -> str | None:
        """Outcome recorded for this exact file version and settings, if any."""
        row = self._conn.execute(
            "SELECT outcome FROM processed WHERE path=? AND size=? AND mtime_ns=? AND signature=?",
            (self._key(path), size, mtime_ns, signature),
        ).fetchone()
        return None if row is None else str(row[0])

    def record(self, path: Path, size: int, mtime_ns: int, signature: str, outcome: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO processed VALUES (?, ?, ?, ?, ?, ?)",
                (self._key(path), size, mtime_ns, signature, outcome, time.time()),
            )

    def forget(self, path: Path) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM processed WHERE path=?", (self._key(path),))

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM processed").fetchone()[0])

    def close(self) -> None:
        self._finalizer()

    def __enter__(self) -> StateDB:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
