"""ffmpeg for a video's MP4 (stage 29.8в, D55): the engine gives an animated WebP - the window plays it, and so do
browsers and Telegram - and «Сохранить как…» also offers MP4 (H.264), which any player and messenger opens. ffmpeg is
fetched the first time an MP4 is asked for, as ripgrep is (hub/ripgrep.py): BtbN's Windows build, pinned by its
name to the 9.0 branch (its Animated WebP demuxer reads the engine's video; checked on 2026-11-07), the sha256
GitHub gives checked, only the program, its libraries and its licence kept (<app>/tools/ffmpeg). It is a separate
program AFO runs, never linked: its GPL build (with x264) is the user's download, as llama.cpp is.

The video is not made smoother by RIFE (planned in docs/visual.md §4.6): Wan2.2-TI2V-5B gives 24 frames a second
itself (29.8а live) - RIFE ×2 was for models of 16.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from common.i18n import tr
from common.paths import app_home

from . import engines, hf
from .engines import EngineError

REPO = "BtbN/FFmpeg-Builds"
TAG = "latest"  # BtbN's builds of each branch, renewed daily; the asset's name pins the branch
ASSET = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip"
VERSION = "9.0"
CONVERT_TIMEOUT_S = 600.0


def home() -> Path:
    """Where the hub puts what it fetches: <app>/tools."""
    return app_home() / "tools"


def pick(release: dict[str, Any]) -> dict[str, Any] | None:
    """The pinned Windows build of the release: {version, asset}; None when it is not there."""
    asset = next((a for a in release.get("assets") or [] if a["name"] == ASSET), None)
    return {"version": VERSION, "asset": engines._asset(asset)} if asset else None


async def latest(http: httpx.AsyncClient) -> dict[str, Any]:
    """The pinned build as GitHub has it now, its sha256 known."""
    found = pick(await engines.github(http, REPO, f"releases/tags/{TAG}"))
    if found is None:
        raise EngineError(tr("в выпуске {repo} нет сборки {asset}", repo=REPO, asset=ASSET))
    if not found["asset"]["sha256"]:
        raise EngineError(tr("у сборки ffmpeg нет sha256 — непроверенное не ставлю"))
    return found


def find(into: Path) -> Path | None:
    program = into / "ffmpeg" / "ffmpeg.exe"
    return program if program.is_file() else None


def install(found: dict[str, Any], into: Path, cancel: threading.Event) -> Path:
    """The build downloaded (sha256 checked) and ffmpeg.exe with its libraries and licence put into `into`/ffmpeg
    (blocking)."""
    asset = found["asset"]
    downloads = into / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / asset["name"]
    hf.download(asset["url"], archive, size=asset["size"], sha256=asset["sha256"], token=None, report=lambda _n: None,
                cancel=cancel)  # fmt: skip
    folder = into / "ffmpeg"
    with tempfile.TemporaryDirectory(dir=downloads) as unpacked:
        engines.unpack(archive, Path(unpacked))
        program = next(iter(sorted(Path(unpacked).rglob("ffmpeg.exe"), key=lambda p: len(p.parts))), None)
        if program is None:
            raise EngineError(tr("в {archive} нет ffmpeg.exe", archive=asset["name"]))
        folder.mkdir(parents=True, exist_ok=True)
        for library in program.parent.glob("*.dll"):
            shutil.copy2(library, folder / library.name)
        licence = next(iter(sorted(Path(unpacked).rglob("LICENSE.txt"), key=lambda p: len(p.parts))), None)
        if licence is not None:
            shutil.copy2(licence, folder / "LICENSE.txt")
        shutil.copy2(program, folder / "ffmpeg.exe")  # last: the program there means the rest is too
    archive.unlink(missing_ok=True)
    return folder / "ffmpeg.exe"


def to_mp4(program: Path, source: Path, target: Path) -> None:
    """A video into MP4 (blocking): H.264 in yuv420p - any player and messenger - the index at the front, no sound;
    written beside under another name, then put in place."""
    part = target.with_name(target.name + ".part")
    args = [str(program), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", "-f", "mp4", str(part)]  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(args, capture_output=True, timeout=CONVERT_TIMEOUT_S, creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        part.unlink(missing_ok=True)
        raise EngineError(tr("ffmpeg не запустился: {error}", error=exc)) from exc
    if done.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        said = " ".join((done.stderr or b"").decode("utf-8", "replace").split())[-300:]
        raise EngineError(tr("ffmpeg не перевёл видео в MP4: {said}", said=said or done.returncode))
    part.replace(target)
