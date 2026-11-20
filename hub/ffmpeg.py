"""ffmpeg for a video's MP4 (stage 29.8в, D55): the engine gives an animated WebP - the window plays it, and so do
browsers and Telegram - and «Сохранить как…» also offers MP4 (H.264), which any player and messenger opens. ffmpeg is
fetched the first time an MP4 is asked for, as ripgrep is (hub/ripgrep.py): BtbN's Windows build, pinned by its
name to the 9.0 branch (its Animated WebP demuxer reads the engine's video; checked on 2026-11-07), the sha256
GitHub gives checked, only the program, its libraries and its licence kept (<app>/tools/ffmpeg). It is a separate
program AFO runs, never linked: its GPL build (with x264) is the user's download, as llama.cpp is.

For the director's film (50.3) the same ffmpeg takes a clip's last frame (the next scene goes on from it), the
frames asked for by number, and joins scenes into one MP4 - cut to cut or with a cross-fade.

For «Улучшить ролик» (54.3) it takes a video apart into frames for the programs (hub/enhance.py), finds where a new
shot begins - the frames between are not made across it - and puts the frames back together, the sound with them.
"""

from __future__ import annotations

import math
import re
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
# the sound a clip has (52.2: the engine's PCM) goes into AAC; a clip without - no sound track, as before
H264 = ("-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k",
        "-f", "mp4")  # fmt: skip
PNG = ("-c:v", "png", "-f", "image2")  # one picture, whatever the file is called while it is written
JPEG = ("-frames:v", "1", "-update", "1", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "image2")
SOUND = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"  # every scene's sound alike, to be joined
STILL_SIDE = 64  # the frames compared to tell a clip that does not move


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
    """A video into MP4 (blocking): H.264 in yuv420p - any player and messenger - the index at the front, its sound,
    when it has one (52.2), in AAC; written beside under another name, then put in place. Each frame a key frame
    (48.12.8): the window's player goes to a frame by decoding it alone, not the clip's frames from the first
    (x264's one key frame in 250) - three times the size, a 3 s clip at 960×960 2.3 MB."""
    _written(program, ["-i", str(source), *H264], target, N_("ffmpeg не перевёл видео в MP4: {said}"))


def heard(program: Path, source: Path) -> bool:
    """Whether a clip has sound (52.2, blocking): what ffmpeg says of its streams. One it cannot read has none."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run([str(program), "-hide_banner", "-i", str(source)], capture_output=True,
                              timeout=CONVERT_TIMEOUT_S, creationflags=flags, check=False)  # fmt: skip
    except OSError, subprocess.TimeoutExpired:
        return False
    return re.search(r"Stream #\d+:\d+.*: Audio:", (done.stderr or b"").decode("utf-8", "replace")) is not None


def poster(program: Path, source: Path, target: Path) -> None:
    """A clip's first frame as a JPEG (52.2, blocking): what the window shows of a clip it cannot read as a picture -
    the engine's WebM - in its lists and until its MP4 plays."""
    _written(program, ["-i", str(source), *JPEG], target, N_("ffmpeg не взял первый кадр: {said}"))


