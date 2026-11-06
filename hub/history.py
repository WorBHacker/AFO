"""A project's files, version after version (stage 26; the audit's Local History): whatever changes a file - a model,
the user's editor, Claude Code, a command, another program, git - its new content is kept, so any change can be taken
back, not only the hub's own writes (hub/changes.py).

The watcher (hub/watch.py) hands over the files each batch changed, with what git ignores and the caches left out
already. Each is read; a content that is not the file's last version goes into the snapshots' store (data/snapshots,
by hash, one copy each - D29, shared with the changes) and a row of `file_versions`; a file gone gets a row without
content. The hub's own writes come in first, from the change's record (changes.record): with the version before them
and the change's name - so the watcher's sight of the same write adds nothing.

Not kept: a secret file's content (D10), a file over MAX_BYTES (the hub writes none bigger), a binary one, the hub's
own half-written temporaries; outside git, the folders the tree hides there (dist, build, ...). The history begins
with the first change the hub saw: what a file was before that is in git, or nowhere. It is kept within D30's term
and size as the other snapshots are (hub/retention.py): rows past the term go, and a version whose content went over
the size is not offered.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import changes, projects
from .db import Database
from .projects import NOISE, Project, ProjectError
from .secret_filter import is_secret_file

log = logging.getLogger("hub.history")

MAX_BYTES = changes.MAX_BYTES
TEMP_SUFFIX = ".localchat"  # changes.write_atomic's file beside the one it replaces
DIFF_LINES = 4000
FILES_LISTED = 200


def looked(root: Path, rel: str) -> tuple[bool, bytes | None]:
    """A changed file as the history takes it: (True, its bytes), (True, None) - gone, (False, None) - not for the
    history (see the module's note)."""
    name = rel.rsplit("/", 1)[-1]
    if (name.startswith(".") and name.endswith(TEMP_SUFFIX)) or is_secret_file(rel):
        return False, None
    try:
        target = projects.resolve_inside(root, rel)
        stat = target.stat()
    except FileNotFoundError:
        return True, None
    except ProjectError, OSError:
        return False, None
    if not target.is_file() or stat.st_size > MAX_BYTES or projects.linked_secret(target, stat.st_nlink):
        return False, None
    try:
        data = target.read_bytes()
    except FileNotFoundError:
        return True, None
    except OSError:  # held by another program: its next change comes again
        return False, None
    return (False, None) if projects.is_binary(data) else (True, data)


def gather(root: Path, paths: Iterable[str], folder: Path) -> dict[str, tuple[str, int] | None]:
    """The batch's files that the history takes: path -> (hash, size) of the content, kept in `folder`; None - gone."""
    found: dict[str, tuple[str, int] | None] = {}
    repo: bool | None = None
    for rel in sorted(paths):
        if any(part in NOISE for part in rel.split("/")):
            if repo is None:
                repo = projects.is_repo(root)
            if not repo:
                continue
        take, data = looked(root, rel)
        if take:
            found[rel] = None if data is None else (changes.keep(folder, data), len(data))
    return found


def record(conn: sqlite3.Connection, project_id: str, found: dict[str, tuple[str, int] | None]) -> int:
    """The gathered files into their history; how many new versions."""
    return sum(changes.note_version(conn, project_id, rel, *(seen or (None, 0))) for rel, seen in found.items())


# ---------------------------------------------------------------- reading


SELECT = ("SELECT v.*, f.tool, c.title AS chat FROM file_versions v LEFT JOIN file_changes f ON f.id = v.change_id"
          " LEFT JOIN conversations c ON c.id = f.conversation_id")  # fmt: skip


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "deleted": row["hash"] is None}


def versions(conn: sqlite3.Connection, folder: Path, project_id: str, path: str, limit: int = 100) -> list[dict]:
    """The file's versions, newest first: {id, created_at, path, hash, size, deleted, change_id, tool, chat} - the
    tool and the chat of the hub's write that made it. One whose content is gone (over D30's size) is left out."""
    rows = conn.execute(SELECT + " WHERE v.project_id = ? AND v.path = ? ORDER BY v.id DESC LIMIT ?",
                        (project_id, path, limit)).fetchall()  # fmt: skip
    return [_row(r) for r in rows if r["hash"] is None or (folder / r["hash"]).is_file()]


def files(conn: sqlite3.Connection, project_id: str, limit: int = FILES_LISTED) -> list[dict[str, Any]]:
    """The project's files that have a history, the latest change first: {path, versions, last_at, deleted} -
    deleted: the last version is the file gone."""
    rows = conn.execute(
        "SELECT v.path, COUNT(*) AS versions, MAX(v.created_at) AS last_at,"
        " (SELECT l.hash IS NULL FROM file_versions l WHERE l.project_id = v.project_id AND l.path = v.path"
        "  ORDER BY l.id DESC LIMIT 1) AS deleted"
        " FROM file_versions v WHERE v.project_id = ? GROUP BY v.path ORDER BY MAX(v.id) DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [{**dict(r), "deleted": bool(r["deleted"])} for r in rows]


def get_version(conn: sqlite3.Connection, version_id: int) -> dict[str, Any] | None:
    row = conn.execute(SELECT + " WHERE v.id = ?", (version_id,)).fetchone()
    return _row(row) if row is not None else None


def previous(conn: sqlite3.Connection, version: dict[str, Any]) -> dict[str, Any] | None:
    """The version of the same file just before this one."""
    row = conn.execute(SELECT + " WHERE v.project_id = ? AND v.path = ? AND v.id < ? ORDER BY v.id DESC LIMIT 1",
                       (version["project_id"], version["path"], version["id"])).fetchone()  # fmt: skip
    return _row(row) if row is not None else None


def _text(data: bytes | None) -> bytes | None:
    """A version's bytes as UTF-8 text, so that a cp1251 or UTF-16 file reads in the diff as it does in the viewer."""
    return None if data is None else projects.decoded(data)["text"].encode("utf-8")


def diff(path: str, before: bytes | None, after: bytes | None) -> str:
    return changes.diff(path, _text(before), _text(after), limit=DIFF_LINES)


# ---------------------------------------------------------------- the watcher's side


class History:
    """Takes what the watcher saw into the history: reading on a worker thread, a project's batches one at a time
    (each compares with the last version the one before it left)."""

    def __init__(self, db: Database, folder: Path):
        self._db = db
        self.folder = folder
        self._locks: dict[str, asyncio.Lock] = {}

    async def keep(self, project: Project, paths: set[str]) -> None:
        lock = self._locks.setdefault(project.id, asyncio.Lock())
        async with lock:
            try:
                found = await asyncio.to_thread(gather, project.root, paths, self.folder)
                if found:
                    await self._db.acall(lambda conn: record(conn, project.id, found))
            except sqlite3.IntegrityError:  # the project was removed meanwhile
                return
            except Exception:
                log.exception("project %s: keeping the changed files' versions failed", project.id)
