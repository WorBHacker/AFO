"""Watching project folders (stage 2, D4, D28): what changed on disk and in git, pushed to clients as events.

One watchdog observer (ReadDirectoryChangesW on Windows) watches every project folder recursively. Raw events come
on the observer's thread in bursts - an editor's save is several, a `git checkout` thousands - so they are sorted
there, collected per project and handled on the event loop FLUSH_S after the first one:

- noise is dropped first: .git internals other than what moves git's state (HEAD, index, refs), cache folders
  (node_modules, .venv, ...), the hub's own data folder, and then whatever git ignores;
- `project.files` {project_id, changed, folders, more} names the files that changed and the folders that gained or
  lost entries (`more`: too many to list, reload everything);
- git is read again, and `project.git` (status and recent commits) goes out when it differs from the last one.

Both events are live only (not stored): the files and git are the record. Git runs read-only and without the index
lock (hub/projects.py), so reading it never sets off the watcher again.

A project's folder that goes away (2026-10-28; it was silently no longer watched until the hub restarted) is noticed
within `check_s` (the real hub's 5 s): renamed or moved on the same disk - found again by its file id (NTFS keeps
it) among its old neighbours, the project's path changes and watching goes on (`project.moved`); gone - not watched,
`project.missing`; back again - watched, `project.found`. Those three are stored: they are the feed's.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import ObservedWatch

from . import projects
from .projects import CACHES, Project, ProjectError

log = logging.getLogger("hub.watch")


def _observer() -> Any:
    """The observer, with watchdog's Windows emitter closing its folder's handle once. watchdog 6 closes it when the
    watched folder is deleted, and again when the watch is unscheduled: the second CloseHandle hits whatever handle
    reused that number - the whole hub fell («Fatal Python error: ... ReleaseSemaphore failed», 2026-10-28)."""
    if sys.platform != "win32":
        return Observer()
    from watchdog.observers import winapi
    from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT, BaseObserver
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    class OnceEmitter(WindowsApiEmitter):
        def on_thread_stop(self) -> None:
            # no lock: watchdog stops a deleted folder's emitter from inside its own (not re-entrant) lock
            handle, self._whandle = self._whandle, None
            if handle:
                winapi.close_directory_handle(handle)

    return BaseObserver(OnceEmitter, timeout=DEFAULT_OBSERVER_TIMEOUT)


FLUSH_S = 0.4
MAX_LISTED = 200
GIT_STATE_FILES = {"HEAD", "index", "packed-refs", "MERGE_HEAD", "ORIG_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"}
CHANGES = {"created", "deleted", "moved", "modified"}


@dataclass
class Batch:
    """What one or more raw events changed: files, folders whose entries changed, and git's own state."""

    changed: set[str] = field(default_factory=set)
    folders: set[str] = field(default_factory=set)
    git: bool = False

    def __bool__(self) -> bool:
        return bool(self.changed or self.folders or self.git)

    def merge(self, other: Batch) -> None:
        self.changed |= other.changed
        self.folders |= other.folders
        self.git = self.git or other.git


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def relative(root: str, path: str, ignore: Iterable[str] = ()) -> str | None:
    """`path` relative to `root` (posix), or None when it is outside the root or inside an ignored folder.
    `root` and `ignore` are normalized (_norm)."""
    norm = _norm(path)
    if any(norm == i or norm.startswith(i + os.sep) for i in ignore):
        return None
    if norm == root:
        return ""
    if not norm.startswith(root + os.sep):
        return None
    return os.path.normpath(path)[len(root) + 1 :].replace("\\", "/")


def sort_event(
    root: str, kind: str, src: str, dest: str = "", *, is_dir: bool = False, ignore: Iterable[str] = ()
) -> Batch:
    """One raw file-system event as a Batch (empty when it is noise)."""
    batch = Batch()
    if kind not in CHANGES:
        return batch
    for path in (src, dest) if kind == "moved" else (src,):
        rel = relative(root, path, ignore)
        if not rel:
            continue
        if rel == ".git" or rel.startswith(".git/"):
            inner = rel[5:]
            batch.git = batch.git or inner in GIT_STATE_FILES or inner.startswith("refs/")
            continue
        parts = rel.split("/")
        if any(part in CACHES for part in parts):
            continue
        if kind == "modified":
            if not is_dir:  # a folder is "modified" when something inside it changes; that is reported itself
                batch.changed.add(rel)
            continue
        batch.folders.add("/".join(parts[:-1]))
        if not is_dir:
            batch.changed.add(rel)
    return batch