def still(program: Path, source: Path, frames: int) -> bool:
    """Whether a clip of `frames` frames shows one picture all along (52.2, blocking): its first, middle and last
    frame, small, are the same to a byte - the engine lays each frame by itself, the same picture the same way. A
    WebP folds such frames into one (`visual.moving`); a WebM keeps each."""
    with tempfile.TemporaryDirectory() as folder:
        shown = frames_at(program, source, Path(folder), [0, frames // 2, frames - 1], small=STILL_SIDE)
        return len({f.read_bytes() for f in shown}) == 1


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


CUT_SCORE = 10.0  # (54.1) scdet's score of a hard cut between two clips - 38; the rest of a film's frames 3.3 at most
FRAME_NAME = "%06d.png"  # (54.3) a video's frames in a folder: 000001.png, 000002.png...


def frames_out(program: Path, source: Path, folder: Path) -> int:
    """All of a video's frames as PNGs in `folder`, by FRAME_NAME (54.3, blocking): what «Улучшить ролик» gives its
    programs; how many."""
    folder.mkdir(parents=True, exist_ok=True)
    _run(program, ["-i", str(source), "-fps_mode", "passthrough", str(folder / FRAME_NAME)],
         N_("ffmpeg не разобрал видео на кадры: {said}"), timeout=JOIN_TIMEOUT_S)  # fmt: skip
    count = sum(1 for _ in folder.glob("*.png"))
    if not count:
        raise EngineError(tr("ffmpeg не разобрал видео на кадры: {said}", said=tr("кадров нет")))
    return count


def frame_count(program: Path, source: Path) -> int:
    """How many frames a video has (56, blocking): its picture copied to nowhere - nothing decoded - and the last
    count ffmpeg tells. An MP4's frame is one packet each: what `frames_out` would write."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [str(program), "-hide_banner", "-i", str(source), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"]
    try:
        done = subprocess.run(command, capture_output=True, timeout=JOIN_TIMEOUT_S, creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(tr("ffmpeg не запустился: {error}", error=exc)) from exc
    said = (done.stderr or b"").decode("utf-8", "replace")
    counted = re.findall(r"frame=\s*(\d+)", said)
    if done.returncode != 0 or not counted or not int(counted[-1]):
        raise EngineError(tr("ffmpeg не сосчитал кадры видео: {said}", said=" ".join(said.split())[-300:] or
                             done.returncode))  # fmt: skip
    return int(counted[-1])


def cuts(program: Path, source: Path) -> list[int]:
    """Where a new shot begins in a video - its frames' numbers, from 0 (54.3, blocking): ffmpeg's scene detector,
    a score past CUT_SCORE. A film's scenes that go on from the last frame of the one before have none (54.1)."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [str(program), "-hide_banner", "-i", str(source), "-an", "-vf",
               f"scdet=threshold={CUT_SCORE:g},metadata=print:file=-", "-f", "null", "-"]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, timeout=JOIN_TIMEOUT_S, creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(tr("ffmpeg не запустился: {error}", error=exc)) from exc
    if done.returncode != 0:
        said = " ".join((done.stderr or b"").decode("utf-8", "replace").split())[-300:]
        raise EngineError(tr("ffmpeg не нашёл стыки сцен: {said}", said=said or done.returncode))
    found, frame = [], -1
    for line in (done.stdout or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("frame:"):
            frame = int(line.split()[0].removeprefix("frame:"))
        elif line.startswith("lavfi.scd.time=") and frame > 0:
            found.append(frame)
    return found


def scaled_frames(program: Path, folder: Path, target: Path, *, width: int, height: int, start: int) -> None:
    """The PNG frames of `folder` (FRAME_NAME, from 1) brought to `width` × `height` into `target`, numbered from
    `start` (54.4, blocking): Real-ESRGAN's four times to twice, a piece at a time - its frames are not kept."""
    _run(program, ["-i", str(folder / FRAME_NAME), "-vf", f"scale={width}:{height}:flags=lanczos", "-start_number",
                   str(start), str(target / FRAME_NAME)], N_("ffmpeg не уменьшил кадры: {said}"),
         timeout=JOIN_TIMEOUT_S)  # fmt: skip


def from_frames(program: Path, folder: Path, target: Path, *, fps: int, sound: Path | None = None) -> None:
    """A video of the PNG frames in `folder` (FRAME_NAME) at `fps` (54.3, blocking): H.264 as `to_mp4` makes it,
    with the sound of `sound` - another video - when it has one, cut to the picture's length."""
    args = ["-framerate", str(fps), "-i", str(folder / FRAME_NAME)]
    if sound is not None:
        args += ["-i", str(sound), "-map", "0:v", "-map", "1:a?", "-shortest"]
    _written(program, [*args, *H264], target, N_("ffmpeg не собрал видео из кадров: {said}"),
             timeout=JOIN_TIMEOUT_S)  # fmt: skip


def join(program: Path, clips: list[tuple[Path, float]], target: Path, *, fps: int, fade_s: float = 0.0) -> None:
    """Scenes into one MP4 (50.3, blocking): `clips` - each with its length in seconds (frames / fps: the engine's
    WebP does not say it). Cut to cut - every frame of each, once; with `fade_s` - a cross-fade of that length at
    each joint, the film shorter by the fades. Each scene's frames are stamped anew, one every 1/fps: a WebP keeps
    whole milliseconds, 42 for a frame of 24 a second, and a frame would be doubled every three seconds. With sound
    in any of them (52.2) the film has sound: each scene's own, as long as the scene - a shorter one padded, a
    silent scene's stretch silence - and cross-faded where the picture is."""
    if not clips:
        raise EngineError(tr("нечего склеивать: в фильме нет снятых сцен"))
    if fade_s > 0 and any(seconds <= fade_s * (1 if n in (0, len(clips) - 1) else 2) for n, (_c, seconds)
                          in enumerate(clips)) and len(clips) > 1:  # fmt: skip
        raise EngineError(tr("сцена короче перехода: уберите переход или сделайте сцену длиннее"))
    sounds = [heard(program, clip) for clip, _seconds in clips]
    loud = any(sounds)
    args, graph = [], []
    for n, (clip, seconds) in enumerate(clips):
        args += ["-i", str(clip)]
        graph.append(f"[{n}:v]setpts=N/({fps}*TB),fps={fps},format=yuv420p,setsar=1,settb=AVTB[v{n}]")
        if loud and sounds[n]:
            graph.append(f"[{n}:a]{SOUND},apad,atrim=duration={seconds:.4f},asetpts=N/SR/TB[a{n}]")
        elif loud:
            graph.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={seconds:.4f},"
                         f"aformat=sample_fmts=fltp[a{n}]")  # fmt: skip
    if len(clips) == 1:
        out, sound = "v0", "a0"
    elif fade_s > 0:
        out, sound, at = "v0", "a0", 0.0
        for n in range(1, len(clips)):
            at += clips[n - 1][1] - fade_s  # where this joint's fade begins, in the film so far
            # a quarter of a frame before it (52.2): a time rounded up past its frame began the fade a frame late
            begins = max(0.0, at - 0.25 / fps)
            graph.append(f"[{out}][v{n}]xfade=transition=fade:duration={fade_s:.4f}:offset={begins:.6f}[x{n}]")
            out = f"x{n}"
            if loud:
                graph.append(f"[{sound}][a{n}]acrossfade=duration={fade_s:.4f}:curve1=tri:curve2=tri[y{n}]")
                sound = f"y{n}"
    else:
        streams = "".join(f"[v{n}]" + (f"[a{n}]" if loud else "") for n in range(len(clips)))
        graph.append(streams + f"concat=n={len(clips)}:v=1:a={int(loud)}[v]" + ("[a]" if loud else ""))
        out, sound = "v", "a"
    heard_too = ["-map", f"[{sound}]"] if loud else []
    _written(program, [*args, "-filter_complex", ";".join(graph), "-map", f"[{out}]", *heard_too, *H264], target,
             N_("ffmpeg не склеил сцены: {said}"), timeout=JOIN_TIMEOUT_S)  # fmt: skip


# (53.5) the film's added sound, each kind at a mean volume of its own, dB: the voice is heard over the rest
VOICE_DB, MUSIC_DB, EFFECT_DB = -18.0, -27.0, -23.0
GAIN_DB = 24.0  # the most a track is made louder or quieter to come to its level
# (53.7, the live film) a piece made by the engine of sound is cut below its kind's frequency before it is measured
# and mixed: what lies there is a rumble no ear and no ordinary loudspeaker gets - but it was most of the piece (a
# scene's «wind»: 90% of its energy below 60 Hz; music: 40% between 20 and 60), so the level was set by it and what
# is heard sank 20-30 dB under its place. The user's own music is left as it is.
VOICE_CUT = "highpass=f=90"
MUSIC_CUT = "highpass=f=80,highpass=f=80"  # twice: a steep cut - the bottom is loud
EFFECT_CUT = "highpass=f=100,highpass=f=100"
# What is not the voice goes down while the narrator speaks - once for a stretch of speech, by so many dB, slowly
# down and slowly up again; a pause shorter than BED_PAUSE_S between two lines is still that stretch. (53.8, the
# user's ear: «дождь как через шумоподавление») A compressor keyed by the voice did it before: 10-20 dB down at
# every word and up again in every pause between words - the film's rain jumped several times in five seconds.
BED_DOWN_DB = 6.0
BED_SLOPE_S = 0.4
BED_PAUSE_S = 1.5
MUSIC_FADES_S = (0.5, 1.5)  # music comes in and goes out
EFFECT_FADE_S = 0.15  # a scene's made sound comes in and goes out: no click at a cut


def _measured(program: Path, source: Path, cut: str = "") -> tuple[float | None, float | None]:
    """A file's sound as ffmpeg measures it (blocking): (its mean volume in dB - `cut`: after that filter -, its
    length in seconds); None - it has no sound, or cannot be read."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        heard = f"{cut},volumedetect" if cut else "volumedetect"
        done = subprocess.run([str(program), "-hide_banner", "-i", str(source), "-vn", "-af", heard, "-f", "null",
                               "-"], capture_output=True, timeout=CONVERT_TIMEOUT_S, creationflags=flags,
                              check=False)  # fmt: skip
    except OSError, subprocess.TimeoutExpired:
        return None, None
    said = (done.stderr or b"").decode("utf-8", "replace")
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", said)
    long = re.search(r"Duration: (\d+):(\d+):([\d.]+)", said)
    seconds = int(long.group(1)) * 3600 + int(long.group(2)) * 60 + float(long.group(3)) if long else None
    return (float(mean.group(1)), seconds) if mean else (None, None)


def loudness(program: Path, source: Path, cut: str = "") -> float | None:
    """The mean volume of a file's sound, dB (blocking) - `cut`: of what that filter leaves of it; None - it has
    none, or cannot be read."""
    return _measured(program, source, cut)[0]


# (53.12) where a sound's energy lies: what is left of it above each of these frequencies, Hz
BAND_CUTS = (100, 1000, 4000, 8000)
SILENCE_DB = -70.0  # a sound quieter than this is none


def bands(program: Path, source: Path, *, at: float | None = None,
          seconds: float | None = None) -> dict[str, float] | None:  # fmt: skip
    """Where the energy of a file's sound lies (53.12, blocking): {level - its mean volume, dB; low - the share of
    its energy below 100 Hz, a rumble no ordinary loudspeaker gives; mid - of what is above 100 Hz, the share above
    1 kHz; top - the share above 4 kHz; air - above 8 kHz}. `at`, `seconds` - of that piece of it alone. None - no
    sound, silence, or it cannot be read. A sound as from a telephone has next to no `top` and no `air`; a hum has
    no `mid` either (53.10: the video model's «rain» - top 0.05, air 0.01, its «thunder» - mid 0.01; the rain the
    engine of sound makes - top 0.12-0.35, air 0.06-0.19). One run of ffmpeg: the sound split, each copy cut twice
    below its frequency - a steep slope - and measured."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    piece = [] if at is None else ["-ss", f"{at:.3f}", "-t", f"{seconds or 0:.3f}"]
    copies = "".join(f"[i{k}]" for k in range(len(BAND_CUTS) + 1))
    graph = [f"[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,asplit={len(BAND_CUTS) + 1}"
             f"{copies}", "[i0]volumedetect[o0]"]  # fmt: skip
    graph += [f"[i{k}]highpass=f={hz},highpass=f={hz},volumedetect[o{k}]" for k, hz in enumerate(BAND_CUTS, start=1)]
    outs = [arg for k in range(len(BAND_CUTS) + 1) for arg in ("-map", f"[o{k}]", "-f", "null", "-")]
    try:
        done = subprocess.run([str(program), "-hide_banner", *piece, "-i", str(source), "-vn", "-filter_complex",
                               ";".join(graph), *outs], capture_output=True, timeout=CONVERT_TIMEOUT_S,
                              creationflags=flags, check=False)  # fmt: skip
    except OSError, subprocess.TimeoutExpired:
        return None
    said = (done.stderr or b"").decode("utf-8", "replace")
    found = sorted((int(n), float(db)) for n, db in re.findall(
        r"Parsed_volumedetect_(\d+)[^\n]*mean_volume: (-?[\d.]+) dB", said))  # fmt: skip
    if len(found) != len(BAND_CUTS) + 1 or found[0][1] < SILENCE_DB:
        return None
    whole, heard, *above = (10 ** (db / 10) for _n, db in found)
    mid, top, air = (min(1.0, part / heard) if heard else 0.0 for part in above)
    return {"level": found[0][1], "low": max(0.0, 1 - heard / whole), "mid": mid, "top": top, "air": air}


