"""Copies of the hub's database (D29), by SQLite's own backup, into data/backups: one a day, one before migrations
(D46), and the database a restore replaced.

The copy reads through a connection of its own: under WAL a reader neither waits for the hub's writes nor holds
them up, and one backup step reads one consistent state. The last KEEP daily copies stay, older ones go; of the
copies before migrations and of the replaced databases the last KEEP_OTHER stay. `python -m hub db restore` lists
them and puts one back while the hub is stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from common.i18n import decimal, tr

from .db import Database, available_migrations, current_version, migrate

log = logging.getLogger("hub.backup")

KEEP = 7  # daily copies kept
KEEP_OTHER = 3  # copies before migrations, and replaced databases, kept of each
START_DELAY_S = 60.0  # the first check waits for the hub to settle
PREFIX, SUFFIX = "hub-", ".db"
BEFORE = "before-v"  # before-v033-20261103-101500.db: the database at schema 33, before newer migrations
REPLACED = "replaced-"  # replaced-20261103-101500.db: the database a restore put a copy in place of


def backup_path(folder: Path, day: date) -> Path:
    return folder / f"{PREFIX}{day.isoformat()}{SUFFIX}"


def copies(folder: Path) -> list[Path]:
    """The daily copies in the folder, oldest first (the names sort by date)."""
    return sorted(p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if _day(p) is not None)


def _day(path: Path) -> date | None:
    with contextlib.suppress(ValueError):
        return date.fromisoformat(path.name[len(PREFIX) : -len(SUFFIX)])
    return None


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")  # a copy cut short never looks like a finished one
    partial.unlink(missing_ok=True)
    src = _read_only(source)
    try:
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    partial.replace(target)


def _keep_last(folder: Path, prefix: str, keep: int) -> None:
    for old in sorted(folder.glob(f"{prefix}*{SUFFIX}"))[:-keep]:
        old.unlink(missing_ok=True)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def back_up(source: Path, folder: Path, day: date, *, keep: int = KEEP) -> Path | None:
    """Today's copy, unless it is there already; then only the last `keep` copies stay. Returns the new copy."""
    target = backup_path(folder, day)
    made = None
    if not target.exists():
        _copy(source, target)
        made = target
    for old in copies(folder)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return made


def migrate_keeping_a_copy(db: Database, folder: Path, now: datetime | None = None) -> list[int]:
    """The migrations the database does not have yet, after a copy of it as it was: a migration that goes wrong, or
    a newer hub one goes back from, leaves the old state to restore. A new database has nothing to keep; a copy that
    fails is logged and the migrations go on, as they did before there were copies."""
    have = db.call(current_version)
    if have and have < available_migrations()[-1][0]:
        target = folder / f"{BEFORE}{have:03d}-{_stamp(now or datetime.now())}{SUFFIX}"
        try:
            _copy(db.path, target)
            _keep_last(folder, BEFORE, KEEP_OTHER)
            log.info("database copied to %s before migrations", target)
        except OSError, sqlite3.Error:
            log.exception("the copy of the database before migrations failed; migrating without it")
    return db.call(migrate)


def restorable(folder: Path) -> list[Path]:
    """Every copy a restore can take, oldest first: daily ones, the ones before migrations, replaced databases."""
    found = [*copies(folder), *folder.glob(f"{BEFORE}*{SUFFIX}"), *folder.glob(f"{REPLACED}*{SUFFIX}")]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def describe(path: Path) -> str:
    """One line for the list: name, when, size, the schema it has."""
    when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    size = decimal(tr("{size:.1f} МБ", size=path.stat().st_size / 1024 / 1024))
    try:
        conn = _read_only(path)
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        finally:
            conn.close()
        schema = tr("схема {version}", version=version)
    except sqlite3.Error:
        schema = tr("не читается")
    return f"{path.name:40} {when}  {size:>9}  {schema}"


def restore(copy: Path, target: Path, folder: Path, now: datetime | None = None) -> Path | None:
    """Puts `copy` in place of the database `target` - only while no hub runs (the caller holds its lock). The copy
    is checked first; the database it replaces is kept as a copy too, so a restore can be undone the same way.
    SQLite's backup writes into the database in one transaction: cut short, it leaves the old one. Returns the
    copy of the replaced database (None when there was none)."""
    conn = _read_only(copy)
    try:
        verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    if verdict != "ok":
        raise ValueError(tr("копия {name} повреждена: {verdict}", name=copy.name, verdict=verdict))
    kept = None
    if target.exists():
        kept = folder / f"{REPLACED}{_stamp(now or datetime.now())}{SUFFIX}"
        _copy(target, kept)
        _keep_last(folder, REPLACED, KEEP_OTHER)
    src = _read_only(copy)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the restored state in the file itself
        finally:
            dst.close()
    finally:
        src.close()
    return kept


class DailyBackup:
    """Checks every `interval_s` whether today's copy exists, and makes it if not."""

    def __init__(self, source: Path, folder: Path, interval_s: float, *, keep: int = KEEP):
        self._source = source
        self._folder = folder
        self._interval = interval_s
        self._keep = keep
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Only for the real hub (interval > 0)."""
        if self._interval > 0:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        await asyncio.sleep(START_DELAY_S)
        while True:
            await self.check()
            await asyncio.sleep(self._interval)

    async def check(self) -> Path | None:
        try:
            made = await asyncio.to_thread(back_up, self._source, self._folder, date.today(), keep=self._keep)
        except OSError, sqlite3.Error:
            log.exception("the daily copy of the database failed")
            return None
        if made is not None:
            log.info("database copied to %s", made)
        return made
