"""Processes and ports in project folders (stage 2, D4): what runs where, without its output.

Every few seconds the hub looks at the machine's processes (psutil). A process belongs to the project whose folder
holds its working directory or its program (the deepest project, when projects nest). For each one: command,
folder, listening TCP ports, CPU (a share of the whole machine, as Task Manager shows it) and memory. What a
process is does not change while it lives, so its details are read once per (pid, start time); only CPU and memory
are read on every scan.

The picture goes out as `processes.changed` (live only) whenever it differs from the last one; the ports listening
in projects' folders are kept then, with when they were last seen (25.3: a link's port evidence lives after its
process stops - hub/links.py). The `git` the hub itself runs for projects is left out: it is the hub's own reading,
not the project's work. Stopping a process is the app's alone, only for a process of a project and never for the
hub, and goes into the event log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psutil

from . import links
from .db import utc_now
from .projects import list_projects

log = logging.getLogger("hub.processes")

DEFAULT_INTERVAL_S = 3.0
STOP_WAIT_S = 3.0
CMDLINE_MAX = 500
HUB_HELPERS = {"git.exe", "git", "conhost.exe", "openconsole.exe"}  # what the hub runs for itself (consoles too)


@dataclass(frozen=True)
class Root:
    project_id: str
    path: str  # normalized (os.path.normcase), no trailing separator


def roots_of(projects: list[Any]) -> list[Root]:
    """Project folders, deepest first, so the first match is the most specific project."""
    found = [Root(p.id, os.path.normcase(os.path.normpath(p.path))) for p in projects]
    return sorted(found, key=lambda r: len(r.path), reverse=True)


def owner(roots: list[Root], *paths: str) -> tuple[str, str] | None:
    """(project id, path relative to it) of the first of `paths` that lies inside a project."""
    for path in paths:
        if not path:
            continue
        norm = os.path.normcase(os.path.normpath(path))
        for root in roots:
            if norm == root.path or norm.startswith(root.path + os.sep):
                return root.project_id, os.path.normpath(path)[len(root.path) + 1 :].replace("\\", "/")
    return None


@dataclass
class _Known:
    """What does not change while a process lives, read once."""

    process: psutil.Process
    name: str
    exe: str
    cwd: str
    cmdline: str
    args: str  # the command line without the program
    started_at: str


def _command(argv: list[str]) -> str:
    """Arguments as Windows would quote them, on one line."""
    return subprocess.list2cmdline(argv).replace("\r", " ").replace("\n", " ")[:CMDLINE_MAX]


class ProcessScanner:
    """One scan of the machine (blocking: run it on a worker thread)."""

    def __init__(self, own_pid: int | None = None):
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self._known: dict[tuple[int, float], _Known | None] = {}  # None: not readable, or not interesting
        self._cpus = psutil.cpu_count() or 1

    def _read(self, process: psutil.Process) -> _Known | None:
        try:
            with process.oneshot():
                name = process.name()
                exe = process.exe()
                cwd = process.cwd()
                argv = process.cmdline()
                started = process.create_time()
        except psutil.Error, OSError:
            return None
        process.cpu_percent(None)  # the first reading only starts the clock
        return _Known(
            process=process,
            name=name,
            exe=exe,
            cwd=cwd,
            cmdline=_command(argv),
            args=_command(argv[1:]),
            started_at=datetime.fromtimestamp(started, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def scan(self, projects: list[Any]) -> dict[str, list[dict[str, Any]]]:
        """project id -> its processes, each {pid, ppid, name, exe, cwd (relative), cmdline, started_at, cpu,
        memory_mb, ports, hub}, parents before their children."""
        roots = roots_of(projects)
        if not roots:
            self._known.clear()
            return {}
        listening: dict[int, set[int]] = {}
        with contextlib.suppress(psutil.Error, OSError):
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status == psutil.CONN_LISTEN and conn.pid and conn.laddr:
                    listening.setdefault(conn.pid, set()).add(conn.laddr.port)
        alive: set[tuple[int, float]] = set()
        found: dict[str, list[dict[str, Any]]] = {}
        for process in psutil.process_iter(["pid", "ppid", "create_time"]):
            info = process.info
            if info["create_time"] is None:
                continue
            key = (info["pid"], info["create_time"])
            alive.add(key)
            if key not in self._known:
                self._known[key] = self._read(process)
            known = self._known[key]
            if known is None:
                continue
            if info["ppid"] == self.own_pid and known.name.lower() in HUB_HELPERS:
                continue
            place = owner(roots, known.cwd, known.exe)
            if place is None:
                continue
            in_cwd = owner(roots, known.cwd)
            try:
                cpu = known.process.cpu_percent(None) / self._cpus
                memory = known.process.memory_info().rss / 1024 / 1024
            except psutil.Error, OSError:
                continue
            project_id = place[0]
            found.setdefault(project_id, []).append(
                {
                    "pid": info["pid"],
                    "ppid": info["ppid"],
                    "name": known.name,
                    "exe": known.exe,
                    "cwd": in_cwd[1] if in_cwd and in_cwd[0] == project_id else None,  # None: elsewhere
                    "cmdline": known.cmdline,
                    "args": known.args,
                    "started_at": known.started_at,
                    "cpu": round(cpu, 1),
                    "memory_mb": round(memory),
                    "ports": sorted(listening.get(info["pid"], ())),
                    "hub": info["pid"] == self.own_pid,
                }
            )
        for key in [k for k in self._known if k not in alive]:
            del self._known[key]
        return {pid: as_tree(without_launchers(found[pid])) for pid in sorted(found)}


def _ports(found: dict[str, list[dict[str, Any]]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (project_id, port): p
        for project_id, processes in found.items()
        for p in processes
        if not p.get("hub")
        for port in p["ports"]
    }


def port_changes(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> list[tuple[str, dict[str, Any]]]:
    """Ports that opened or closed in projects between two scans, as events for the log: a server started or
    stopped is what a person wants to see later. The hub's own port is not news."""
    old, new = _ports(before), _ports(after)
    changes = []
    for key in sorted(new.keys() - old.keys()):
        p = new[key]
        changes.append(("port.opened", {"project_id": key[0], "port": key[1], "pid": p["pid"], "name": p["name"],
                                        "args": p.get("args", "")}))  # fmt: skip
    for key in sorted(old.keys() - new.keys()):
        p = old[key]
        changes.append(("port.closed", {"project_id": key[0], "port": key[1], "pid": p["pid"], "name": p["name"]}))
    return changes