def sound_of(program: Path, source: Path, target: Path) -> None:
    """A clip's sound alone, a WAV of 48 kHz mono (53.12, blocking): what the listener is given to hear."""
    _written(program, ["-i", str(source), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                       "-f", "wav"], target, N_("ffmpeg не взял звук клипа: {said}"))  # fmt: skip


def _level(now: float | None, wanted: float) -> str:
    """The filter that brings a track of this mean volume to the one wanted; one that is silence, or unread, is left
    as it is."""
    if now is None or now < -80:
        return "volume=1"
    return f"volume={max(-GAIN_DB, min(GAIN_DB, wanted - now)):.1f}dB"


def _stretches(spoken: list[tuple[float, float]], seconds: float) -> list[tuple[float, float]]:
    """When the bed is down: each line's time with a slope's room before and after it, the lines a short pause
    apart as one stretch; inside the film."""
    found: list[tuple[float, float]] = []
    for begins, ends in sorted(spoken):
        begins, ends = max(0.0, begins - BED_SLOPE_S), min(seconds, ends + BED_SLOPE_S)
        if found and begins - found[-1][1] < BED_PAUSE_S:
            found[-1] = (found[-1][0], max(found[-1][1], ends))
        else:
            found.append((begins, ends))
    return found


def _lowered(spoken: list[tuple[float, float]], seconds: float) -> str:
    """The filter that takes the bed down under the narrator's stretches of speech: its volume by the film's time."""
    slopes = [f"clip(min((t-{a:.3f})/{BED_SLOPE_S},({b:.3f}-t)/{BED_SLOPE_S}),0,1)" for a, b in
              _stretches(spoken, seconds)]  # fmt: skip
    down = slopes[0]
    for slope in slopes[1:]:
        down = f"max({down},{slope})"
    return f"volume=volume='1-{1 - 10 ** (-BED_DOWN_DB / 20):.4f}*{down}':eval=frame"


