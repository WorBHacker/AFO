"""Terminals the hub runs (stage 2, D4, D28, D30): a shell or a command in a Windows pseudo-console (ConPTY, through
pywinpty), its output live to clients and into a log file.

A terminal belongs to the hub, not to a window: closing the app leaves it running, and any window can show it again
(recent output comes from memory, older from the log). Each terminal has a reader thread that polls the console;
what it reads goes to the log at once and to clients as `terminal.output` {terminal_id, offset, data}, gathered
over OUTPUT_FLUSH_S so a chatty program does not flood the event stream. `offset` counts characters from the start,
so a client that read the tail first knows which events it already has.

Logs (D30): data/terminals/<id>.log, raw (the console's escape sequences included), at most LOG_MAX_BYTES each; kept
LOG_KEEP_DAYS and within LOGS_MAX_TOTAL_BYTES in all, the oldest logs of ended terminals going first. The row stays.
Starting, typing into and stopping terminals is the app's alone: a terminal runs anything (rights come with D17-D19).
When the hub stops, its terminals and what runs in them end with it; a terminal that was running when the hub died
is marked 'lost'.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

import winpty

from common.i18n import N_, tr

from .db import utc_now
from .processes import stop_tree

log = logging.getLogger("hub.terminals")

OUTPUT_FLUSH_S = 0.03
POLL_S = 0.02
GATHER_CHARS = 64 * 1024  # output read back to back goes on as one piece, up to this much
TAIL_CHARS = 256 * 1024  # recent output kept in memory for a window that opens the terminal later
LOG_MAX_BYTES = 50 * 1024 * 1024
LOG_KEEP_DAYS = 90
LOGS_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
PRUNE_EVERY_S = 6 * 3600
LOG_CUT = N_("[AFO: журнал достиг предела, дальше вывод не сохраняется]")  # in yellow, on a line of its own
SHELLS = ("pwsh", "powershell", "cmd")


class TerminalError(Exception):
    """The message is fit to show."""


def find_shell(name: str = "") -> tuple[str, str]:
    """(kind, full path) of the shell: the one asked for, else PowerShell 7, Windows PowerShell, cmd."""
    for kind in (name,) if name else SHELLS:
        path = shutil.which(kind)
        if path:
            return kind, path
    raise TerminalError(f"no shell found: {name or ', '.join(SHELLS)}")


def shell_args(kind: str, command: str | None) -> list[str]:
    """Arguments for an interactive shell, or for running one command and ending with its exit code."""
    if kind == "cmd":
        return ["/d", "/c", command] if command else ["/d"]
    return ["-NoLogo", "-Command", command] if command else ["-NoLogo"]


@dataclass(eq=False)
class _Live:
    """A running terminal: its console, its log, and what it printed lately."""

    id: str
    project_id: str | None
    pty: Any
    pid: int
    log: TextIO | None
    log_bytes: int = 0
    log_cut: bool = False
    offset: int = 0
    tail: collections.deque[str] = field(default_factory=collections.deque)
    tail_chars: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None
    stopping: bool = False
    pending: list[str] = field(default_factory=list)  # read, not yet published (on the event loop)
    pending_offset: int = 0


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) | {"log_deleted": bool(row["log_deleted"])}


class TerminalManager:
    def __init__(self, bus, db, folder: Path):
        self._bus = bus
        self._db = db
        self._folder = folder
        self._live: dict[str, _Live] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        self._prune_task: asyncio.Task | None = None
        self._starting = asyncio.Lock()  # a terminal starting: stop() waits till it is one of the live ones
        self._closed = False  # stopped: no terminal starts any more (the hub stops, 2026-11-11)

    # --- life of the manager ---

    async def start(self, *, prune: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        lost = await self._db.acall(
            lambda conn: (
                conn.execute(
                    "UPDATE terminals SET status = 'lost', ended_at = ? WHERE status = 'running'", (utc_now(),)
                ).rowcount
            )
        )
        if lost:
            log.info("%d terminal(s) were running when the hub stopped: marked lost", lost)
        if prune:
            self._prune_task = asyncio.create_task(self._prune_loop())

    async def stop(self) -> None:
        """Ends every terminal (and what runs in it): nothing the hub started outlives it - one starting now ends
        with the rest, and none starts after."""
        async with self._starting:
            self._closed = True
        if self._prune_task is not None:
            self._prune_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._prune_task
        for live in list(self._live.values()):
            live.stopping = True
            await asyncio.to_thread(stop_tree, live.pid, 2.0)
        for live in list(self._live.values()):
            if live.thread is not None:
                await asyncio.to_thread(live.thread.join, 5)
        await asyncio.sleep(0)  # let the readers' last callbacks run
        for live in list(self._live.values()):
            await self._ended(live.id, None)
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- reading ---

    async def list(self, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        def query(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            where, args = ("WHERE project_id = ?", [project_id]) if project_id else ("", [])
            return conn.execute(
                f"SELECT * FROM terminals {where} ORDER BY status = 'running' DESC, created_at DESC LIMIT ?",
                [*args, limit],
            ).fetchall()

        return [self._json(_row(r)) for r in await self._db.acall(query)]

    async def get(self, terminal_id: str) -> dict[str, Any]:
        row = await self._db.acall(
            lambda conn: conn.execute("SELECT * FROM terminals WHERE id = ?", (terminal_id,)).fetchone()
        )
        if row is None:
            raise TerminalError(f"no such terminal: {terminal_id}")
        return self._json(_row(row))

    def _json(self, row: dict[str, Any]) -> dict[str, Any]:
        live = self._live.get(row["id"])
        if live is not None:
            row["log_bytes"] = live.log_bytes
        return row

    def log_path(self, terminal_id: str) -> Path:
        return self._folder / f"{terminal_id}.log"

    async def output(self, terminal_id: str, max_chars: int = 64 * 1024, since: int | None = None) -> dict[str, Any]:
        """The last output: from memory while it runs (with `start` and `end`, the offsets of its first character and
        right after it), else from its log. `since`: from that offset on, when memory still has it all - a window
        that missed events fills the gap (D46); otherwise the last output as usual, and `start` says so."""
        live = self._live.get(terminal_id)
        if live is not None:
            with live.lock:
                text = "".join(live.tail)
                end = live.offset
            first = end - len(text)
            if since is not None and first <= since <= end and end - since <= max_chars:
                return {"data": text[since - first :], "start": since, "end": end, "running": True}
            data = text[-max_chars:]
            return {"data": data, "start": end - len(data), "end": end, "running": True}
        info = await self.get(terminal_id)
        if info["log_deleted"]:
            return {"data": "", "end": None, "running": False, "log_deleted": True}
        return {"data": await asyncio.to_thread(_read_tail, self.log_path(terminal_id), max_chars), "end": None,
                "running": False}  # fmt: skip

    # --- starting, typing, stopping ---

    async def create(
        self,
        *,
        cwd: Path,
        by: str,
        project_id: str | None = None,
        command: str | None = None,
        shell: str = "",
        cols: int = 120,
        rows: int = 30,
        title: str = "",
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """A shell (running `command`, or interactive), or with `argv` a program of the hub's choice run directly;
        `env`: variables set for it on top of the hub's own."""
        if not cwd.is_dir():
            raise TerminalError(f"no such folder: {cwd}")
        cols, rows = max(20, min(cols, 500)), max(5, min(rows, 200))
        if argv:
            kind, program, args = "program", argv[0], argv[1:]
        else:
            kind, program = find_shell(shell)
            args = shell_args(kind, command)
        async with self._starting:  # from here till it is live, stop() waits: it ends this one with the rest
            if self._closed:
                raise TerminalError("the hub is stopping: no terminal starts now")
            terminal_id = uuid.uuid4().hex[:12]
            pty = winpty.PTY(cols, rows)
            block = "".join(f"{k}={v}\0" for k, v in {**os.environ, **env}.items()) if env else None
            try:
                spawned = await asyncio.to_thread(
                    pty.spawn, program, cmdline=" " + subprocess.list2cmdline(args), cwd=str(cwd), env=block
                )
            except winpty.WinptyError as exc:
                raise TerminalError(f"the terminal did not start: {exc}") from exc
            if not spawned:
                raise TerminalError("the terminal did not start")
            shown = subprocess.list2cmdline([program, *args])
            self._folder.mkdir(parents=True, exist_ok=True)
            live = _Live(
                id=terminal_id,
                project_id=project_id,
                pty=pty,
                pid=pty.pid,
                log=self.log_path(terminal_id).open("a", encoding="utf-8", newline=""),
            )
            await self._db.acall(
                lambda conn: conn.execute(
                    "INSERT INTO terminals (id, project_id, title, command, cwd, cols, rows, pid, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (terminal_id, project_id, title or command or kind, shown, str(cwd), cols, rows, live.pid, by),
                )
            )
            self._live[terminal_id] = live
            live.thread = threading.Thread(
                target=self._read_loop, args=(live,), name=f"terminal-{terminal_id}", daemon=True
            )
            live.thread.start()
        info = await self.get(terminal_id)
        await self._bus.publish("terminal.started", {"terminal": info}, project_id=project_id)
        log.info("terminal %s started (pid %s): %s in %s", terminal_id, live.pid, shown, cwd)
        return info

    def _running(self, terminal_id: str) -> _Live:
        live = self._live.get(terminal_id)
        if live is None:
            raise TerminalError(f"the terminal is not running: {terminal_id}")
        return live

    async def write(self, terminal_id: str, data: str) -> None:
        live = self._running(terminal_id)
        try:
            await asyncio.to_thread(live.pty.write, data)
        except winpty.WinptyError as exc:
            raise TerminalError(f"the terminal does not take input: {exc}") from exc

    async def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        live = self._running(terminal_id)
        cols, rows = max(20, min(cols, 500)), max(5, min(rows, 200))
        await asyncio.to_thread(live.pty.set_size, cols, rows)
        await self._db.acall(
            lambda conn: conn.execute("UPDATE terminals SET cols = ?, rows = ? WHERE id = ?", (cols, rows, terminal_id))
        )

    async def kill(self, terminal_id: str) -> None:
        """Ends the terminal's program and everything started from it."""
        live = self._running(terminal_id)
        live.stopping = True
        await asyncio.to_thread(stop_tree, live.pid, 2.0)

    async def remove(self, terminal_id: str) -> None:
        """Forgets an ended terminal and deletes its log."""
        if terminal_id in self._live:
            raise TerminalError("the terminal is still running; stop it first")
        await self.get(terminal_id)
        with contextlib.suppress(FileNotFoundError):
            self.log_path(terminal_id).unlink()
        await self._db.acall(lambda conn: conn.execute("DELETE FROM terminals WHERE id = ?", (terminal_id,)))

    # --- the reader thread ---

    def _read_loop(self, live: _Live) -> None:
        """ConPTY hands output over a line or two at a time: pieces read back to back are recorded as one (up to
        GATHER_CHARS), so a flood of output flushes the log and wakes the event loop rarely (D46, step 13.7)."""
        pty = live.pty
        gathered: list[str] = []
        size = 0
        while True:
            try:
                data = pty.read(blocking=False)
            except winpty.WinptyError:
                break
            if data:
                gathered.append(data)
                size += len(data)
                if size < GATHER_CHARS:
                    continue
            if gathered:
                self._record(live, "".join(gathered))
                gathered, size = [], 0
                continue
            if not pty.isalive():
                with contextlib.suppress(winpty.WinptyError):
                    rest = pty.read(blocking=False)
                    if rest:
                        self._record(live, rest)
                break
            time.sleep(POLL_S)
        if gathered:  # the console broke off after them
            self._record(live, "".join(gathered))
        code = None
        for _ in range(50):  # the exit code may lag the end of the process a little
            with contextlib.suppress(winpty.WinptyError):
                code = pty.get_exitstatus()
            if code is not None:
                break
            time.sleep(0.02)
        self._to_loop(self._ended_soon, live.id, code)

    def _record(self, live: _Live, data: str) -> None:
        with live.lock:
            start = live.offset
            live.offset += len(data)
            live.tail.append(data)
            live.tail_chars += len(data)
            while live.tail_chars > TAIL_CHARS and len(live.tail) > 1:
                live.tail_chars -= len(live.tail.popleft())
            if live.log is not None and not live.log_cut:
                size = len(data.encode("utf-8", "replace"))
                try:
                    if live.log_bytes + size > LOG_MAX_BYTES:
                        live.log.write(f"\r\n\x1b[33m{tr(LOG_CUT)}\x1b[0m\r\n")
                        live.log_cut = True
                    else:
                        live.log.write(data)
                        live.log_bytes += size
                    live.log.flush()
                except OSError as exc:
                    log.warning("terminal %s: log not written (%s)", live.id, exc)
        self._to_loop(self._queue_output, live.id, start, data)

    def _to_loop(self, callback, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # the loop is closing
            loop.call_soon_threadsafe(callback, *args)

    # --- on the event loop ---

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _queue_output(self, terminal_id: str, offset: int, data: str) -> None:
        live = self._live.get(terminal_id)
        if live is None:
            return
        if not live.pending:
            live.pending_offset = offset
            self._spawn(self._flush_later(terminal_id))
        live.pending.append(data)

    async def _flush_later(self, terminal_id: str) -> None:
        await asyncio.sleep(OUTPUT_FLUSH_S)
        await self._flush(terminal_id)

    async def _flush(self, terminal_id: str) -> None:
        live = self._live.get(terminal_id)
        if live is not None:
            await self._publish_pending(live)

    async def _publish_pending(self, live: _Live) -> None:
        if not live.pending:
            return
        terminal_id = live.id
        data, offset = "".join(live.pending), live.pending_offset
        live.pending.clear()
        await self._bus.publish(
            "terminal.output",
            {"terminal_id": terminal_id, "offset": offset, "data": data},
            project_id=live.project_id,
            persist=False,
        )

    def _ended_soon(self, terminal_id: str, code: int | None) -> None:
        self._spawn(self._ended(terminal_id, code))

    async def _ended(self, terminal_id: str, code: int | None) -> None:
        live = self._live.pop(terminal_id, None)  # first, so a second call (the reader and stop()) does nothing
        if live is None:
            return
        await self._publish_pending(live)  # the last output goes before the end
        with live.lock:
            if live.log is not None:
                with contextlib.suppress(OSError):
                    live.log.close()
                live.log = None
        await self._db.acall(
            lambda conn: conn.execute(
                "UPDATE terminals SET status = 'exited', exit_code = ?, ended_at = ?, log_bytes = ? WHERE id = ?",
                (code, utc_now(), live.log_bytes, terminal_id),
            )
        )
        info = await self.get(terminal_id)
        await self._bus.publish(
            "terminal.exited",
            {"terminal_id": terminal_id, "exit_code": code, "title": info["title"], "stopped": live.stopping},
            project_id=live.project_id,
        )
        log.info("terminal %s ended with %s", terminal_id, code)

    # --- keeping logs within limits (D30) ---

    async def _prune_loop(self) -> None:
        while True:
            try:
                removed = await self._db.acall(lambda conn: prune_logs(conn, self._folder))
                if removed:
                    log.info("terminal logs removed: %d", removed)
            except Exception:
                log.exception("pruning terminal logs failed")
            await asyncio.sleep(PRUNE_EVERY_S)


def _read_tail(path: Path, max_chars: int) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_chars * 4))
            data = handle.read()
    except FileNotFoundError:
        return ""
    return data.decode("utf-8", "replace")[-max_chars:]


