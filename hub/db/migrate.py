"""Numbered SQL migrations: hub/db/migrations/NNN_name.sql, each applied once, in its own transaction."""

from __future__ import annotations

import re
import sqlite3
from importlib import resources

_FILE_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""


def available_migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every migration file, in order."""
    found = []
    for entry in (resources.files("hub.db") / "migrations").iterdir():
        match = _FILE_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), match.group(2), entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [version for version, _, _ in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"two migration files share a number: {versions}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(_CREATE_TABLE)
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Applies the migrations the database does not have yet; returns their versions."""
    start = current_version(conn)
    applied = []
    for version, name, sql in available_migrations():
        if version <= start:
            continue
        # name matches [a-z0-9_]+, so it is safe inside the script
        record = f"INSERT INTO schema_migrations (version, name) VALUES ({version}, '{name}');"
        script = f"BEGIN IMMEDIATE;\n{sql}\n{record}\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