def _silenced(stretches: list[tuple[float, float]]) -> str:
    """The filter that takes the film's own sound away in these stretches (the second each begins at, how long it
    is): down and up again in EFFECT_FADE_S - as the made sound that takes its place comes in and goes out."""
    if not stretches:
        return ""
    gone = [f"clip(min((t-{at:.3f})/{EFFECT_FADE_S},({at + long:.3f}-t)/{EFFECT_FADE_S}),0,1)"
            for at, long in stretches]  # fmt: skip
    down = gone[0]
    for one in gone[1:]:
        down = f"max({down},{one})"
    return f",volume=volume='1-{down}':eval=frame"


def dub(program: Path, source: Path, target: Path, *, seconds: float, lines: list[tuple[Path, float]] | None = None,
        music: Path | None = None, effects: list[tuple[Path, float, float]] | None = None,
        own_music: bool = False, mute: list[tuple[float, float]] | None = None) -> None:  # fmt: skip
    """The joined film with its added sound (53.5, blocking): the picture as it is - copied, not coded anew - and
    one sound track of `seconds`: the film's own sound (its scenes', when they have one), the scenes' made sounds
    (`effects`: file, the second it begins at, how long it is), music under the whole film (`music`: a shorter one
    goes round again; it comes in and fades out) and the narrator's lines, each at its second (`lines`; one that
    runs past the film's end is cut there). Each kind is brought to its own mean volume; while the narrator speaks,
    all the rest is down by BED_DOWN_DB - steadily, a stretch of speech at a time. A made piece is cut below its
    kind's frequency first (VOICE_CUT...), and its volume is that of what is left; `own_music` - the music is the
    user's file: as it is. `mute` (53.14) - the stretches of the film (begins, seconds) where its own sound is taken
    away: the scenes whose sound, as the video model filmed it, was not right - a made one is in `effects`.

    Every sound is cut to its length where it is read (`-t` before its `-i`), and a track is made as long as the
    film by mixing it with silence of that length - nothing in the graph throws a sound's end away. Cut inside the
    graph (`apad,atrim` after a line longer than what is left of the film), ffmpeg 9.0 never ended every second
    time (53.5, the test); so did music asked to go round without end - it goes round a counted number of times."""
    lines, effects = lines or [], effects or []
    args, graph = ["-i", str(source)], []
    silence = f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={seconds:.3f},aformat=sample_fmts=fltp"
    bed, said, spoken, at = [], [], [], 1
    if heard(program, source):
        graph.append(f"[0:a]{SOUND}{_silenced(mute or [])}[own]")
        bed.append("[own]")
    for clip, begins, length in effects:
        length = min(length, seconds - begins)
        if length <= 2 * EFFECT_FADE_S:
            continue
        args += ["-t", f"{length:.3f}", "-i", str(clip)]
        level = _level(loudness(program, clip, EFFECT_CUT), EFFECT_DB)
        graph.append(f"[{at}:a]{EFFECT_CUT},{SOUND},{level},afade=t=in:d={EFFECT_FADE_S},"
                     f"afade=t=out:st={length - EFFECT_FADE_S:.3f}:d={EFFECT_FADE_S},"
                     f"adelay={round(begins * 1000)}:all=1[e{at}]")  # fmt: skip
        bed.append(f"[e{at}]")
        at += 1
    if music is not None:
        cut = "" if own_music else MUSIC_CUT
        mean, long = _measured(program, music, cut)
        again = max(0, math.ceil(seconds / long) - 1) if long else 0  # times it goes round after the first
        args += [*(["-stream_loop", str(again)] if again else []), "-t", f"{seconds:.3f}", "-i", str(music)]
        come, go = MUSIC_FADES_S
        graph.append(f"[{at}:a]{cut + ',' if cut else ''}{SOUND},{_level(mean, MUSIC_DB)},afade=t=in:d={come},"
                     f"afade=t=out:st={max(0.0, seconds - go):.3f}:d={go}[music]")  # fmt: skip
        bed.append("[music]")
        at += 1
    for clip, begins in lines:
        if seconds - begins < 0.1:  # a line that would begin where the film ends
            continue
        args += ["-t", f"{seconds - begins:.3f}", "-i", str(clip)]
        mean, long = _measured(program, clip, VOICE_CUT)
        graph.append(f"[{at}:a]{VOICE_CUT},{SOUND},{_level(mean, VOICE_DB)},adelay={round(begins * 1000)}:all=1"
                     f"[l{at}]")  # fmt: skip
        said.append(f"[l{at}]")
        spoken.append((begins, min(seconds, begins + (long or seconds))))
        at += 1
    if not bed and not said:
        raise EngineError(tr("добавлять нечего: ни голоса, ни музыки, ни звуков"))

    def whole(tracks: list[str], name: str) -> str:
        """The tracks as one, as long as the film: mixed with silence of the film's length."""
        graph.append(f"{silence}[quiet{name}]")
        graph.append(f"[quiet{name}]{''.join(tracks)}amix=inputs={len(tracks) + 1}:normalize=0:"
                     f"dropout_transition=0[{name}]")  # fmt: skip
        return f"[{name}]"

    if said and bed:
        graph.append(f"{whole(bed, 'bed')}{_lowered(spoken, seconds)}[low]")
        graph.append(f"[low]{whole(said, 'voice')}amix=inputs=2:normalize=0:dropout_transition=0[all]")
        last = "[all]"
    else:
        last = whole(said or bed, "all")
    graph.append(f"{last}alimiter=limit=0.95[a]")
    _written(program, [*args, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-f", "mp4"], target,
             N_("ffmpeg не свёл звук фильма: {said}"), timeout=JOIN_TIMEOUT_S)  # fmt: skip