def without_launchers(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A virtual environment's python.exe only starts the real interpreter with the same arguments and waits for
    it: the pair is shown as one process, the real one (it holds the ports), under the launcher's parent."""
    by_pid = {p["pid"]: p for p in processes}
    launchers = set()
    for p in processes:
        parent = by_pid.get(p["ppid"])
        if (
            parent is not None
            and parent["pid"] != p["pid"]
            and parent["name"].lower() == p["name"].lower()
            and parent.get("args") == p.get("args")
            and not parent["ports"]
        ):
            launchers.add(parent["pid"])
    out = []
    for p in processes:
        if p["pid"] in launchers:
            continue
        ppid, seen = p["ppid"], set()
        while ppid in launchers and ppid not in seen:
            seen.add(ppid)
            ppid = by_pid[ppid]["ppid"]
        out.append({**p, "ppid": ppid})
    return out


def as_tree(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parents before their children, each with its depth; the rest by start time."""
    by_pid = {p["pid"]: p for p in processes}
    children: dict[int, list[dict[str, Any]]] = {}
    tops = []
    for p in sorted(processes, key=lambda p: (p["started_at"], p["pid"])):
        if p["ppid"] in by_pid and p["ppid"] != p["pid"]:
            children.setdefault(p["ppid"], []).append(p)
        else:
            tops.append(p)
    out: list[dict[str, Any]] = []

    def walk(p: dict[str, Any], depth: int) -> None:
        out.append({**p, "depth": depth})
        for child in children.get(p["pid"], []):
            walk(child, depth + 1)

    for p in tops:
        walk(p, 0)
    return out


class ProcessMonitor:
    """Scans on an interval and publishes `processes.changed` when the picture changes."""

    def __init__(self, bus, db, interval_s: float = DEFAULT_INTERVAL_S, scanner: ProcessScanner | None = None):
        self._bus = bus
        self._db = db
        self._interval = interval_s
        self._scanner = scanner or ProcessScanner()
        self._task: asyncio.Task | None = None
        self._extra: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self.latest: dict[str, list[dict[str, Any]]] | None = None

    async def sample(self) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:  # one scan at a time: the scanner keeps state between scans
            projects = await self._db.acall(list_projects)
            found = await asyncio.to_thread(self._scanner.scan, projects)
            if found != self.latest:
                before, self.latest = self.latest, found
                heard, now, at = links.listening(before or {}), links.listening(found), utc_now()
                if heard != now:  # a port opened or closed (CPU and memory change the picture every scan)
                    await self._db.acall(lambda conn: links.remember_ports(conn, heard, now, at))
                await self._bus.publish("processes.changed", {"projects": found}, persist=False)
                if before is not None:  # the first scan is where things stand, not something that happened
                    for kind, payload in port_changes(before, found):
                        await self._bus.publish(kind, payload, project_id=payload["project_id"])
            return found

    def project_of(self, pid: int) -> tuple[str, dict[str, Any]] | None:
        for project_id, processes in (self.latest or {}).items():
            for process in processes:
                if process["pid"] == pid:
                    return project_id, process
        return None

    def soon(self) -> None:
        """A scan now rather than at the next tick (a project was added or removed)."""
        if self._interval > 0:
            task = asyncio.create_task(self._sample_quietly())
            self._extra.add(task)
            task.add_done_callback(self._extra.discard)

    async def _sample_quietly(self) -> None:
        try:
            await self.sample()
        except Exception:
            log.exception("process scan failed")

    def start(self) -> None:
        if self._interval > 0:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for task in [self._task, *self._extra]:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _loop(self) -> None:
        while True:
            await self._sample_quietly()
            await asyncio.sleep(self._interval)


def stop_tree(pid: int, wait_s: float = STOP_WAIT_S) -> list[int]:
    """Ends the process and its children (children first, so a parent does not start them again), waits for
    them, and kills what is still there. On Windows ending is immediate: a console program has no polite
    request to quit that another process can send. Returns the pids that were running."""
    try:
        top = psutil.Process(pid)
        tree = [top, *top.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []
    for process in reversed(tree):
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _gone, alive = psutil.wait_procs(tree, timeout=wait_s)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    psutil.wait_procs(alive, timeout=wait_s)
    return [p.pid for p in tree]


def is_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).is_running()
    except psutil.Error:
        return False


def listener(port: int) -> dict[str, Any] | None:
    """Who listens on this TCP port of this computer: {pid, name, exe} ('' for what cannot be read); None when nobody
    does, or it cannot be seen (23.4: llama-swap's port held by another program)."""
    with contextlib.suppress(psutil.Error, OSError):
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid:
                found = {"pid": conn.pid, "name": "", "exe": ""}
                for key in ("name", "exe"):
                    with contextlib.suppress(psutil.Error, OSError):
                        found[key] = getattr(psutil.Process(conn.pid), key)()
                return found
    return None
