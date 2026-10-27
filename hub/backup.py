"""A copy of the hub's database once a day (D29), by SQLite's own backup, into data/backups.

The copy reads through a connection of its own: under WAL a reader neither waits for the hub's writes nor holds
them up, and one backup step reads one consistent state. The last KEEP daily copies stay, older ones go.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from datetime import date
from pathlib import Path

log = logging.getLogger("hub.backup")

KEEP = 7  # daily copies kept
START_DELAY_S = 60.0  # the first check waits for the hub to settle
PREFIX, SUFFIX = "hub-", ".db"


def backup_path(folder: Path, day: date) -> Path:
    return folder / f"{PREFIX}{day.isoformat()}{SUFFIX}"


def copies(folder: Path) -> list[Path]:
    """The daily copies in the folder, oldest first (the names sort by date)."""
    return sorted(p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if _day(p) is not None)


def _day(path: Path) -> date | None:
    with contextlib.suppress(ValueError):
        return date.fromisoformat(path.name[len(PREFIX) : -len(SUFFIX)])
    return None


def back_up(source: Path, folder: Path, day: date, *, keep: int = KEEP) -> Path | None:
    """Today's copy, unless it is there already; then only the last `keep` copies stay. Returns the new copy."""
    target = backup_path(folder, day)
    made = None
    if not target.exists():
        folder.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")  # a copy cut short never looks like a finished one
        partial.unlink(missing_ok=True)
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(partial)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        partial.replace(target)
        made = target
    for old in copies(folder)[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return made


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
