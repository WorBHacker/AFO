"""audio.cpp - the engine of a film's added sound (stage 53): the narrator's voice, music, a scene's sounds. One
program without Python (github.com/0xShug0/audio.cpp, Apache-2.0), run once for each piece and gone when it is made
- the card is free again; it is a separate program AFO runs, never linked.

The build is pinned (`TAG`: checked live on 2026-11-19, 53.1): CUDA for an NVIDIA card (13.3, or 12.4 for an old
driver - its runtime is a second archive), else the processor's. The Vulkan build is not taken: on the stand it was
4-6 times slower and its sound effect came out as noise. The sha256 GitHub gives is checked; the build lies in
<app>/tools/audio.cpp/<tag>-<build>.

The models are single GGUF files of audio-cpp/audio.cpp-gguf on Hugging Face, pinned by size and sha256 (`PARTS`):
the voice (Qwen3-TTS 1.7B CustomVoice: nine voices, Russian among its ten languages), music (ACE-Step 1.5 turbo),
sound effects (MOSS-SoundEffect v2). Measured on an RTX 5080: 12 s of speech in 3-5 s and 2.3-3.5 GB of the card
(15 s on the processor alone), 20 s of music in 8 s and 9.8 GB, 10 s of a sound effect in 15 s and 5.4 GB - none of
them fits beside a video model, so the hub unloads the models first (hub/sound.py).
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from common.i18n import N_, tr

from . import engines, hf
from .engines import EngineError

REPO = "0xShug0/audio.cpp"
TAG = "v0.9.5"
PROGRAM = "audiocpp_cli.exe"
# the machine's build (common.hardware.build_for) -> the archives of the release: the program, and CUDA's runtime
ARCHIVES = {
    "cuda-13": ("bin-windows-x64-cuda13.3", "cudart-windows-x64-cuda13.3"),
    "cuda-12": ("bin-windows-x64-cuda12.4", "cudart-windows-x64-cuda12.4"),
    "cpu": ("bin-windows-x64-cpu-portable",),
}
MODELS = "audio-cpp/audio.cpp-gguf"  # the repository on Hugging Face
REVISION = "32c1ce1fea57a513eb9386aa62408d47b1cf7ab2"  # as it was on 2026-11-19: the files measured in 53.1
PARTS: dict[str, dict[str, Any]] = {
    "voice": {
        "name": N_("Голос диктора"), "about": N_("Qwen3-TTS 1.7B: девять голосов, русский и ещё девять языков"),
        "path": "Qwen3-TTS-12Hz-1.7B-CustomVoice-GGUF/qwen3-tts-12hz-1.7b-customvoice-q8_0.gguf",
        "size": 2817055200, "sha256": "c8ac3f94b6e73f137c0e7ecc9270bc8faec7a476ff799b87e48844d1d0987ac3",
        "family": "qwen3_tts", "licence": "Apache-2.0",
    },
    "music": {
        "name": N_("Музыка"), "about": N_("ACE-Step 1.5 turbo: музыка без слов по описанию"),
        "path": "ACE-Step1.5-GGUF/turbo/ace-step-1.5-turbo-q8_0.gguf",
        "size": 6185468096, "sha256": "1f0015f81c10a1182531e6207c7aeaa060b8cad3470a2f88daa3cb74ed636e63",
        "family": "ace_step", "licence": "MIT",
    },
    "effects": {
        "name": N_("Звуки сцен"), "about": N_("MOSS-SoundEffect v2: звук сцены по описанию — для видео без звука"),
        "path": "MOSS-SoundEffect-v2-GGUF/moss-soundeffect-v2-q8_0.gguf",
        "size": 4001175520, "sha256": "99feab80176e5fdd178aa005fa5e4079f8f6a8c45e576b8c4b48f26981ce63a1",
        "family": "moss_soundeffect", "licence": "Apache-2.0",
    },
}  # fmt: skip
# the voices of the CustomVoice checkpoint (the program's own list, 53.1); whose they are - by the model's card
VOICES: dict[str, str] = {
    "ryan": N_("Райан — мужской"), "aiden": N_("Эйден — мужской"), "eric": N_("Эрик — мужской"),
    "dylan": N_("Дилан — мужской"), "uncle_fu": N_("Дядя Фу — мужской, низкий"),
    "serena": N_("Серена — женский"), "vivian": N_("Вивиан — женский"), "ono_anna": N_("Анна — женский"),
    "sohee": N_("Сохи — женский"),
}  # fmt: skip
SPEECH_CPS = 12.0  # characters of Russian a second the voices read (53.1: 148 in 10.6-12.3 s)
EFFECT_S = (0.1, 30.0)  # a sound effect's length, as the model takes it
MUSIC_S = (5.0, 240.0)  # music asked for at once
SEED = 7  # the same words give the same sound: a piece made is kept and not made twice
RUN_TIMEOUT_S = 900.0
_METRIC = re.compile(r"^metrics\.([a-z_.]+)=([-0-9.eE]+)\s*$", re.M)


def build_of(machine: str) -> str:
    """The engine's build for the machine's (cuda-13 | cuda-12 | vulkan | cpu): Vulkan is not taken (53.1) - the
    processor's instead."""
    return machine if machine in ("cuda-13", "cuda-12") else "cpu"


