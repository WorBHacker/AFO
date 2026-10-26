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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
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

    def __init__(self, bus, *, ignore: Iterable[Path] = (), flush_s: float = FLUSH_S):
        self._bus = bus
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
            self.watch(project)

    def watch(self, project: Project) -> bool:
        if self._loop is None or project.id in self._watches:
            return False
        if not project.root.is_dir():
            log.info("project %s: folder %s is missing, not watched", project.id, project.path)
            return False
        if self._observer is None:
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
        try:
            handle = self._observer.schedule(_Handler(self, project.id, project.path), project.path, recursive=True)
        except OSError as exc:
            log.warning("project %s: cannot watch %s (%s)", project.id, project.path, exc)
            return False
        self._watches[project.id] = (project, handle)
        log.info("watching project %s at %s", project.id, project.path)
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
                if state is not None and project_id in self._watches and state != self._git_last.get(project_id):
                    self._git_last[project_id] = state
                    await self._bus.publish(
                        "project.git", {"project_id": project_id, **state}, project_id=project_id, persist=False
                    )
                if project_id not in self._git_again:
                    return
        finally:
            self._git_running.discard(project_id)


def git_state(root: Path) -> dict[str, Any] | None:
    """Status and recent commits, as GET /v1/projects/{id}/git gives them; None if git could not be read."""
    try:
        return {**projects.git_status(root), "log": projects.git_log(root)}
    except (ProjectError, OSError, subprocess.TimeoutExpired) as exc:
        log.info("git state of %s not read (%s)", root, exc)
        return None
