"""Models already on this computer (step 15.1, D49): GGUF files on every local drive - downloaded by LM Studio, by
hand, by an older AFO - and Ollama's models through its manifests (its files have no extension). The search runs in
the background, may be stopped, skips the system's own folders and does not follow links to other folders (a junction
would show the same files twice, or loop). A model's parts (`-00001-of-00003.gguf`) are one model; a vision projector
(`mmproj…`) goes with the model beside it. Each file's header says what the model is; the model is never loaded.

What was found is kept (the settings table) until the next search; the window adds a model from the list - to
llama-swap's config where it lies, without a copy (hub/model_install.py, `add_local`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from common.disks import drives
from common.gguf import GGUFError, model_info

from .db import utc_now
from .hf import quant_of

log = logging.getLogger("hub.model_scan")

MIN_BYTES = 50 * 1024**2  # smaller GGUF files are vocabularies and test files, not models
SPLIT = re.compile(r"^(?P<stem>.+)-(?P<part>\d{5})-of-(?P<parts>\d{5})\.gguf$", re.IGNORECASE)
# folders a model never lies in: the system's own, and the many small ones of programs' packages (it goes faster)
SKIP = {"windows", "program files", "program files (x86)", "programdata", "system volume information", "recovery",
        "msocache", "node_modules", ".git", "__pycache__", "winsxs", "perflogs", ".venv", "venv", "site-packages",
        ".gradle", ".m2", ".nuget", ".cargo", ".rustup", ".npm", "temp"}  # fmt: skip
SAVED_KEY = "models.found"
PROGRESS_S = 1.0
OLLAMA_ENV = "OLLAMA_MODELS"


def default_roots() -> list[Path]:
    """Every local drive (fixed and removable) with a file system."""
    return [Path(d["root"]) for d in drives()]


def ollama_home() -> Path | None:
    """Ollama's models folder: OLLAMA_MODELS, else ~/.ollama/models."""
    home = Path(os.environ[OLLAMA_ENV]) if os.environ.get(OLLAMA_ENV) else Path.home() / ".ollama" / "models"
    return home if (home / "manifests").is_dir() else None


def _walk(root: Path, min_bytes: int, cancel: threading.Event, tick: Callable[[], None]) -> Iterator[Path]:
    """The .gguf files under `root` of at least `min_bytes`; no links followed, the system's folders skipped."""
    stack = [root]
    while stack and not cancel.is_set():
        folder = stack.pop()
        tick()
        try:
            entries = list(os.scandir(folder))
        except OSError:  # no right to look in, or gone
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in SKIP and not entry.name.startswith("$"):
                        stack.append(Path(entry.path))
                elif entry.name.lower().endswith(".gguf") and entry.stat(follow_symlinks=False).st_size >= min_bytes:
                    yield Path(entry.path)
            except OSError:
                continue


def _model(path: Path, files: list[Path], name: str, source: str, projector: str = "") -> dict[str, Any] | None:
    """What the window shows of a model: from its first file's header. None when it is not GGUF inside, or not a
    model to chat with: a speculative draft (`mtp-…`) needs its model, an embedding model makes no answers, a picture
    model's files are stable-diffusion.cpp's (29.3: its diffusion GGUF has no layers in the header, umt5 is a text
    encoder)."""
    if name.lower().startswith(("mtp-", "draft-")):
        return None
    try:
        info = model_info(path)
    except GGUFError, OSError:
        return None
    if "embed" in name.lower() or (info.get("architecture") or "").endswith("bert"):
        return None
    if not info.get("layers") or info.get("architecture") == "t5encoder":
        return None
    size = 0
    for file in files:
        with contextlib.suppress(OSError):
            size += file.stat().st_size
    return {"name": name, "path": str(path), "files": [str(f) for f in files], "size": size,
            "quant": quant_of(name + ".gguf") or "", "title": info.get("name") or "", "architecture":
            info.get("architecture") or "", "layers": info.get("layers") or 0,
            "context_trained": info.get("context_trained") or 0, "info": info, "projector": projector,
            "source": source}  # fmt: skip


def _lens_for(model: Path, lenses: list[Path]) -> str:
    """The projector beside a model: the one whose name shares the most with the model's."""

    def common(lens: Path) -> int:
        a, b = model.stem.lower(), lens.stem.lower().removeprefix("mmproj-").removeprefix("mmproj")
        return next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))

    return str(max(lenses, key=common)) if lenses else ""


def group(files: list[Path]) -> list[dict[str, Any]]:
    """Files found into models: a model's parts as one, projectors with their models."""
    by_folder: dict[Path, list[Path]] = {}
    for file in files:
        by_folder.setdefault(file.parent, []).append(file)
    out = []
    for folder_files in by_folder.values():
        lenses = [f for f in folder_files if "mmproj" in f.name.lower()]
        parts: dict[str, list[Path]] = {}
        for file in sorted(set(folder_files) - set(lenses)):
            split = SPLIT.match(file.name)
            parts.setdefault(split["stem"] if split else file.stem, []).append(file)
        for stem, each in parts.items():
            found = _model(each[0], each, stem, "file", _lens_for(each[0], lenses))
            if found is not None:
                out.append(found)
    return out