class _Handler(FileSystemEventHandler):
    """Runs on the observer's thread: sorts each event and hands what is left to the event loop."""

    def __init__(self, watcher: ProjectWatcher, project_id: str, root: str):
        self._watcher = watcher
        self._project_id = project_id
        self._root = _norm(root)

    def on_any_event(self, event: FileSystemEvent) -> None:
        batch = sort_event(
            self._root,
            event.event_type,
            os.fsdecode(event.src_path),
            os.fsdecode(event.dest_path) if event.dest_path else "",
            is_dir=event.is_directory,
            ignore=self._watcher.ignore,
        )
        if batch:
            self._watcher.from_thread(self._project_id, batch)


class ProjectWatcher:
    """Watches projects' folders and publishes `project.files` and `project.git` on the bus."""

    def __init__(self, bus, *, ignore: Iterable[Path] = (), flush_s: float = FLUSH_S, listed=None, relocate=None,
                 check_s: float = 0.0):  # fmt: skip
        """`listed`: async, every listed project now; `relocate`: async (project_id, new folder) -> the project as it
        is then - both from the hub's database, for the folders' check (none given: no check)."""
        self._bus = bus
        self._listed, self._relocate, self._check_s = listed, relocate, check_s
        self._ids: dict[str, tuple[int, int]] = {}  # project -> its folder's (device, file id), to find it moved
        self._missing: set[str] = set()
        self.ignore = tuple(_norm(str(p)) for p in ignore)  # folders never reported, e.g. the hub's own data
        self._flush_s = flush_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer: Any = None
        self._watches: dict[str, tuple[Project, ObservedWatch]] = {}
        self._pending: dict[str, Batch] = {}
        self._git_last: dict[str, dict[str, Any]] = {}
        self._git_running: set[str] = set()
        self._git_again: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    @property
    def watched(self) -> set[str]:
        return set(self._watches)

    def start(self, found: Iterable[Project] = ()) -> None:
        """Must be called on the event loop."""
        self._loop = asyncio.get_running_loop()
        for project in found:
            if not self.watch(project) and not project.root.is_dir():
                self._missing.add(project.id)
        if self._listed is not None and self._check_s > 0:
            self._spawn(self._check_loop())

    def watch(self, project: Project) -> bool:
        if self._loop is None or project.id in self._watches:
            return False
        if not project.root.is_dir():
            log.info("project %s: folder %s is missing, not watched", project.id, project.path)
            return False
        if self._observer is None:
            self._observer = _observer()
            self._observer.daemon = True
            self._observer.start()
        try:
            handle = self._observer.schedule(_Handler(self, project.id, project.path), project.path, recursive=True)
        except OSError as exc:
            log.warning("project %s: cannot watch %s (%s)", project.id, project.path, exc)
            return False
        self._watches[project.id] = (project, handle)
        with contextlib.suppress(OSError):
            stat = project.root.stat()
            self._ids[project.id] = (stat.st_dev, stat.st_ino)
        log.info("watching project %s at %s", project.id, project.path)
        self._spawn(self.refresh_git(project.id))  # the state to compare the first change with
        return True

    def unwatch(self, project_id: str) -> None:
        entry = self._watches.pop(project_id, None)
        self._pending.pop(project_id, None)
        self._git_last.pop(project_id, None)
        if entry is not None and self._observer is not None:
            with contextlib.suppress(KeyError, OSError):
                self._observer.unschedule(entry[1])

    async def stop(self) -> None:
        for project_id in list(self._watches):
            self.unwatch(project_id)
        if self._observer is not None:
            self._observer.stop()
            await asyncio.to_thread(self._observer.join, 5)
            self._observer = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._loop = None

    # --- the folders themselves: gone, moved, back ---

    async def _check_loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_s)
            try:
                await self.check(await self._listed())
            except Exception:
                log.exception("checking the projects' folders failed")

    async def check(self, found: Iterable[Project]) -> None:
        """Each listed project's folder against what is watched (see the module's note)."""
        for project in found:
            here = await asyncio.to_thread(project.root.is_dir)
            if project.id in self._watches and not here:
                self.unwatch(project.id)
                moved = await asyncio.to_thread(find_moved, project.root, self._ids.get(project.id))
                if moved is not None and self._relocate is not None:
                    renamed = await self._relocate(project.id, moved)
                    self.watch(renamed)
                    log.info("project %s moved: %s -> %s", project.id, project.path, renamed.path)
                    await self._bus.publish("project.moved", {"project_id": project.id, "name": project.name,
                                                              "from": project.path, "to": renamed.path,
                                                              "project": renamed.to_json()})  # fmt: skip
                else:
                    self._missing.add(project.id)
                    log.info("project %s: folder %s is gone", project.id, project.path)
                    await self._bus.publish("project.missing", {"project_id": project.id, "name": project.name,
                                                                "path": project.path})  # fmt: skip
            elif project.id not in self._watches and here and self.watch(project):
                if project.id in self._missing:
                    self._missing.discard(project.id)
                    await self._bus.publish("project.found", {"project_id": project.id, "name": project.name,
                                                              "path": project.path})  # fmt: skip

    # --- from the observer's thread to the loop ---

    def from_thread(self, project_id: str, batch: Batch) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # the loop is closing
            loop.call_soon_threadsafe(self._note, project_id, batch)

    def _note(self, project_id: str, batch: Batch) -> None:
        if project_id not in self._watches:
            return
        pending = self._pending.get(project_id)
        if pending is None:
            self._pending[project_id] = batch
            self._spawn(self._flush_later(project_id))
        else:
            pending.merge(batch)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush_later(self, project_id: str) -> None:
        await asyncio.sleep(self._flush_s)
        batch = self._pending.pop(project_id, None)
        entry = self._watches.get(project_id)
        if batch is None or entry is None:
            return
        root = entry[0].root
        listed = (batch.changed | batch.folders) - {""}
        if listed:
            try:
                ignored = await asyncio.to_thread(projects.git_ignored, root, listed)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.info("project %s: git check-ignore failed (%s)", project_id, exc)
                ignored = set()
            batch.changed -= ignored
            batch.folders -= ignored
        if batch.changed or batch.folders:
            more = len(batch.changed) > MAX_LISTED or len(batch.folders) > MAX_LISTED
            payload = {
                "project_id": project_id,
                "changed": sorted(batch.changed)[:MAX_LISTED],
                "folders": sorted(batch.folders)[:MAX_LISTED],
                "more": more,
            }
            await self._bus.publish("project.files", payload, project_id=project_id, persist=False)
        if batch:
            await self.refresh_git(project_id)

    # --- git ---

    async def refresh_git(self, project_id: str) -> None:
        """Reads git again and publishes `project.git` if it changed. One read at a time per project: a change
        during a read asks for one more read after it."""
        if project_id in self._git_running:
            self._git_again.add(project_id)
            return
        self._git_running.add(project_id)
        try:
            while True:
                self._git_again.discard(project_id)
                entry = self._watches.get(project_id)
                if entry is None:
                    return
                state = await asyncio.to_thread(git_state, entry[0].root)
                before = self._git_last.get(project_id)
                if state is not None and project_id in self._watches and state != before:
                    self._git_last[project_id] = state
                    await self._bus.publish(
                        "project.git", {"project_id": project_id, **state}, project_id=project_id, persist=False
                    )
                    moved = head_change(before, state)
                    if moved is not None:  # a commit or a switch is worth keeping in the event log
                        await self._bus.publish("git.head", {"project_id": project_id, **moved}, project_id=project_id)
                if project_id not in self._git_again:
                    return
        finally:
            self._git_running.discard(project_id)


