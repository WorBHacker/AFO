"""ffmpeg for a video's MP4 (stage 29.8в, D55): the engine gives an animated WebP - the window plays it, and so do
browsers and Telegram - and «Сохранить как…» also offers MP4 (H.264), which any player and messenger opens. ffmpeg is
fetched the first time an MP4 is asked for, as ripgrep is (hub/ripgrep.py): BtbN's Windows build, pinned by its
name to the 9.0 branch (its Animated WebP demuxer reads the engine's video; checked on 2026-11-07), the sha256
GitHub gives checked, only the program, its libraries and its licence kept (<app>/tools/ffmpeg). It is a separate
program AFO runs, never linked: its GPL build (with x264) is the user's download, as llama.cpp is.

For the director's film (50.3) the same ffmpeg takes a clip's last frame (the next scene goes on from it), the
frames asked for by number, and joins scenes into one MP4 - cut to cut or with a cross-fade.

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

from common.i18n import N_, tr
from common.paths import app_home

from . import engines, hf
from .engines import EngineError

REPO = "BtbN/FFmpeg-Builds"
TAG = "latest"  # BtbN's builds of each branch, renewed daily; the asset's name pins the branch
ASSET = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip"
VERSION = "9.0"
CONVERT_TIMEOUT_S = 600.0
JOIN_TIMEOUT_S = 1800.0  # a film of a minute at 1216x704, every frame a key frame
# H.264 in yuv420p - any player and messenger - the index at the front, no sound; each frame a key frame (48.12.8)
H264 = ("-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", "-movflags", "+faststart", "-an", "-f", "mp4")
PNG = ("-c:v", "png", "-f", "image2")  # one picture, whatever the file is called while it is written


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


def _run(program: Path, args: list[str], failed: str, *, made: Path | None = None,
         timeout: float = CONVERT_TIMEOUT_S) -> None:  # fmt: skip
    """ffmpeg with these arguments (blocking). `failed` - the words when it does not do it, with {said}: what ffmpeg
    said; `made` - the file it must leave."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [str(program), "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout, creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(tr("ffmpeg не запустился: {error}", error=exc)) from exc
    if done.returncode != 0 or (made is not None and not made.is_file()):
        said = " ".join((done.stderr or b"").decode("utf-8", "replace").split())[-300:]
        raise EngineError(tr(failed, said=said or done.returncode))


def _written(program: Path, args: list[str], target: Path, failed: str, *,
             timeout: float = CONVERT_TIMEOUT_S) -> None:  # fmt: skip
    """One file made by ffmpeg: written beside under another name, then put in place - a player that has the old
    one open, or a reader that comes meanwhile, never sees a half of it; nothing is left when it fails."""
    part = target.with_name(target.name + ".part")
    try:
        _run(program, [*args, str(part)], failed, made=part, timeout=timeout)
        part.replace(target)
    except (EngineError, OSError) as exc:
        part.unlink(missing_ok=True)
        if isinstance(exc, EngineError):
            raise
        raise EngineError(tr(failed, said=exc)) from exc


def to_mp4(program: Path, source: Path, target: Path) -> None:
    """A video into MP4 (blocking): H.264 in yuv420p - any player and messenger - the index at the front, no sound;
    written beside under another name, then put in place. Each frame a key frame (48.12.8): the window's player goes
    to a frame by decoding it alone, not the clip's frames from the first (x264's one key frame in 250) - three
    times the size, a 3 s clip at 960×960 2.3 MB."""
    _written(program, ["-i", str(source), *H264], target, N_("ffmpeg не перевёл видео в MP4: {said}"))


def last_frame(program: Path, source: Path, target: Path) -> None:
    """A clip's last frame as a PNG (50.3, blocking): the director's next scene goes on from it. Every frame is
    written over the one before - the last stays; the engine's WebP says nothing of its length, and to seek its end
    ffmpeg would have to know it."""
    _written(program, ["-i", str(source), "-fps_mode", "passthrough", "-update", "1", *PNG], target,
             N_("ffmpeg не взял последний кадр: {said}"))  # fmt: skip


def frames_at(program: Path, source: Path, folder: Path, numbers: list[int], *, small: int = 0) -> list[Path]:
    """The clip's frames with these numbers (from 0) as PNGs in `folder` - 1.png, 2.png... in the clip's order (50.3,
    blocking): a look at a scene - its beginning, middle and end. `small` (51.2) - for a model's look: the longer
    side `small` points at most, JPEGs (1.jpg...) - a tenth of a PNG's bytes on the way to the model."""
    wanted = sorted(set(numbers))
    picked = "+".join(f"eq(n\\,{n})" for n in wanted)
    words = N_("ffmpeg не взял кадры видео: {said}")
    chain, kind, coded = f"select='{picked}'", "png", []
    if small:
        wide = f"if(gt(iw\\,ih)\\,min(iw\\,{small})\\,-2)"
        high = f"if(gt(iw\\,ih)\\,-2\\,min(ih\\,{small}))"
        chain += f",scale='{wide}':'{high}'"
        kind, coded = "jpg", ["-pix_fmt", "yuvj420p", "-q:v", "4"]
    _run(program, ["-i", str(source), "-vf", chain, "-fps_mode", "passthrough", *coded,
                   str(folder / f"%d.{kind}")], words)  # fmt: skip
    found = [folder / f"{k}.{kind}" for k in range(1, len(wanted) + 1)]
    if not all(f.is_file() for f in found):
        raise EngineError(tr(words, said=tr("в видео меньше кадров, чем спрошено")))
    return found


def join(program: Path, clips: list[tuple[Path, float]], target: Path, *, fps: int, fade_s: float = 0.0) -> None:
    """Scenes into one MP4 (50.3, blocking): `clips` - each with its length in seconds (frames / fps: the engine's
    WebP does not say it). Cut to cut - every frame of each, once; with `fade_s` - a cross-fade of that length at
    each joint, the film shorter by the fades. Each scene's frames are stamped anew, one every 1/fps: a WebP keeps
    whole milliseconds, 42 for a frame of 24 a second, and a frame would be doubled every three seconds."""
    if not clips:
        raise EngineError(tr("нечего склеивать: в фильме нет снятых сцен"))
    if fade_s > 0 and any(seconds <= fade_s * (1 if n in (0, len(clips) - 1) else 2) for n, (_c, seconds)
                          in enumerate(clips)) and len(clips) > 1:  # fmt: skip
        raise EngineError(tr("сцена короче перехода: уберите переход или сделайте сцену длиннее"))
    args, graph = [], []
    for n, (clip, _seconds) in enumerate(clips):
        args += ["-i", str(clip)]
        graph.append(f"[{n}:v]setpts=N/({fps}*TB),fps={fps},format=yuv420p,setsar=1,settb=AVTB[v{n}]")
    if len(clips) == 1:
        out = "v0"
    elif fade_s > 0:
        out, at = "v0", 0.0
        for n in range(1, len(clips)):
            at += clips[n - 1][1] - fade_s  # where this joint's fade begins, in the film so far
            graph.append(f"[{out}][v{n}]xfade=transition=fade:duration={fade_s:.4f}:offset={at:.4f}[x{n}]")
            out = f"x{n}"
    else:
        graph.append("".join(f"[v{n}]" for n in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]")
        out = "v"
    _written(program, [*args, "-filter_complex", ";".join(graph), "-map", f"[{out}]", *H264], target,
             N_("ffmpeg не склеил сцены: {said}"), timeout=JOIN_TIMEOUT_S)  # fmt: skip
