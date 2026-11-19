"""audio.cpp - the engine of a film's added sound (stage 53): the narrator's voice, music, a scene's sounds. One
program without Python (github.com/0xShug0/audio.cpp, Apache-2.0), run once for each piece and gone when it is made
- the card is free again; it is a separate program AFO runs, never linked.

The build is pinned (`TAG`: checked live on 2026-11-19, 53.1): CUDA for an NVIDIA card (13.3, or 12.4 for an old
driver - its runtime is a second archive), else the processor's. The Vulkan build is not taken: on the stand it was
4-6 times slower and its sound effect came out as noise. The sha256 GitHub gives is checked; the build lies in
<app>/tools/audio.cpp/<tag>-<build>.

The models are single GGUF files of audio-cpp/audio.cpp-gguf on Hugging Face, pinned by size and sha256 (`PARTS`):
the voice (Qwen3-TTS 1.7B Base: it reads by a clip of a voice - nine clips lie beside this module, `VOICE_CLIPS`;
Russian among its ten languages), music (ACE-Step 1.5 turbo),
sound effects (MOSS-SoundEffect v2), and the listener (CED small: it says what is heard in a sound - AudioSet's
527 names; 0.2 s on the processor, the card not needed). Measured on an RTX 5080: 12 s of speech in 3-5 s and
2.3-3.5 GB of the card (15 s on the processor alone), 20 s of music in 8 s and 9.8 GB, 10 s of a sound effect in
15 s and 5.4 GB - none of them fits beside a video model, so the hub unloads the models first (hub/sound.py).
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
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
        "name": N_("Голос диктора"), "about": N_("Qwen3-TTS 1.7B: читает голосом по образцу, девять голосов"),
        "path": "Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf",
        "size": 2695186240, "sha256": "92641a8022d521efd79a5db45e952589b3f2b781680f9abfcf26f075d37b86c6",
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
    "listener": {
        "name": N_("Слушатель"), "about": N_("CED small: узнаёт, что слышно в звуке сцены, — проверка звука"),
        "path": "CED-GGUF/ced-small-f32.gguf",
        "size": 86607904, "sha256": "82c3c1535552717a76d9a9ea934c7dd9f2f0f801bba7cbf24631b06db53f8e12",
        "family": "ced", "licence": "Apache-2.0",
    },
}  # fmt: skip
# The narrator's voices. Each is a clip beside this module (voices/<id>.wav, 7-12 s of Russian, 24 kHz mono): the
# model reads a line by the clip's voice. (53.8, the user's ear: «говорят несколько голосов») The model with the
# voices built in (CustomVoice) did not hold a voice from one short Russian line to the next - a man's «ryan» came
# at 109, 114, 153 and 240 Hz in one film, and so with all nine; neither the seed, nor a low temperature, nor a
# style instruction, nor the whole text at once helped. By one clip the same lines come at 104-136 Hz. The clips are
# that model's own takes - the steadiest of five of each voice, its words checked by a recogniser.
VOICE_CLIPS = Path(__file__).with_name("voices")
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
SAY_TAKES = 3  # a line that came in another voice, or far too long, is read again - by the next seed
PITCH_OFF = 0.25  # how far a line's pitch may be from its voice's clip
SAY_LONG = (3.0, 1.5)  # a line is too long past this many times its text's time, and these seconds more
CODEC_HZ = 12.5  # the voice model's speech tokens a second
RUN_TIMEOUT_S = 900.0
HEARD = 8  # the listener's labels of a sound, the surest first
HEAR_TIMEOUT_S = 120.0
_HEARD = "custom_schema_output="  # the line the listener's answer is on
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


def _started(program: Path, args: list[str], cancel: threading.Event, timeout: float) -> tuple[int, str]:
    """The program once (blocking): (its exit code, what it said). `cancel` set, or the time out - the program is
    killed. What it says goes to a file, not a pipe: nothing reads a pipe while we wait."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    began = time.monotonic()
    with tempfile.TemporaryFile() as said:
        try:
            process = subprocess.Popen([*command(program), *args], stdout=said, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, cwd=str(program.parent),
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
            return process.returncode, said.read().decode("utf-8", "replace")
        finally:
            if process.poll() is None:
                process.kill()


def run(program: Path, args: list[str], target: Path, cancel: threading.Event, *,
        timeout: float = RUN_TIMEOUT_S) -> dict[str, float]:  # fmt: skip
    """The program once (blocking): one piece of sound into `target` - written beside under another name, then put
    in place; what it measured ({wall_ms, audio_duration_ms, ...}). `cancel` set, or the time out - the program is
    killed and nothing is left."""
    part = target.with_name(f"{target.stem}.part{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        code, text = _started(program, [*args, "--metrics", "--out", str(part)], cancel, timeout)
        if code != 0 or not part.is_file() or part.stat().st_size < 64:
            last = " ".join(text.split())[-300:]
            raise EngineError(tr("движок звука не сделал файл: {said}", said=last or code))
        part.replace(target)
    finally:
        with contextlib.suppress(OSError):
            part.unlink()
    return {name: float(value) for name, value in _METRIC.findall(text)}


def hear(program: Path, args: list[str], cancel: threading.Event, *,
         timeout: float = HEAR_TIMEOUT_S) -> list[tuple[str, float]]:  # fmt: skip
    """What the listener hears in a sound (blocking): its labels, the surest first - [(AudioSet's name, how sure:
    0..1)]. `cancel` set, or the time out - the program is killed."""
    code, text = _started(program, args, cancel, timeout)
    for line in text.splitlines() if code == 0 else []:
        if line.startswith(_HEARD):
            try:
                scores = json.loads(line[len(_HEARD) :])["data"]["scores"]
                return [(str(score["label"]), float(score["score"])) for score in scores]
            except ValueError, KeyError, TypeError:
                break
    raise EngineError(tr("движок звука не сказал, что слышно: {said}", said=" ".join(text.split())[-300:] or code))


def _model(models: Path, part: str, backend: str) -> list[str]:
    return ["--family", PARTS[part]["family"], "--model", str(model_file(models, part)), "--backend", backend]


def language_of(text: str) -> str:
    """The language named to the voice: Russian for Cyrillic letters, else its own guess."""
    return "Russian" if re.search("[а-яё]", text.lower()) else ""  # i18n: keep


def voice_clip(voice: str) -> Path:
    """The clip a voice reads by; a voice not known - the first one's."""
    return VOICE_CLIPS / f"{voice if voice in VOICES else next(iter(VOICES))}.wav"


def say_seconds(text: str) -> float:
    """The longest a line of this text may be: the model, lost, goes on to the end of its tokens - eleven minutes
    of sound for three words (53.8, seen with a low temperature)."""
    return SAY_LONG[0] * len(text) / SPEECH_CPS + SAY_LONG[1]


def speak_args(models: Path, backend: str, *, text: str, voice: str, take: int = 0) -> list[str]:
    """A line read by a voice's clip - a WAV of 24 kHz, mono. The clip gives the voice alone (its words are not
    the model's business: `x_vector_only_mode`); `take` - which reading it is: another seed."""
    named = language_of(text)
    return ["--task", "tts", *_model(models, "voice", backend), "--text", text, "--voice-ref",
            str(voice_clip(voice)), "--request-option", "x_vector_only_mode=true",
            *(["--language", named] if named else []), "--seed", str(SEED + take), "--max-tokens",
            str(math.ceil(CODEC_HZ * say_seconds(text)))]  # fmt: skip


def pitch_of(path: Path, most_s: float = 8.0) -> float | None:
    """The pitch of the voice in a WAV, Hz (blocking, about a second): the median over its voiced pieces of 40 ms,
    each by autocorrelation between 60 and 400 Hz, the sound taken down to 6 kHz. None - no voice is found."""
    try:
        with wave.open(str(path), "rb") as sound:
            rate, channels, width = sound.getframerate(), sound.getnchannels(), sound.getsampwidth()
            raw = sound.readframes(min(sound.getnframes(), int(most_s * rate)))
    except OSError, wave.Error, EOFError:
        return None
    if width != 2 or not raw:
        return None
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)[::channels]
    down = max(1, rate // 6000)
    low = [sum(samples[at : at + down]) / down for at in range(0, len(samples) - down + 1, down)]
    rate //= down
    size, hop, first, last = int(0.04 * rate), int(0.02 * rate), rate // 400, rate // 60
    if len(low) < size + last:
        return None
    loud = 0.25 * sum(v * v for v in low) / len(low)  # half the whole's volume, squared
    found = []
    for at in range(0, len(low) - size - last, hop):
        frame = low[at : at + size]
        power = sum(v * v for v in frame)
        if power / size < loud:
            continue
        near = [sum(a * b for a, b in zip(frame, low[at + n : at + n + size], strict=True))
                for n in range(first, last + 1)]  # fmt: skip
        best = max(near)
        if best <= 0:
            continue
        # the shortest period that is as good as the best: two and three periods of a steady sound are as good too
        lag = first + next(k for k, value in enumerate(near) if value >= 0.9 * best)
        other = sum(v * v for v in low[at + lag : at + lag + size])
        if best > 0.5 * math.sqrt(power * other):  # as periodic as a vowel is
            found.append(rate / lag)
    return statistics.median(found) if len(found) >= 3 else None


@functools.lru_cache(maxsize=32)
def voice_pitch(voice: str) -> float | None:
    """The pitch of a voice's clip (blocking the first time)."""
    return pitch_of(voice_clip(voice))


def amiss(path: Path, *, text: str, voice: str) -> float:
    """How far a line read is from what it should be (blocking): 0 - its voice's pitch exactly; over PITCH_OFF -
    another voice; 1 and more - far too long, or no voice in it at all."""
    try:
        with wave.open(str(path), "rb") as sound:
            seconds = sound.getnframes() / sound.getframerate()
    except OSError, wave.Error, EOFError:
        return 2.0
    usual, now = voice_pitch(voice), pitch_of(path)
    if now is None or seconds >= say_seconds(text) - 0.2:  # at the end of its tokens: it did not stop by itself
        return 1.0 + (0.0 if now is None or not usual else abs(now / usual - 1))
    return abs(now / usual - 1) if usual else 0.0


def music_args(models: Path, backend: str, *, words: str, seconds: float) -> list[str]:
    """Music without words by its description - a WAV of 48 kHz, stereo."""
    length = min(MUSIC_S[1], max(MUSIC_S[0], seconds))
    return ["--task", "gen", *_model(models, "music", backend), "--task-route", "text2music", "--text",
            f"{words.rstrip('. ')}, instrumental, no vocals", "--lyrics", "[instrumental]", "--duration-seconds",
            f"{length:.1f}", "--seed", str(SEED)]  # fmt: skip


def effect_args(models: Path, backend: str, *, words: str, seconds: float, take: int = 0) -> list[str]:
    """What is heard, by its description in English - a WAV of 48 kHz, mono, 30 s at most. `take` (53.13) - which
    making of it this is: another seed, another sound."""
    length = min(EFFECT_S[1], max(EFFECT_S[0], seconds))
    return ["--task", "gen", *_model(models, "effects", backend), "--text", words, "--request-option",
            f"duration_sec={length:.1f}", "--request-option", f"seed={SEED + take}"]  # fmt: skip


def listen_args(models: Path, *, audio: Path) -> list[str]:
    """What is heard in a sound file (a WAV) - on the processor, whatever the build: it takes a fifth of a second
    there (53.10), and the card is someone else's meanwhile."""
    return ["--task", "cls", *_model(models, "listener", "cpu"), "--audio", str(audio), "--threads",
            str(min(8, os.cpu_count() or 4)), "--request-option", f"top_k={HEARD}"]  # fmt: skip


def remove(path: Path) -> None:
    """A model's file gone, and what a download left of it."""
    for each in (path, *path.parent.glob(path.name + ".*")):
        with contextlib.suppress(OSError):
            each.unlink()
    with contextlib.suppress(OSError):
        shutil.rmtree(path.parent) if not any(path.parent.iterdir()) else None
