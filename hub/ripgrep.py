"""ripgrep fetched by the hub itself (2026-10-28): searching projects (D26) is fast on a computer that has none.

search.ripgrep() looks in a checkout's tools/ripgrep, in LocalChat's app folder (<app>/tools/ripgrep) and on PATH.
When none has it, the hub takes the latest Windows build from ripgrep's own GitHub releases (BurntSushi/ripgrep),
checks its sha256 (GitHub's `digest`, else the `.sha256` file the release carries; without one nothing is put in)
and puts rg.exe with its licences into the app folder. Until then, and when GitHub cannot be reached (it tries
again every RETRY_S), search walks the files itself - slower, the same results.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from common.paths import app_home

from . import engines, hf, search
from .engines import EngineError

log = logging.getLogger("hub.ripgrep")
REPO = "BurntSushi/ripgrep"
ASSET = re.compile(r"^ripgrep-(\d+\.\d+\.\d+)-x86_64-pc-windows-msvc\.zip$")
KEEP = ("rg.exe", "COPYING", "LICENSE-MIT", "UNLICENSE")  # the program and the licences it comes under
RETRY_S = 6 * 3600.0


def home() -> Path:
    """Where the hub puts what it fetches: <app>/tools (search.ripgrep() looks in <app>/tools/ripgrep)."""
    return app_home() / "tools"


def pick(release: dict[str, Any]) -> dict[str, Any] | None:
    """The release's Windows build: {version, asset, sums} (`sums`: the URL of its .sha256 file, if any)."""
    assets = release.get("assets") or []
    asset = next((a for a in assets if ASSET.match(a["name"])), None)
    if asset is None:
        return None
    sums = next((a["browser_download_url"] for a in assets if a["name"] == asset["name"] + ".sha256"), None)
    return {"version": ASSET.match(asset["name"]).group(1), "asset": engines._asset(asset), "sums": sums}


async def latest(http: httpx.AsyncClient) -> dict[str, Any]:
    """The latest release's Windows build, its sha256 known."""
    found = pick(await engines._releases(http, REPO, "releases/latest"))
    if found is None:
        raise EngineError("в последнем выпуске ripgrep нет сборки для Windows")
    if found["asset"]["sha256"] is None and found["sums"]:
        try:
            response = await http.get(found["sums"], headers=hf.headers())
            said = response.text.split()[0] if response.status_code == 200 and response.text.strip() else ""
        except httpx.HTTPError:
            said = ""
        found["asset"]["sha256"] = said.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", said) else None
    if found["asset"]["sha256"] is None:
        raise EngineError("у сборки ripgrep нет sha256 — непроверенное не ставлю")
    return found


def install(found: dict[str, Any], into: Path, cancel: threading.Event) -> Path:
    """The build downloaded (sha256 checked) and rg.exe with its licences put into `into`/ripgrep (blocking)."""
    asset = found["asset"]
    downloads = into / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / asset["name"]
    hf.download(asset["url"], archive, size=asset["size"], sha256=asset["sha256"], token=None, report=lambda _n: None,
                cancel=cancel)  # fmt: skip
    folder = into / "ripgrep"
    with tempfile.TemporaryDirectory(dir=downloads) as unpacked:
        engines.unpack(archive, Path(unpacked))
        program = next(iter(sorted(Path(unpacked).rglob("rg.exe"), key=lambda p: len(p.parts))), None)
        if program is None:
            raise EngineError(f"в {asset['name']} нет rg.exe")
        folder.mkdir(parents=True, exist_ok=True)
        for name in KEEP:
            if (program.parent / name).is_file():
                shutil.copy2(program.parent / name, folder / name)
    archive.unlink(missing_ok=True)
    return folder / "rg.exe"


class Fetcher:
    """At the hub's start: ripgrep fetched when search finds none (see the module's note)."""

    def __init__(self, bus, *, enabled: bool, into: Path | None = None):
        self._bus, self._enabled, self._into = bus, enabled, into
        self._cancel = threading.Event()
        self._task: asyncio.Task | None = None
        self.error = ""  # the last try's failure, for «Проверить систему»

    def start(self) -> None:
        if self._enabled and search.ripgrep() is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._cancel.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while search.ripgrep() is None and not await self.fetch():
            await asyncio.sleep(RETRY_S)

    async def fetch(self) -> bool:
        into = self._into or home()
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                found = await latest(http)
            program = await asyncio.to_thread(install, found, into, self._cancel)
        except (EngineError, hf.HubError, hf.Cancelled, httpx.HTTPError, OSError) as exc:
            self.error = str(exc) or exc.__class__.__name__
            log.warning("ripgrep was not fetched: %s", self.error)
            return False
        self.error = ""
        log.info("ripgrep %s fetched into %s", found["version"], program)
        await self._bus.publish("tools.fetched", {"tool": "ripgrep", "version": found["version"], "path": str(program)})
        return True