def find_moved(root: Path, file_id: tuple[int, int] | None) -> Path | None:
    """The folder that was `root`, renamed or moved among its old neighbours (its parent, and the parent's parent):
    the one with the same (device, file id). None: not found - deleted, or moved further."""
    if not file_id or not file_id[1]:
        return None
    for place in (root.parent, root.parent.parent):
        try:
            entries = list(place.iterdir()) if place.is_dir() else []
        except OSError:
            continue
        for entry in entries:
            with contextlib.suppress(OSError):
                if entry.is_dir():
                    stat = entry.stat()
                    if (stat.st_dev, stat.st_ino) == file_id:
                        return entry
    return None


def head_change(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any] | None:
    """What moved HEAD between two git states, for the event log: {kind: "branch" | "commit" | "moved", branch,
    from?, sha, subject, author}; None when HEAD stayed (or there is nothing to compare with)."""
    if not before or not before.get("repo") or not after.get("repo"):
        return None
    old_log, new_log = before.get("log") or [], after.get("log") or []
    old_sha = old_log[0]["sha"] if old_log else ""
    head = new_log[0] if new_log else {}
    common = {"branch": after.get("branch", ""), "sha": head.get("sha", ""), "subject": head.get("subject", "")}
    if before.get("branch") != after.get("branch"):
        return {"kind": "branch", "from": before.get("branch", ""), **common}
    if head.get("sha", "") == old_sha:
        return None
    if head and head["sha"] not in {c["sha"] for c in old_log}:
        return {"kind": "commit", "author": head.get("author", ""), **common}
    return {"kind": "moved", **common}  # back to a known commit: a reset or a checkout


def git_state(root: Path) -> dict[str, Any] | None:
    """Status and recent commits, as GET /v1/projects/{id}/git gives them; None if git could not be read."""
    try:
        return {**projects.git_status(root), "log": projects.git_log(root)}
    except (ProjectError, OSError, subprocess.TimeoutExpired) as exc:
        log.info("git state of %s not read (%s)", root, exc)
        return None
