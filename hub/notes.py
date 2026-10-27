"""Notes (stage 3, D22 L4): short facts and decisions about a project that a model should always know.

The user writes them in the project's overview; a chat with the project gets them as their own context block
(at most NOTES_BUDGET tokens for all of a project's notes, secret-filtered), sent again only when they change.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import utc_now
from .limits import limit

NOTE_MAX_CHARS = 1000
NOTES_CHARS_PER_TOKEN = 4  # the budget is in tokens (D22, hub/limits.py): ~1K tokens by default


class NoteError(Exception):
    """The message is fit to show."""


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "project_id", "text", "created_at", "updated_at")}


def list_notes(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
    return [_row(r) for r in rows]


def _clean(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise NoteError("an empty note")
    if len(text) > NOTE_MAX_CHARS:
        raise NoteError(f"a note is at most {NOTE_MAX_CHARS} characters; write a few shorter ones")
    return text


def add_note(conn: sqlite3.Connection, project_id: str, text: str) -> dict[str, Any]:
    note_id = conn.execute("INSERT INTO notes (project_id, text) VALUES (?, ?)", (project_id, _clean(text))).lastrowid
    return _row(conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone())


def update_note(conn: sqlite3.Connection, note_id: int, text: str) -> dict[str, Any]:
    if not conn.execute(
        "UPDATE notes SET text = ?, updated_at = ? WHERE id = ?", (_clean(text), utc_now(), note_id)
    ).rowcount:
        raise NoteError("no such note")
    return _row(conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone())


def delete_note(conn: sqlite3.Connection, note_id: int) -> str | None:
    """Deletes a note; returns its project's id (None when there was no such note)."""
    row = conn.execute("SELECT project_id FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return row["project_id"]


def notes_layer(project_name: str, notes: list[dict[str, Any]]) -> str:
    """The L4 block of a project: its notes, newest last, within the budget (older ones give way first)."""
    lines: list[str] = []
    used = 0
    for note in reversed(notes):
        line = f"- {note['text']}"
        if used + len(line) > limit("notes") * NOTES_CHARS_PER_TOKEN:
            break
        lines.insert(0, line)
        used += len(line) + 1
    skipped = len(notes) - len(lines)
    more = f"\n[{skipped} older note(s) left out to fit the budget]" if skipped else ""
    return (
        f'<notes project="{project_name}" about="facts and decisions the user wrote down">\n'
        + "\n".join(lines)
        + more
        + "\n</notes>"
    )