def prune_logs(
    conn: sqlite3.Connection,
    folder: Path,
    *,
    keep_days: int = LOG_KEEP_DAYS,
    max_total_bytes: int = LOGS_MAX_TOTAL_BYTES,
    now: datetime | None = None,
) -> int:
    """Deletes the logs of ended terminals older than `keep_days`, then the oldest ones until all logs fit in
    `max_total_bytes`. Running terminals keep theirs. Returns how many were deleted."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%S")
    ended = conn.execute(
        "SELECT id, COALESCE(ended_at, created_at) AS at FROM terminals"
        " WHERE status != 'running' AND log_deleted = 0 ORDER BY at"
    ).fetchall()
    sizes = {}
    for row in conn.execute("SELECT id FROM terminals WHERE log_deleted = 0").fetchall():
        with contextlib.suppress(OSError):
            sizes[row["id"]] = (folder / f"{row['id']}.log").stat().st_size
    total = sum(sizes.values())
    removed = 0
    for row in ended:
        if row["at"] >= cutoff and total <= max_total_bytes:
            break
        with contextlib.suppress(FileNotFoundError):
            (folder / f"{row['id']}.log").unlink()
        total -= sizes.get(row["id"], 0)
        conn.execute("UPDATE terminals SET log_deleted = 1 WHERE id = ?", (row["id"],))
        removed += 1
    return removed