def scan(roots: list[Path], *, min_bytes: int | None = None, cancel: threading.Event | None = None,
         report: Callable[[int, int, str], None] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """The models on these drives (blocking: in a thread); `report(folders, files, drive)` now and then."""
    cancel = cancel or threading.Event()
    limit = MIN_BYTES if min_bytes is None else min_bytes
    files: list[Path] = []
    folders = [0, 0.0]
    drive = [""]

    def tick() -> None:
        folders[0] += 1
        if report is not None and time.monotonic() - folders[1] >= PROGRESS_S:
            folders[1] = time.monotonic()
            report(folders[0], len(files), drive[0])

    for root in roots:
        drive[0] = str(root)
        files.extend(_walk(root, limit, cancel, tick))
        if cancel.is_set():
            return []
    if report is not None:
        report(folders[0], len(files), drive[0])
    return sorted(group(files), key=lambda m: m["name"].lower())


def ollama(home: Path) -> list[dict[str, Any]]:
    """Ollama's models: each manifest (`manifests/<host>/<namespace>/<model>/<tag>`) names its model's blob and its
    projector's; the name is Ollama's own (`gemma3:4b`, `hf.co/unsloth/Qwen3-GGUF:Q4_K_M`)."""
    out = []
    for manifest in sorted(p for p in (home / "manifests").rglob("*") if p.is_file()):
        try:
            layers = json.loads(manifest.read_text(encoding="utf-8")).get("layers") or []
        except OSError, ValueError:
            continue
        blob = {layer.get("mediaType", "").rsplit(".", 1)[-1]: home / "blobs" / str(layer.get("digest", ""))
                .replace(":", "-") for layer in layers}  # fmt: skip
        model = blob.get("model")
        if model is None or not model.is_file():
            continue
        host, *rest = manifest.relative_to(home / "manifests").parts
        *where, tag = rest
        name = (f"{where[-1]}:{tag}" if host == "registry.ollama.ai" and where[0] == "library"
                else f"{host}/{'/'.join(where)}:{tag}")  # fmt: skip
        lens = blob.get("projector")
        found = _model(model, [model], name, "ollama", str(lens) if lens is not None and lens.is_file() else "")
        if found is not None:
            found["quant"] = quant_of(tag + ".gguf") or found["quant"]
            out.append(found)
    return out


def saved(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SAVED_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(conn: sqlite3.Connection, found: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (SAVED_KEY, json.dumps(found, ensure_ascii=False), utc_now()),
    )


class Scanner:
    """The search on the drives: one at a time, in the background, stopped on the window's word."""

    def __init__(self, db, bus):
        self._db = db
        self._bus = bus
        self.models: list[dict[str, Any]] = []
        self.at = ""
        self.job: dict[str, Any] = {"status": "idle"}  # idle | scanning | done | cancelled | failed
        self._task: asyncio.Task | None = None
        self._cancel = threading.Event()

    def load(self, found: dict[str, Any]) -> None:
        self.models, self.at = found.get("models") or [], found.get("at") or ""

    async def start(self) -> None:
        self.load(await self._db.acall(saved))

    async def stop(self) -> None:
        self._cancel.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def scan(self) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            return dict(self.job)
        self._cancel = threading.Event()
        self.job = {"status": "scanning", "folders": 0, "files": 0, "drive": "", "error": ""}
        self._task = asyncio.create_task(self._run())
        return dict(self.job)

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return dict(self.job)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()

        def report(folders: int, files: int, drive: str) -> None:  # in the search's thread
            self.job.update(folders=folders, files=files, drive=drive)
            asyncio.run_coroutine_threadsafe(self._publish(False), loop)

        def everything() -> list[dict[str, Any]]:
            found = scan(default_roots(), cancel=self._cancel, report=report)
            home = ollama_home()
            return found + ollama(home) if home is not None and not self._cancel.is_set() else found

        try:
            found = await asyncio.to_thread(everything)
            if self._cancel.is_set():
                self.job["status"] = "cancelled"
            else:
                self.models, self.at = found, utc_now()
                await self._db.acall(lambda conn: save(conn, {"at": self.at, "models": found}))
                self.job.update(status="done", files=len(found))
                log.info("models on the drives: %s found", len(found))
        except Exception as exc:  # a drive that fails mid-way must not leave the search «going» for ever
            log.exception("the search on the drives")
            self.job.update(status="failed", error=str(exc))
        await self._publish()

    async def _publish(self, persist: bool = True) -> None:
        await self._bus.publish("install.found", {"job": dict(self.job)}, persist=persist)