def backend_of(build: str) -> str:
    return "cuda" if build.startswith("cuda") else "cpu"


def folder(into: Path, build: str) -> Path:
    return into / "audio.cpp" / f"{TAG}-{build}"


def find(into: Path, build: str) -> Path | None:
    program = folder(into, build) / PROGRAM
    return program if program.is_file() else None


def command(program: Path) -> list[str]:
    """How the program is started; a stand-in of the tests is started its own way."""
    return [str(program)]


def pick(release: dict[str, Any], build: str) -> dict[str, Any] | None:
    """The pinned release's archives for this build: {tag, build, assets}; None - another release, or one lacks."""
    if release.get("tag_name") != TAG or build not in ARCHIVES:
        return None
    named = {a["name"]: a for a in release.get("assets") or []}
    wanted = [named.get(f"audio-{TAG}-{kind}.zip") for kind in ARCHIVES[build]]
    if any(a is None for a in wanted):
        return None
    return {"tag": TAG, "build": build, "assets": [engines._asset(a) for a in wanted]}


async def latest(http: httpx.AsyncClient, build: str) -> dict[str, Any]:
    """The pinned build as GitHub has it now, its sizes and sha256 known."""
    found = pick(await engines.github(http, REPO, f"releases/tags/{TAG}"), build)
    if found is None:
        raise EngineError(tr("в выпуске audio.cpp {tag} нет сборки {build} для Windows", tag=TAG, build=build))
    return found


def install(found: dict[str, Any], into: Path, cancel: threading.Event,
            report: Callable[[int, int], None] = lambda _n, _t: None) -> Path:  # fmt: skip
    """The build downloaded (sha256 checked), unpacked beside under another name and put in place whole (blocking);
    the archives are not kept."""
    target = folder(into, found["build"])
    downloads, part = into / "downloads" / "audio.cpp", target.with_name(target.name + ".part")
    engines.remove_tree(part)
    try:
        engines.fetch(found["assets"], downloads, part, report=report, cancel=cancel)
        if not (part / PROGRAM).is_file():
            raise EngineError(tr("в сборке audio.cpp нет {program}", program=PROGRAM))
        engines.remove_tree(target)
        part.replace(target)
    finally:
        engines.remove_tree(part)
        if not cancel.is_set():  # a download stopped goes on from where it was
            engines.remove_tree(downloads)
    return target / PROGRAM


def model_file(models: Path, part: str) -> Path:
    """Where a part's model lies: <models>/audio/<its file>."""
    return models / "audio" / PARTS[part]["path"].rsplit("/", 1)[1]


def has(models: Path, part: str) -> bool:
    path = model_file(models, part)
    return path.is_file() and path.stat().st_size == PARTS[part]["size"]


def fetch_model(models: Path, part: str, cancel: threading.Event, report: Callable[[int], None],
                progress: hf.Progress | None = None) -> Path:  # fmt: skip
    """A part's model downloaded whole, its sha256 checked (blocking)."""
    spec, target = PARTS[part], model_file(models, part)
    target.parent.mkdir(parents=True, exist_ok=True)
    hf.download(hf.file_url(MODELS, spec["path"], REVISION), target, size=spec["size"], sha256=spec["sha256"],
                token=None, report=report, cancel=cancel, progress=progress)  # fmt: skip
    return target


