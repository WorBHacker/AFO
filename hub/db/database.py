"""One SQLite connection for the hub: WAL, foreign keys on, used from worker threads under a lock."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

PRAGMAS = ("journal_mode=WAL", "foreign_keys=ON", "busy_timeout=5000", "synchronous=NORMAL")


def utc_now() -> str:
    """ISO 8601 UTC with milliseconds, the same text format as the schema's column defaults."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    """The hub is the only writer (D29). Calls are serialized by a lock, so the asyncio loop can hand
    them to worker threads (`acall`) without blocking, and no two calls interleave a transaction."""

    def __init__(self, path: Path):
        self.path = path
        # isolation_level=None: autocommit; transactions are explicit (see `transaction`)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        for pragma in PRAGMAS:
            self._conn.execute(f"PRAGMA {pragma}")

    def call[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            return fn(self._conn)

    async def acall[T](self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self.call, fn)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """`with db.transaction() as conn:` - BEGIN IMMEDIATE, COMMIT on success, ROLLBACK on error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
