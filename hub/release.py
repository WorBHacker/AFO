"""AFO's own updates (step 10.4d): the hub learns of a newer AFO from the releases on GitHub (WorBHacker/AFO: a tag
vX.Y.Z with AFO-Setup-X.Y.Z.exe and its .sha256, ARCHITECTURE §7), downloads the installer - its sha256 checked, one
GitHub gives none for is refused - and, at the person's word, runs it silently over the installed program. The
installer stops the hub and the window, replaces the code and the libraries, keeps the data and the models, and
opens the window again (`/UPDATE=1`, installer/afo.iss); the hub that starts after it says how it went.

A checkout of the code is updated with git, not by the installer (it would put a second AFO beside it): there the hub
only says that a newer version is out."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from common import __version__, i18n
from common.i18n import tr

from . import engines, hf
from .db import utc_now

log = logging.getLogger("hub.release")

REPO = "WorBHacker/AFO"
TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
HERE = ("127.0.0.1", "localhost", "::1")  # the tests' stand-in for GitHub
AUTO_KEY = "release.auto"
PENDING = "pending.json"  # left for the next hub: which version the installer was to put in
WIZARD = {"ru": "russian", "en": "english"}  # the installer's languages (installer/afo.iss)
FIRST_CHECK_S = 60.0  # after the hub starts: the window and the models first
CHECK_EVERY_S = 12 * 3600.0
PROGRESS_S = 1.0
NOTES_CHARS = 4000
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: it outlives the hub
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: out of a job the hub may be in, when the job lets it


class ReleaseError(Exception):
    """A release cannot be read, fetched or put in; the message is for the window."""


def version(tag: str) -> tuple[int, int, int] | None:
    found = TAG.match(tag or "")
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def newer(latest: str, current: str) -> bool:
    ours, theirs = version(current), version(latest)
    return ours is not None and theirs is not None and theirs > ours


def trusted(url: str) -> bool:
    """An installer comes from GitHub over https - or, in the tests, from this computer."""
    parts = urlsplit(url or "")
    return (parts.scheme == "https" and parts.hostname in HOSTS) or (
        parts.scheme in ("http", "https") and parts.hostname in HERE
    )


async def _listed(http: httpx.AsyncClient, asset: dict[str, Any] | None) -> str | None:
    """The sha256 written in the release's AFO-Setup-X.Y.Z.exe.sha256, if it has one."""
    if asset is None or not trusted(asset.get("browser_download_url", "")):
        return None
    try:
        response = await http.get(asset["browser_download_url"])
    except httpx.HTTPError:
        return None
    first = response.text.split()[0].lower() if response.status_code == 200 and response.text.split() else ""
    return first if re.fullmatch(r"[0-9a-f]{64}", first) else None


async def latest(http: httpx.AsyncClient) -> dict[str, Any] | None:
    """AFO's newest release with its installer, or None when there is none of this kind."""
    try:
        data = await engines.github(http, REPO, "releases/latest", missing_ok=True)
    except engines.EngineError as exc:
        raise ReleaseError(str(exc)) from exc
    found = version(str((data or {}).get("tag_name") or ""))
    if found is None:
        return None
    number = ".".join(map(str, found))
    name = f"AFO-Setup-{number}.exe"
    assets = {a.get("name"): a for a in data.get("assets") or []}
    setup = assets.get(name)
    if setup is None:
        raise ReleaseError(tr("в выпуске {tag} нет установщика {name}", tag=data["tag_name"], name=name))
    if not trusted(setup.get("browser_download_url", "")):
        raise ReleaseError(tr("установщик лежит не на GitHub — адрес {url} не принят",
                              url=setup.get("browser_download_url", "")))  # fmt: skip
    digest = str(setup.get("digest") or "")
    digest = digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else None
    listed = await _listed(http, assets.get(name + ".sha256"))
    if digest and listed and digest != listed:
        raise ReleaseError(tr("контрольные суммы установщика {name} не совпадают — он не скачивается", name=name))
    if not (digest or listed):
        raise ReleaseError(tr("GitHub не дал контрольную сумму установщика {name}: без неё он не скачивается",
                              name=name))  # fmt: skip
    return {"version": number, "tag": data["tag_name"], "name": name, "download": setup["browser_download_url"],
            "size": int(setup.get("size") or 0), "sha256": digest or listed, "url": data.get("html_url") or "",
            "notes": str(data.get("body") or "")[:NOTES_CHARS],
            "published": data.get("published_at") or ""}  # fmt: skip


def program_folder() -> Path | None:
    """The installed program's folder - the embedded Python (its ._pth beside it) in `python`, the uninstaller beside
    that; a checkout's Python is not that: None."""
    python = Path(sys.executable).resolve().parent
    if any(python.glob("python*._pth")) and (python.parent / "unins000.exe").is_file():
        return python.parent
    return None


def install_command(setup: Path, log_file: Path) -> list[str]:
    """The installer, silent, over the installed program (the same folder: Inno Setup remembers it), in the window's
    language, opening the window again when it is done."""
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/UPDATE=1",
            f"/LANG={WIZARD.get(i18n.language(), 'english')}", f"/LOG={log_file}"]  # fmt: skip


def auto(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (AUTO_KEY,)).fetchone()
    return bool(json.loads(row[0])) if row else True


