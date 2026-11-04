"""The language of what the hub writes for people (step 10.5): the window's choice, with a copy in the settings table -
the hub started at Windows login speaks it before the window opens; the window tells it again on every connect."""

from __future__ import annotations

import json
import sqlite3

from common import i18n

from .db import utc_now

KEY = "ui.language"


def saved(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def load(conn: sqlite3.Connection) -> str:
    """At the hub's start: the language chosen before, else as i18n.chosen() finds it."""
    return i18n.set_language(i18n.chosen(saved(conn)))


def store(conn: sqlite3.Connection, code: str) -> str:
    i18n.set_language(code)  # ValueError for a language AFO does not speak: nothing is kept
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (KEY, json.dumps(code), utc_now()),
    )
    return code
