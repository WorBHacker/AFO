"""What the hub keeps on disk, within D30's limits (2026-10-28; Q4's default size): the files before and after the
models' changes and the versions of the files' history (data/snapshots, by hash; stage 26), the agent's screen
snapshots (data/computer) and the terminals' logs
(data/terminals, hub/terminals.py) - each kind kept `days` and within `max_gb`, the oldest going first. Conversations,
runs, events and spending are kept always (D30) - and so are the files and pictures their messages name
(data/blobs); working copies of sent files (data/attachments, data/pasted) go like the snapshots, and so do the
pictures «Нарисовать» keeps (data/media/<year-month>, 29.4б) - one sent into a chat is the chat's, in data/blobs.

The limits are the user's (`settings` row "retention", the window's «Настройки → Хаб → Хранение»); the hub prunes at
its start and every PRUNE_EVERY_S. A snapshot still needed by a change younger than `days` stays; when it goes, the
change can no longer be undone (changes.py says so), its row stays.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.i18n import tr

from . import checkpoints
from .db import Database, utc_now
from .terminals import prune_logs

log = logging.getLogger("hub.retention")

SETTING = "retention"
DEFAULTS = {"days": 90, "max_gb": 2.0}  # D30's term; Q4's size - per kind
DAYS = (7, 3650)
MAX_GB = (0.1, 1000.0)
PRUNE_EVERY_S = 6 * 3600
GIB = 1024**3


class RetentionError(ValueError):
    """A limit the hub does not take; the message says which, in words for the window."""


def current(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
    saved = json.loads(row["value"]) if row else {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save(conn: sqlite3.Connection, days: Any, max_gb: Any) -> dict[str, Any]:
    if isinstance(days, bool) or not isinstance(days, int) or not DAYS[0] <= days <= DAYS[1]:
        raise RetentionError(tr("сколько дней хранить: целое от {low} до {high}", low=DAYS[0], high=DAYS[1]))
    if isinstance(max_gb, bool) or not isinstance(max_gb, int | float) or not MAX_GB[0] <= max_gb <= MAX_GB[1]:
        raise RetentionError(tr("предел размера: от {low:g} до {high:g} ГБ", low=MAX_GB[0], high=MAX_GB[1]))
    value = {"days": days, "max_gb": float(max_gb)}
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (SETTING, json.dumps(value), utc_now()),
    )
    return value


# ---------------------------------------------------------------- the kinds


def _files(folder: Path, pattern: str) -> list[tuple[Path, int, float]]:
    out = []
    if folder.is_dir():
        for path in folder.glob(pattern):
            with contextlib.suppress(OSError):
                if path.is_file():
                    stat = path.stat()
                    out.append((path, stat.st_size, stat.st_mtime))
    return out


def _thin(found: list[tuple[Path, int, float]], cutoff: float, max_bytes: int) -> int:
    """Those older than `cutoff` (seconds), then the oldest until the rest fit `max_bytes`: `found` is (path, size,
    last used), any order."""
    removed = 0
    total = sum(size for _path, size, _at in found)
    for path, size, at in sorted(found, key=lambda f: f[2]):
        if at >= cutoff and total <= max_bytes:
            break
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
            total -= size
    return removed


def prune_snapshots(conn: sqlite3.Connection, folder: Path, *, days: int, max_bytes: int,
                    now: float | None = None) -> int:  # fmt: skip
    """The changed files' snapshots: each is as old as the youngest change or version of the files' history (stage
    26) that needs it (a card's export, or one nothing refers to, as its file). The history's rows past the term go
    with them."""
    now = time.time() if now is None else now
    used: dict[str, float] = {}
    rows = conn.execute(
        "SELECT before_hash, after_hash, created_at FROM file_changes UNION ALL"
        " SELECT hash, NULL, created_at FROM file_versions WHERE hash IS NOT NULL"
    ).fetchall()
    for row in rows:
        at = _seconds(row["created_at"])
        for name in (row["before_hash"], row["after_hash"]):
            if name and at > used.get(name, 0.0):
                used[name] = at
    found = [(path, size, max(used.get(path.name, 0.0), 0.0) or mtime) for path, size, mtime in _files(folder, "*")]
    cutoff = now - days * 86400
    since = datetime.fromtimestamp(cutoff, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute("DELETE FROM file_versions WHERE created_at < ?", (since,))
    return _thin(found, cutoff, max_bytes)


def prune_screens(folder: Path, *, days: int, max_bytes: int, now: float | None = None) -> int:
    """The agent's screen snapshots (data/computer/<run>/*.png); a run's folder left empty goes too."""
    now = time.time() if now is None else now
    removed = _thin(_files(folder, "*/*.png"), now - days * 86400, max_bytes)
    if folder.is_dir():
        for run in folder.iterdir():
            with contextlib.suppress(OSError):
                if run.is_dir() and not any(run.iterdir()):
                    run.rmdir()
    return removed


def prune_blobs(conn: sqlite3.Connection, folder: Path, *, days: int, now: float | None = None) -> int:
    """Files and pictures sent with messages (data/blobs): kept while a message names them - conversations are kept
    always (D30), and a picture goes with every request of its chat; one no message names (a send that was refused,
    a deleted chat) goes after `days`."""
    now = time.time() if now is None else now
    named: set[str] = set()
    for (content,) in conn.execute("SELECT content FROM messages WHERE content LIKE '%\"blob\"%'"):
        with contextlib.suppress(ValueError, TypeError):
            named |= {b["blob"] for b in json.loads(content) if isinstance(b, dict) and b.get("blob")}
    removed = 0
    for path, _size, mtime in _files(folder, "*"):
        if path.name not in named and mtime < now - days * 86400:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


def prune_copies(folder: Path, pattern: str, *, days: int, max_bytes: int, now: float | None = None) -> int:
    """Working copies of sent files - Claude Code's (data/attachments/<chat>/<message>/...), the window's pasted
    pictures (data/pasted): kept `days` and within `max_bytes`; empty folders go too."""
    now = time.time() if now is None else now
    removed = _thin(_files(folder, pattern), now - days * 86400, max_bytes)
    if folder.is_dir():
        for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            with contextlib.suppress(OSError):
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
    return removed


def _seconds(at: str | None) -> float:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def usage(root: Path) -> dict[str, dict[str, int]]:
    """How much each kind takes now: {kind: {bytes, files}}."""
    kinds = {"snapshots": _files(root / "snapshots", "*"), "screens": _files(root / "computer", "*/*.png"),
             "terminals": _files(root / "terminals", "*.log"), "files": _files(root / "blobs", "*"),
             "pictures": _files(root / "media", "*/*")}  # fmt: skip
    return {kind: {"bytes": sum(f[1] for f in found), "files": len(found)} for kind, found in kinds.items()}


def prune_all(conn: sqlite3.Connection, root: Path, now: float | None = None) -> dict[str, int]:
    """Every kind within the user's limits now: how many files each lost."""
    limits = current(conn)
    days, max_bytes = int(limits["days"]), int(float(limits["max_gb"]) * GIB)
    moment = datetime.fromtimestamp(now, UTC) if now is not None else None
    return {
        "snapshots": prune_snapshots(conn, root / "snapshots", days=days, max_bytes=max_bytes, now=now),
        "screens": prune_screens(root / "computer", days=days, max_bytes=max_bytes, now=now),
        "terminals": prune_logs(conn, root / "terminals", keep_days=days, max_total_bytes=max_bytes, now=moment),
        "files": prune_blobs(conn, root / "blobs", days=days, now=now),
        "copies": prune_copies(root / "attachments", "*/*/*", days=days, max_bytes=max_bytes, now=now)
        + prune_copies(root / "pasted", "*", days=days, max_bytes=max_bytes, now=now),
        "pictures": prune_copies(root / "media", "*/*", days=days, max_bytes=max_bytes, now=now),
    }


def goals_kept(conn: sqlite3.Connection, now: float | None = None) -> tuple[list[Path], set[str]]:
    """The projects' folders, and the goals whose checkpoints' refs stay (11.6): open ones, and those ended within
    D30's term; the others' refs go - and those of goals gone with their chat."""
    days = int(current(conn)["days"])
    cutoff = datetime.fromtimestamp((now if now is not None else time.time()) - days * 86400, UTC)
    since = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    live = {r[0] for r in conn.execute("SELECT id FROM goals WHERE ended_at IS NULL OR ended_at > ?", (since,))}
    return [Path(r[0]) for r in conn.execute("SELECT DISTINCT path FROM projects")], live


class Keeper:
    """Prunes at the hub's start and every PRUNE_EVERY_S after."""

    def __init__(self, db: Database, root: Path):
        self._db = db
        self._root = root
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def prune(self) -> dict[str, int]:
        removed = await self._db.acall(lambda conn: prune_all(conn, self._root))
        roots, live = await self._db.acall(goals_kept)
        removed["goal_refs"] = await asyncio.to_thread(
            lambda: sum(len(checkpoints.prune(root, live)) for root in roots))  # fmt: skip
        return removed

    async def _loop(self) -> None:
        while True:
            try:
                removed = await self.prune()
                if any(removed.values()):
                    log.info("kept within the limits (D30), removed: %s", removed)
            except Exception:
                log.exception("pruning what the hub keeps failed")
            await asyncio.sleep(PRUNE_EVERY_S)