def set_auto(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (AUTO_KEY, json.dumps(bool(on)), utc_now()),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _said(log_file: str) -> str:
    """The installer's last error from its log (Inno Setup writes «Error» lines in English)."""
    try:
        lines = Path(log_file).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return ""
    errors = [line for line in lines if "rror" in line]
    return (errors or [line for line in lines if line.strip()] or [""])[-1].strip()[:300]


class Releases:
    """AFO's newest release, checked now and then (unless the person turned it off), its installer's download and
    its run."""

    def __init__(self, db, bus, folder: Path):
        self._db = db
        self._bus = bus
        self.folder = folder  # <data>/updates
        self.found: dict[str, Any] | None = None
        self.error = ""
        self.checked = ""
        self.job: dict[str, Any] = {"status": "idle"}  # idle | downloading | ready | installing | failed | updated
        self._task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._cancel = threading.Event()
        self._shown = 0.0

    async def start(self) -> None:
        await self.after_update()
        self._watch_task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        self._cancel.set()
        for task in (self._watch_task, self._task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _watch(self) -> None:
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            if await self._db.acall(auto):
                await self.check()
            await asyncio.sleep(CHECK_EVERY_S)

    async def _publish(self, persist: bool = True) -> None:
        await self._bus.publish("release.changed", {"job": dict(self.job)}, persist=persist)

    async def state(self) -> dict[str, Any]:
        found = self.found or {}
        return {"current": __version__, "latest": found.get("version", ""),
                "newer": bool(found) and newer(found["version"], __version__), "size": found.get("size", 0),
                "url": found.get("url", ""), "notes": found.get("notes", ""), "published": found.get("published", ""),
                "auto": await self._db.acall(auto), "installed": program_folder() is not None,
                "checked": self.checked, "error": self.error, "job": dict(self.job)}  # fmt: skip

    async def check(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                self.found = await latest(http)
            self.error = ""
        except ReleaseError as exc:
            self.error = str(exc)
            log.info("AFO's releases: %s", exc)
        self.checked = utc_now()
        if self.found and newer(self.found["version"], __version__):
            log.info("AFO %s is out (this is %s)", self.found["version"], __version__)
        await self._publish()
        return await self.state()

    def download(self) -> dict[str, Any]:
        found = self.found
        if not found or not newer(found["version"], __version__):
            raise ReleaseError(tr("нет версии новее этой — проверьте обновления ещё раз"))
        if self._task is not None and not self._task.done():
            return dict(self.job)
        self._cancel = threading.Event()
        self.job = {"status": "downloading", "version": found["version"], "done": 0, "total": found["size"],
                    "error": "", "file": ""}  # fmt: skip
        self._task = asyncio.create_task(self._download(found, self.folder / found["name"]))
        return dict(self.job)

    async def _download(self, found: dict[str, Any], dest: Path) -> None:
        loop = asyncio.get_running_loop()

        def report(done: int) -> None:  # in the download's thread
            self.job["done"] = done
            now = time.monotonic()
            if now - self._shown >= PROGRESS_S:
                self._shown = now
                asyncio.run_coroutine_threadsafe(self._publish(persist=False), loop)

        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for old in self.folder.glob("AFO-Setup-*.exe"):  # one installer kept: the newest
                if old.name != dest.name:
                    old.unlink(missing_ok=True)
            whole = dest.is_file() and await asyncio.to_thread(_sha256, dest) == found["sha256"]
            if not whole:
                await asyncio.to_thread(hf.download, found["download"], dest, size=found["size"],
                                        sha256=found["sha256"], token=None, report=report,
                                        cancel=self._cancel)  # fmt: skip
            self.job.update(status="ready", done=found["size"], file=str(dest))
        except hf.Cancelled:
            self.job.update(status="idle")
        except (hf.HubError, OSError) as exc:
            self.job.update(status="failed", error=str(exc))
        await self._publish()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return dict(self.job)

    def install(self) -> dict[str, Any]:
        if program_folder() is None:
            raise ReleaseError(tr("Это рабочая копия AFO: её обновляют через git (git pull), а установщик поставил бы "
                                  "рядом отдельную программу."))  # fmt: skip
        if self.job.get("status") != "ready" or not Path(self.job.get("file", "")).is_file():
            raise ReleaseError(tr("сначала скачайте новую версию"))
        setup = Path(self.job["file"])
        log_file = self.folder / "install.log"
        note = {"from": __version__, "to": self.job["version"], "log": str(log_file)}
        (self.folder / PENDING).write_bytes(json.dumps(note).encode("utf-8"))
        command = install_command(setup, log_file)
        options = {"cwd": str(self.folder), "close_fds": True, "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}  # fmt: skip
        try:
            subprocess.Popen(command, creationflags=DETACHED | BREAKAWAY, **options)
        except OSError:  # the hub's job does not let its children out: the installer still outlives the hub
            subprocess.Popen(command, creationflags=DETACHED, **options)
        log.info("installing AFO %s over %s", self.job["version"], __version__)
        self.job.update(status="installing")
        return dict(self.job)

    async def after_update(self) -> None:
        """The hub after the installer: the note left before it says which version was to come."""
        marker = self.folder / PENDING
        try:
            note = json.loads(marker.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return
        marker.unlink(missing_ok=True)
        if note.get("to") == __version__:
            for old in [*self.folder.glob("AFO-Setup-*"), self.folder / "install.log"]:
                old.unlink(missing_ok=True)
            self.job = {"status": "updated", "version": __version__, "from": note.get("from", "")}
            log.info("updated from %s to %s", note.get("from"), __version__)
            await self._bus.publish("release.updated", {"version": __version__, "from": note.get("from", "")})
            return
        why = _said(note.get("log", "")) or tr("установщик не сказал почему")
        error = tr("AFO {version} не установилась: {why}", version=note.get("to", ""), why=why)
        self.job = {"status": "failed", "version": note.get("to", ""), "error": error}
        log.warning("the update to %s did not happen: %s", note.get("to"), why)