class Stopped(EngineError):
    """The piece was stopped: nothing is left of it."""


def run(program: Path, args: list[str], target: Path, cancel: threading.Event, *,
        timeout: float = RUN_TIMEOUT_S) -> dict[str, float]:  # fmt: skip
    """The program once (blocking): one piece of sound into `target` - written beside under another name, then put
    in place; what it measured ({wall_ms, audio_duration_ms, ...}). `cancel` set, or the time out - the program is
    killed and nothing is left. What it says goes to a file, not a pipe: nothing reads a pipe while we wait."""
    part = target.with_name(f"{target.stem}.part{target.suffix}")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    target.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    with tempfile.TemporaryFile() as said:
        try:
            process = subprocess.Popen([*command(program), *args, "--metrics", "--out", str(part)], stdout=said,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(program.parent),
                                       creationflags=flags)  # fmt: skip
        except OSError as exc:
            raise EngineError(tr("движок звука не запустился: {error}", error=exc)) from exc
        try:
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() - began > timeout:
                    process.kill()
                    process.wait(timeout=30)
                    if cancel.is_set():
                        raise Stopped(tr("остановлено"))
                    raise EngineError(tr("движок звука не ответил за {seconds} с", seconds=round(timeout)))
                time.sleep(0.1)
            said.seek(0)
            text = said.read().decode("utf-8", "replace")
            if process.returncode != 0 or not part.is_file() or part.stat().st_size < 64:
                last = " ".join(text.split())[-300:]
                raise EngineError(tr("движок звука не сделал файл: {said}", said=last or process.returncode))
            part.replace(target)
        finally:
            if process.poll() is None:
                process.kill()
            with contextlib.suppress(OSError):
                part.unlink()
    return {name: float(value) for name, value in _METRIC.findall(text)}


def _model(models: Path, part: str, backend: str) -> list[str]:
    return ["--family", PARTS[part]["family"], "--model", str(model_file(models, part)), "--backend", backend]


def language_of(text: str) -> str:
    """The language named to the voice: Russian for Cyrillic letters, else its own guess."""
    return "Russian" if re.search("[а-яё]", text.lower()) else ""  # i18n: keep


def speak_args(models: Path, backend: str, *, text: str, voice: str) -> list[str]:
    """A line read by one of the model's voices - a WAV of 24 kHz, mono."""
    named = language_of(text)
    return ["--task", "tts", *_model(models, "voice", backend), "--text", text, "--request-option",
            f"voice_id={voice if voice in VOICES else next(iter(VOICES))}", *(["--language", named] if named else []),
            "--seed", str(SEED)]  # fmt: skip


def music_args(models: Path, backend: str, *, words: str, seconds: float) -> list[str]:
    """Music without words by its description - a WAV of 48 kHz, stereo."""
    length = min(MUSIC_S[1], max(MUSIC_S[0], seconds))
    return ["--task", "gen", *_model(models, "music", backend), "--task-route", "text2music", "--text",
            f"{words.rstrip('. ')}, instrumental, no vocals", "--lyrics", "[instrumental]", "--duration-seconds",
            f"{length:.1f}", "--seed", str(SEED)]  # fmt: skip


def effect_args(models: Path, backend: str, *, words: str, seconds: float) -> list[str]:
    """What is heard, by its description in English - a WAV of 48 kHz, mono, 30 s at most."""
    length = min(EFFECT_S[1], max(EFFECT_S[0], seconds))
    return ["--task", "gen", *_model(models, "effects", backend), "--text", words, "--request-option",
            f"duration_sec={length:.1f}", "--request-option", f"seed={SEED}"]  # fmt: skip


def remove(path: Path) -> None:
    """A model's file gone, and what a download left of it."""
    for each in (path, *path.parent.glob(path.name + ".*")):
        with contextlib.suppress(OSError):
            each.unlink()
    with contextlib.suppress(OSError):
        shutil.rmtree(path.parent) if not any(path.parent.iterdir()) else None
