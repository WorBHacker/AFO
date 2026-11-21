"""«Улучшить ролик» (stage 54): the programs that make a video smoother, slower or larger - without Python, each a
part fetched when the person asks for it, run once for each piece and gone when it is done. Separate programs AFO
runs, never linked.

- «Плавность» (`smooth`): RIFE (github.com/TNTwise/rife-ncnn-vulkan, MIT; its model rife-v4.26) - the frames between
  frames: 24 → 60 a second, or a video slowed two or four times. Vulkan: any card; the program takes the discrete one
  by itself (54.1: the RTX 5080, not the processor's Intel beside it).
- «Быстрое увеличение» (`sharp`): Real-ESRGAN (github.com/xinntao/Real-ESRGAN, BSD-3; realesrgan-x4plus) - each frame
  four times larger, ffmpeg brings it to twice. Vulkan, any card.
- «Увеличение с дорисовкой» (`detail`): RelayVSR ×2 (huggingface.co/Comfy-Org/RelayVSR, Apache-2.0) through `sd-cli
  -M vid_upscale` of stable-diffusion.cpp `SD_TAG` - a newer build than the one that films (`engines.SD_TAG`): 53
  changes after it, the core's among them (54.1), so it stands beside and does only this. It adds details and does
  not flicker, but takes 14.5 GB of the card: an NVIDIA card of 16 GB (`DETAIL_GIB`), and not the «Качественнее»
  sizes. Its CUDA libraries are the filming build's own files (the same archive, `engines.SD_RUNTIME`): hard links
  when that build is here, else the archive is fetched.
- «Выделение предмета» (`select`, stage 56): SAM 2.1 - the first engine on PyTorch, with Python: its environment,
  install and runs are hub/torch_engines.py's; here it is a part as the others are.
- «Замена и дорисовка» (`replace`, stage 58): Wan2.1-VACE-1.3B, the second engine on PyTorch - an object or the
  background drawn anew where SAM 2.1 found it, or the frame's edges drawn to another format. Planned here (which
  frames, which masks, the windows of 81 frames - `windows_of`), drawn by hub/torch_scripts/replace.py. «Быстрее»
  (58.1): 20 steps, and a video of 24 frames a second drawn at 16 - the frames between by RIFE («Плавность»):
  a third fewer windows, the object following the hand in the probe; «Тщательнее» - 25 steps, every frame.

What 54.1 measured (RTX 5080; a scene of LTX, 768×512, 73 frames): RIFE 2.4-3.9 s for 60 a second, +0.5 GB of the
card; Real-ESRGAN 35 s, +2.4 GB; RelayVSR 24 s, +14 GB - its window must take the whole piece (`detail_args`): with
16 frames the engine runs out of the card at its third reference frame. SeedVR2 was measured and not taken: it
flickers thrice as much, darkens, and draws its own details.

The programs' archives are pinned by tag, name, size and sha256 (`PARTS`): RIFE's and Real-ESRGAN's came to GitHub
before it gave sums - theirs were taken in 54.1. What is kept of an archive (`keep`) - the program and the model it
runs - lies in <tools>/enhance/<part>; RelayVSR's weights in <models>/upscale. What is on the disk is the state, as
with the sound's parts (hub/sound.py); the window is told by `enhance.changed`.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import itertools
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx

from common.i18n import N_, decimal, tr
from common.paths import engines_home

from . import engines, hf, torch_engines
from . import ffmpeg as video
from .engines import EngineError
from .events import EventBus

log = logging.getLogger(__name__)

SD_TAG = "master-1106-532d2c1"  # the first build with RelayVSR that worked here (54.1, 2026-11-19)
RUNTIME_DLLS = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")  # what `engines.SD_RUNTIME` holds
RIFE_MODEL = "rife-v4.26"
SHARP_MODEL = "realesrgan-x4plus"
PARTS: dict[str, dict[str, Any]] = {
    "smooth": {
        "name": N_("Плавность"), "about": N_("RIFE 4.26: 60 кадров в секунду и замедление в 2 и 4 раза"),
        "licence": "MIT", "program": "rife-ncnn-vulkan.exe",
        "keep": ("rife-ncnn-vulkan.exe", "vcomp140.dll", "LICENSE", f"{RIFE_MODEL}/*"),
        "files": ({"repo": "TNTwise/rife-ncnn-vulkan", "tag": "20250112", "name": "windows.zip", "size": 826923873,
                   "sha256": "42ed35e115b026f222386648920218cb8a9c7ae1e23698a7363bdd2e1455aba3"},),
    },
    "sharp": {
        "name": N_("Быстрое увеличение"), "about": N_("Real-ESRGAN: ролик вдвое больше — кадр за кадром"),
        "licence": "BSD-3-Clause", "program": "realesrgan-ncnn-vulkan.exe",
        "keep": ("realesrgan-ncnn-vulkan.exe", "vcomp140.dll", f"models/{SHARP_MODEL}.*"),
        "files": ({"repo": "xinntao/Real-ESRGAN", "tag": "v0.2.5.0", "size": 45474481,
                   "name": "realesrgan-ncnn-vulkan-20220424-windows.zip",
                   "sha256": "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"},),
    },
    "detail": {
        "name": N_("Увеличение с дорисовкой"),
        "about": N_("RelayVSR: вдвое больше, с новыми деталями и без мерцания — видеокарта NVIDIA от 16 ГБ"),
        "licence": "Apache-2.0", "program": "sd-cli.exe", "keep": None,
        "files": (
            {"repo": engines.SD_CPP, "tag": SD_TAG, "size": 412365497,
             "name": f"sd-master-{SD_TAG.rsplit('-', 1)[1]}-bin-win-cuda12-x64.zip",
             "sha256": "b087219145a382cc1b1e826318289e1f672b9bb6965977734ef0deb85fe2d03a"},
            {"repo": engines.SD_CPP, "tag": SD_TAG, "name": engines.SD_RUNTIME, "size": 563452046, "runtime": True,
             "sha256": "fe20366827d357c00797eebb58244dddab7fd9a348d70090c3871004c320f38d"},
            {"hf": "Comfy-Org/RelayVSR", "revision": "202c35eb0a5bb64b7017d5a09ea9acea87b7a081", "role": "model",
             "path": "diffusion_models/relayvsr_x2_lightx2v_int8_convrot.safetensors", "size": 5163295248,
             "sha256": "240e75e0c17cf2845d559b25254a230ff7458f81449d7a8c5e7e3fb8275c7b3c"},
            {"hf": "Comfy-Org/RelayVSR", "revision": "202c35eb0a5bb64b7017d5a09ea9acea87b7a081", "role": "vae",
             "path": "vae/relayvsr_flash_vae_x2_fp32.safetensors", "size": 127759464,
             "sha256": "b5bb0339acb5b52545911ea29a51b4c5b805776a779222b5a14591571f42e21c"},
        ),
    },
    # (56) an engine on PyTorch: its own environment, put in and run by hub/torch_engines.py
    "select": torch_engines.part("select"),
    "replace": torch_engines.part("replace"),
    "lips": torch_engines.part("lips"),  # (57) a mouth to a voice: MuseTalk - asked first, its licence (62)
}  # fmt: skip
# (58) «Замена и дорисовка»: what VACE draws anew, the formats the edges are drawn to - VACE's frame for each (its
# sides of 16; the new frame takes its proportions, so the source lies in both at one place)
DRAWS = ("object", "background", "edges")
CANVASES = {"9:16": (480, 832), "1:1": (624, 624), "16:9": (832, 480)}
VACE_PIXELS = 832 * 480  # 480p - VACE-1.3B's only size; a larger video is drawn smaller, put back at its own
WINDOW, OVERLAP = 81, 9  # frames a window, the last ones of a window the next one keeps (58.1: no jump at the seam)
STEPS = {True: 20, False: 25}  # «Быстрее», «Тщательнее» (58.1: 20 looked as 25; 15 flatter)
VACE_FPS = 16  # «Быстрее» draws a video of 24 a second at 16; RIFE makes the frames between
GUIDANCE, SEED = 5.0, 42  # without guidance - a blur (58.1)
GROW, FEATHER = 9, 4  # the object's mask grown at VACE's size; the drawn area's soft edge at the video's
# Wan's own negative words, as its examples give them (58.6: with a short one a background drawn anew was all a net of
# dots - the same with the full umT5 and English words; with these - clean)
NEGATIVE = ("Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, "
            "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
            "poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still "
            "picture, messy background, three legs, many people in the background, walking backwards")  # fmt: skip
WORDS_MAX = 500
VOICE_FILES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")  # (57) a voice's file, as a film's music
VOICE_BYTES = 200 << 20
STEP_S = 40.0  # a step at most, for the time out (58.1: 17.8-19.3 s, the VAE besides)
LOAD_S = 1200.0  # the model and umT5 loaded, the text encoded
DETAIL_GIB = 15.5  # a card of 16 GB says 15.9: RelayVSR took 14.0-14.5 of it (54.1)
ROOM = 1 << 30  # left free on the disk after a download
TICK_S = 0.5  # how often a download is told to the window
RUN_TIMEOUT_S = 3600.0  # one piece - a scene at most (54.1: RelayVSR 35 s for 121 frames, Real-ESRGAN ~1 s a frame)
OUT_OF_CARD = "cannot make enough memory available"  # sd-cli's words when the card is too small for the piece
FRAMES = video.FRAME_NAME  # how the frames of a piece are named, in and out
MOTIONS = ("fps60", "slow2", "slow4")  # (54.3) 60 frames a second over the same time; two or four times slower
SMOOTH_FPS = 60
SIZES = ("sharp", "detail")  # (54.4) twice as large: the fast way, or with details drawn
PIECE_FRAMES = 121  # a piece enlarged at once - a scene at most: RelayVSR holds all its frames (54.1: 121 - 14.4 GB)
DETAIL_PIXELS = 450_000  # (54.1) 832×480, 768×512, 640×640 fit the card with RelayVSR; 1280×704 does not


class EnhanceError(Exception):
    """A part that cannot be fetched, a piece that cannot be made; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class Stopped(EngineError):
    """The piece was stopped: nothing is left of it."""


def folder(tools: Path, part: str) -> Path:
    return tools / "enhance" / part


def on_torch(part: str) -> bool:
    """A part that is an engine on PyTorch (56): hub/torch_engines.py puts it in and runs it."""
    return bool(PARTS[part].get("torch"))


def find(tools: Path, part: str) -> Path | None:
    if on_torch(part):
        program = torch_engines.python_of(torch_engines.home(), part)
        return program if program.is_file() else None
    program = folder(tools, part) / PARTS[part]["program"]
    return program if program.is_file() else None


def weight_file(models: Path, spec: dict[str, Any]) -> Path:
    """Where a weight of Hugging Face lies: <models>/upscale/<its file>."""
    return models / "upscale" / spec["path"].rsplit("/", 1)[1]


def weights(part: str) -> list[dict[str, Any]]:
    return [f for f in PARTS[part].get("files", ()) if "hf" in f]


def releases(part: str) -> list[dict[str, Any]]:
    return [f for f in PARTS[part].get("files", ()) if "repo" in f]


def has(tools: Path, models: Path, part: str) -> bool:
    """The part's program here, and its weights whole."""
    if on_torch(part):
        return torch_engines.has(models, part)
    return find(tools, part) is not None and all(
        (path := weight_file(models, spec)).is_file() and path.stat().st_size == spec["size"] for spec in weights(part)
    )


def runtime_here() -> Path | None:
    """The filming build's folder when it holds the CUDA libraries - those of the same archive (54.1): linked, not
    fetched again. None - not here."""
    found = engines_home() / "stable-diffusion.cpp" / f"{engines.SD_TAG}-cuda12"
    return found if all((found / name).is_file() for name in RUNTIME_DLLS) else None


def size(part: str, linked: bool, models: Path | None = None) -> int:
    """What «Скачать» brings, in bytes: the archives (the CUDA libraries not, when they are linked) and the weights
    (of an engine on PyTorch - those not whole in `models` yet)."""
    if on_torch(part):
        return torch_engines.size(part, models)
    return sum(f["size"] for f in PARTS[part]["files"] if not (f.get("runtime") and linked))


def disk(part: str, linked: bool) -> int:
    """What the part takes on the disk once in: what it brings - an engine on PyTorch unpacks to more (56)."""
    return torch_engines.disk(part) if on_torch(part) else size(part, linked)


def usable(part: str, machine: dict[str, Any]) -> str:
    """Why the part cannot work on this computer, in words; '' - it can."""
    if on_torch(part):
        return torch_engines.usable(part, machine)
    if part != "detail":
        return ""
    if machine.get("build") != "cuda12" or float(machine.get("memory_gib") or 0) < DETAIL_GIB:
        return tr("нужна видеокарта NVIDIA от 16 ГБ")
    return ""


async def assets(http: httpx.AsyncClient, part: str, linked: bool) -> list[dict[str, Any]]:
    """The part's archives as GitHub has them now: where each is downloaded from, its size and sha256 - ours: a file
    of another size, or one GitHub sums otherwise, is not the one checked (54.1) and is not put in."""
    found = []
    for spec in releases(part):
        if spec.get("runtime") and linked:
            continue
        release = await engines.github(http, spec["repo"], f"releases/tags/{spec['tag']}")
        asset = next((a for a in release.get("assets") or [] if a.get("name") == spec["name"]), None)
        if asset is None:
            raise EngineError(tr("в выпуске {repo} {tag} нет {name}", repo=spec["repo"], tag=spec["tag"],
                                 name=spec["name"]))  # fmt: skip
        given = engines._asset(asset)
        if given["size"] != spec["size"] or given["sha256"] not in (None, spec["sha256"]):
            raise EngineError(tr("{name} на GitHub не тот, что проверен: другой размер или сумма — не ставлю",
                                 name=spec["name"]))  # fmt: skip
        found.append(given | {"sha256": spec["sha256"], "runtime": bool(spec.get("runtime"))})
    return found


def _kept(root: Path, keep: tuple[str, ...] | None) -> list[Path]:
    """The files of an unpacked archive kept (relative to the program's folder): all, or those the patterns name."""
    files = [p for p in root.rglob("*") if p.is_file()]
    if keep is None:
        return files
    return [p for p in files if any(fnmatch.fnmatch(p.relative_to(root).as_posix(), k) for k in keep)]


def _link(source: Path, target: Path) -> None:
    """A hard link on the same disk - no byte more; a copy elsewhere."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def install(part: str, found: list[dict[str, Any]], tools: Path, models: Path, cancel: threading.Event,
            report: Callable[[int, int], None], runtime: Path | None) -> Path:  # fmt: skip
    """The part fetched (blocking): its archives (sha256 checked), what is kept of them put in place whole, the CUDA
    libraries linked from `runtime` (or unpacked from their archive), then its weights. A download stopped goes on
    from where it was; the archives are not kept."""
    spec = PARTS[part]
    target = folder(tools, part)
    # each part's own: two parts fetched at once do not throw away each other's
    downloads, part_dir = tools / "downloads" / "enhance" / part, target.with_name(target.name + ".part")
    total = sum(a["size"] for a in found) + sum(w["size"] for w in weights(part))
    got = 0
    if find(tools, part) is None:
        engines.remove_tree(part_dir)
        try:
            with tempfile.TemporaryDirectory(dir=tools) as unpacked:
                programs = [a for a in found if not a["runtime"]]
                engines.fetch(programs, downloads, Path(unpacked), cancel=cancel,
                              report=lambda n, _t: report(n, total))  # fmt: skip
                got = sum(a["size"] for a in programs)
                program = next(iter(sorted(Path(unpacked).rglob(spec["program"]), key=lambda p: len(p.parts))), None)
                if program is None:
                    raise EngineError(tr("в архиве нет {program}", program=spec["program"]))
                root = program.parent
                part_dir.mkdir(parents=True)
                for file in _kept(root, spec["keep"]):
                    (part_dir / file.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, part_dir / file.relative_to(root))
            libraries = [a for a in found if a["runtime"]]
            if libraries:  # not linked: their own archive
                engines.fetch(libraries, downloads, part_dir, cancel=cancel,
                              report=lambda n, _t: report(got + n, total))  # fmt: skip
                got += sum(a["size"] for a in libraries)
            elif any(f.get("runtime") for f in spec["files"]) and runtime is not None:
                for name in RUNTIME_DLLS:
                    _link(runtime / name, part_dir / name)
            engines.remove_tree(target)
            part_dir.replace(target)
        finally:
            engines.remove_tree(part_dir)
            if not cancel.is_set():
                engines.remove_tree(downloads)
    else:
        got = sum(a["size"] for a in found)
    for weight in weights(part):
        path = weight_file(models, weight)
        if path.is_file() and path.stat().st_size == weight["size"]:
            got += weight["size"]
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        hf.download(hf.file_url(weight["hf"], weight["path"], weight["revision"]), path, size=weight["size"],
                    sha256=weight["sha256"], token=None, cancel=cancel,
                    report=lambda n, b=got: report(b + n, total))  # fmt: skip
        got += weight["size"]
    return target / spec["program"]


def remove(tools: Path, models: Path, part: str) -> None:
    if on_torch(part):
        torch_engines.remove(models, part)
        return
    engines.remove_tree(folder(tools, part))
    for weight in weights(part):
        with contextlib.suppress(OSError):
            weight_file(models, weight).unlink(missing_ok=True)


# --- the programs ---


def command(program: Path) -> list[str]:
    """How the program is started; a stand-in of the tests is started its own way."""
    return [str(program)]


def run(program: Path, args: list[str], cancel: threading.Event, *, timeout: float = RUN_TIMEOUT_S) -> str:
    """The program once (blocking), in its own folder (its models lie there): what it said. `cancel` set, or the
    time out - the program is killed. It failed - its words; the card too small for the piece - said so."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    began = time.monotonic()
    with tempfile.TemporaryFile() as said:  # a file, not a pipe: nothing reads a pipe while we wait
        try:
            process = subprocess.Popen([*command(program), *args], stdout=said, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, cwd=str(program.parent),
                                       creationflags=flags)  # fmt: skip
        except OSError as exc:
            raise EngineError(tr("программа улучшения не запустилась: {error}", error=exc)) from exc
        try:
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() - began > timeout:
                    process.kill()
                    process.wait(timeout=30)
                    if cancel.is_set():
                        raise Stopped(tr("остановлено"))
                    raise EngineError(tr("программа улучшения не закончила за {minutes} мин",
                                         minutes=round(timeout / 60)))  # fmt: skip
                time.sleep(0.1)
            said.seek(0)
            text = said.read().decode("utf-8", "replace")
        finally:
            if process.poll() is None:
                process.kill()
    if process.returncode != 0:
        if OUT_OF_CARD in text:
            raise EngineError(tr("не хватило памяти видеокарты — закройте программы, которые её занимают, или "
                                 "выберите быстрое увеличение"))  # fmt: skip
        raise EngineError(tr("программа улучшения не справилась: {said}",
                             said=" ".join(text.split())[-300:] or process.returncode))  # fmt: skip
    return text


def smooth_args(source: Path, target: Path, frames: int) -> list[str]:
    """RIFE: the frames of `source` (a folder) into `frames` frames in `target`, evenly between them."""
    return ["-i", str(source), "-o", str(target), "-n", str(frames), "-m", RIFE_MODEL, "-f", FRAMES]


def sharp_args(source: Path, target: Path) -> list[str]:
    """Real-ESRGAN: each frame of `source` four times larger into `target`, by the same name."""
    return ["-i", str(source), "-o", str(target), "-n", SHARP_MODEL, "-s", "4", "-f", "png"]


def detail_args(models: Path, source: Path, target: Path, *, frames: int, fps: int) -> list[str]:
    """RelayVSR ×2: the frames of `source` into `target`. Its window takes the whole piece (54.1)."""
    model, vae = (weight_file(models, w) for w in weights("detail"))
    return ["-M", "vid_upscale", "--diffusion-model", str(model), "--vae", str(vae), "--input-frames", str(source),
            "--upscale-factor", "2", "--fps", str(fps), "--vsr-window-size", str(max(frames, 2)), "-s", "42",
            "-o", str(target / FRAMES)]  # fmt: skip


def subject(raw: Any) -> dict[str, float] | None:
    """«Только предмет» (56): {share: where in the video - 0 its first frame, 1 its last; x, y: where in the frame,
    shares of its width and height}; None - not asked. Anything else - refused."""
    if raw is None:
        return None
    try:
        found = {k: float(raw[k]) for k in ("share", "x", "y")}
    except TypeError, KeyError, ValueError:
        raise EnhanceError(tr("нет такого улучшения"), 422) from None
    if not all(0.0 <= v <= 1.0 for v in found.values()):
        raise EnhanceError(tr("нет такого улучшения"), 422)
    return found


def drawing(raw: Any) -> dict[str, Any] | None:
    """(58) «Заменить предмет», «Заменить фон», «Формат»: {kind: object | background | edges, words: what to draw,
    fast: «Быстрее» (else «Тщательнее»), share, x, y: where the object was clicked (object, background), canvas: the
    format (edges)}; None - not asked. The words are wanted for an object or background. Anything else - refused."""
    if raw is None:
        return None
    try:
        kind, words, fast = str(raw["kind"]), str(raw.get("words") or "").strip(), raw.get("fast", True)
    except TypeError, KeyError, AttributeError:
        raise EnhanceError(tr("нет такого улучшения"), 422) from None
    if kind not in DRAWS or len(words) > WORDS_MAX or not isinstance(fast, bool):
        raise EnhanceError(tr("нет такого улучшения"), 422)
    found: dict[str, Any] = {"kind": kind, "words": words, "fast": fast}
    if kind == "edges":
        if raw.get("canvas") not in CANVASES:
            raise EnhanceError(tr("нет такого улучшения"), 422)
        return found | {"canvas": raw["canvas"]}
    if not words:
        raise EnhanceError(tr("напишите, на что заменить"), 422)
    return found | subject({k: raw.get(k) for k in ("share", "x", "y")})  # type: ignore[operator]


def lips_of(raw: Any) -> dict[str, Any] | None:
    """(57) «Губы под голос»: {voice: clip - the video's own sound | file, path: the voice's file; own - (57.4, the
    director's) the new video's sound stays its own, the voice is mixed in elsewhere}; None - not asked. Anything
    else - refused."""
    if raw is None:
        return None
    try:
        voice, path, own = str(raw["voice"]), str(raw.get("path") or "").strip(), raw.get("own") is True
    except TypeError, KeyError, AttributeError:
        raise EnhanceError(tr("нет такого улучшения"), 422) from None
    if voice == "clip":
        return {"voice": "clip"}
    if voice != "file":
        raise EnhanceError(tr("нет такого улучшения"), 422)
    if not path:
        raise EnhanceError(tr("выберите файл с голосом"), 422)
    return {"voice": "file", "path": path} | ({"own": True} if own else {})


def last_said(text: str) -> dict[str, Any]:
    """The last line of JSON a program said; {} - none."""
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            with contextlib.suppress(ValueError):
                found = json.loads(line)
                return found if isinstance(found, dict) else {}
    return {}


def vace_size(width: int, height: int) -> tuple[int, int]:
    """The size VACE draws a video of this size at: its own when 480p or less with sides of 16 (LTX's 768 × 512, a
    film's 832 × 480), else of VACE_PIXELS, the same proportions, sides of 16."""
    if width * height <= VACE_PIXELS and width % 16 == 0 and height % 16 == 0:
        return width, height
    scale = min(1.0, math.sqrt(VACE_PIXELS / (width * height)))
    return max(16, round(width * scale / 16) * 16), max(16, round(height * scale / 16) * 16)


def canvas_of(width: int, height: int, canvas: str) -> tuple[int, int, int, int]:
    """(58) The new frame for «Формат»: its width and height (even), where the source lies in it (x, y) - the source
    at its own size in the middle, the canvas of VACE's proportions around it. Of that format already - refused."""
    vw, vh = CANVASES[canvas]
    if abs(width / height - vw / vh) < 0.03:
        raise EnhanceError(tr("ролик уже в формате {canvas}", canvas=canvas), 422)
    if width / height > vw / vh:  # wider than the format: drawn above and below
        cw, ch = width + width % 2, round(width * vh / vw / 2) * 2
    else:  # narrower: drawn at the sides
        cw, ch = round(height * vw / vh / 2) * 2, height + height % 2
    return cw, ch, (cw - width) // 2, (ch - height) // 2


def stride(fps: int, fast: bool) -> Fraction:
    """How many of a video's frames one frame VACE draws spans (58.1): 1 - each; «Быстрее» on a video of 24 a second
    - 3/2 (it draws 16 a second, RIFE makes the frames between: a frame at a half the source's - its own half-frame
    by RIFE first), of 32 - 2. Other rates - each frame."""
    if not fast or fps <= VACE_FPS:
        return Fraction(1)
    found = Fraction(fps, VACE_FPS)
    return found if found.denominator in (1, 2) else Fraction(1)


def moments_of(frames: int, step: Fraction) -> list[Fraction]:
    """The moments of a shot of `frames` frames VACE draws, in its frames from 0: each `step` - the last one at its
    last frame at most."""
    if step == 1 or frames < 2:
        return [Fraction(j) for j in range(frames)]
    return [min(k * step, Fraction(frames - 1)) for k in range(math.ceil((frames - 1) / step) + 1)]


def windows_of(count: int, longest: int = WINDOW, keep: int = OVERLAP) -> list[tuple[int, int, int]]:
    """[first, end, kept] of each window VACE draws of `count` moments: `longest` at most, each after the first
    beginning with the `keep` last moments of the one before - kept as drawn there (58.1: the new object does not
    change at the seam)."""
    found = [(0, min(count, longest), 0)]
    while found[-1][1] < count:
        first = found[-1][1] - keep
        found.append((first, min(first + longest, count), keep))
    return found


def _copied(frames: Path, first: int, end: int, piece: Path) -> None:
    """The frames first..end-1 (from 0) of a folder into `piece`, numbered from 1 - linked, the folder keeps them."""
    piece.mkdir(parents=True)
    for n in range(first, end):
        _link(frames / (FRAMES % (n + 1)), piece / (FRAMES % (n - first + 1)))


def _progress(path: Path) -> float | None:
    """What replace.py wrote of its steps: a share; None - not yet, or caught while it writes."""
    try:
        done, total = (int(v) for v in path.read_text(encoding="utf-8").split())
    except OSError, ValueError:
        return None
    return done / total if total else None


def motion_frames(frames: int, motion: str, fps: int) -> int:
    """How many frames a shot of `frames` becomes (54.3): 60 a second over the same time, or two or four times as
    many at the video's own rate - so much slower."""
    if motion == "fps60":
        return max(frames, round(frames * SMOOTH_FPS / fps))
    return frames * int(motion.removeprefix("slow"))


def shots(count: int, cuts: list[int]) -> list[tuple[int, int]]:
    """[first, end) of each shot of a video of `count` frames, by the frames where a new one begins (54.3)."""
    return list(itertools.pairwise([0, *sorted({c for c in cuts if 0 < c < count}), count]))


def pieces(count: int, cuts: list[int], longest: int | None = None) -> list[tuple[int, int]]:
    """[first, end) of each piece enlarged at once (54.4): shot by shot - RelayVSR looks at the frames around, and
    another shot's are not this one's -, a long shot in even pieces of `longest` at most; a shot of one frame goes
    with the one before it (or after): a piece of one frame is no video to RelayVSR."""
    found: list[tuple[int, int]] = []
    longest = longest or PIECE_FRAMES
    for first, end in shots(count, cuts):
        if end - first < 2 and found:
            found[-1] = (found[-1][0], end)
            continue
        parts = math.ceil((end - first) / longest)
        found += itertools.pairwise([first + round(k * (end - first) / parts) for k in range(parts + 1)])
    if len(found) > 1 and found[0][1] - found[0][0] < 2:
        found[:2] = [(found[0][0], found[1][1])]
    return found


def png_size(path: Path) -> tuple[int, int]:
    """A PNG's width and height, from its header."""
    with path.open("rb") as file:
        head = file.read(24)
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _gather(frames: Path, first: int, end: int, piece: Path) -> None:
    """The frames first..end-1 (from 0) of a folder moved into `piece`, numbered from 1 - a program takes a folder."""
    piece.mkdir()
    for n in range(first, end):
        (frames / (FRAMES % (n + 1))).replace(piece / (FRAMES % (n - first + 1)))


def _append(out: Path, whole: Path, made: int, wanted: int) -> int:
    """A piece's frames that came out moved to the end of `whole`; how many `whole` has now. Not as many as asked -
    the program did not do it."""
    came = sorted(out.iterdir())
    if len(came) != wanted:
        raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=len(came),
                             wanted=wanted))  # fmt: skip
    for n, frame in enumerate(came, start=made + 1):
        frame.replace(whole / (FRAMES % n))
    return made + len(came)


class Enhance:
    """The parts (`state`, `install`, `stop`, `remove`) and their programs at work (`run`, `make`). The card is not
    taken here: the Studio's job that runs them holds it."""

    def __init__(self, bus: EventBus, *, tools: Path, models: Path, machine: Callable[[], dict[str, Any]]):
        self._bus = bus
        self.tools = tools
        self.models = models
        self.machine = machine  # what this computer's card is (the installer's look); tests give their own
        self._loads: dict[str, dict[str, Any]] = {}  # part -> its download: got, total, going, error...

    def _part(self, part: str, why: str, linked: bool) -> dict[str, Any]:
        spec, load = PARTS[part], self._loads.get(part) or {}
        going = bool(load.get("going"))
        ready = has(self.tools, self.models, part)
        state = "loading" if going else "ready" if ready else "unavailable" if why else "missing"
        whole = size(part, linked, self.models)
        return {"id": part, "name": tr(spec["name"]), "about": tr(spec["about"]), "size": whole,
                "disk": disk(part, linked), "licence": spec["licence"], "state": state, "why": why,
                "got": load.get("got", 0) if going else 0, "total": load.get("total", whole),
                "error": "" if going else load.get("error", "")}  # fmt: skip

    async def state(self) -> dict[str, Any]:
        """What the window shows: each part - ready, loading (how far), missing (its size), unavailable (why)."""
        machine = await asyncio.to_thread(self.machine)

        def look() -> list[dict[str, Any]]:
            linked = runtime_here() is not None
            return [self._part(p, usable(p, machine), linked) for p in PARTS]

        return {"parts": await asyncio.to_thread(look)}

    async def ready(self, part: str) -> bool:
        return await asyncio.to_thread(has, self.tools, self.models, part)

    async def _changed(self) -> dict[str, Any]:
        state = await self.state()
        await self._bus.publish("enhance.changed", state, persist=False)
        return state

    def _known(self, part: str) -> None:
        if part not in PARTS:
            raise EnhanceError(tr("нет такой части улучшения"), 404)

    async def install(self, part: str, *, agreed: bool = False) -> dict[str, Any]:
        """«Скачать»: the part's programs and weights, in the background. The state as it begins; a computer it
        cannot work on, no room on the disk - refused at once. (62) A part whose licence forbids commercial use - not
        without the person's `agreed` (hf.NotForWork); a download stopped and asked again goes on without it."""
        self._known(part)
        load = self._loads.get(part)
        if (load is not None and load["going"]) or await self.ready(part):
            return await self.state()
        spec = PARTS[part]
        if load is None and not agreed and hf.not_for_work(spec["licence"], spec.get("commercial")):
            raise hf.NotForWork(tr(spec["name"]), spec["licence"])
        if why := usable(part, await asyncio.to_thread(self.machine)):
            raise EnhanceError(tr("«{part}» на этом компьютере не работает: {why}", part=tr(PARTS[part]["name"]),
                                  why=why), 422)  # fmt: skip
        runtime = await asyncio.to_thread(runtime_here)
        whole = await asyncio.to_thread(size, part, runtime is not None, self.models)
        needs = await asyncio.to_thread(disk, part, runtime is not None)
        place = torch_engines.home().parent if on_torch(part) else self.tools  # (56) its disk: <engines>
        await asyncio.to_thread(place.mkdir, parents=True, exist_ok=True)
        free = (await asyncio.to_thread(shutil.disk_usage, place)).free
        if free < needs + ROOM:
            raise EnhanceError(tr("на диске мало места: нужно {need} ГБ, свободно {free} ГБ",
                                  need=decimal(f"{(needs + ROOM) / 2**30:.1f}"),
                                  free=decimal(f"{free / 2**30:.1f}")), 507)  # fmt: skip
        load = self._loads[part] = {"got": 0, "total": whole, "error": "", "going": True, "cancel": threading.Event()}
        load["task"] = asyncio.create_task(self._install(part, load, runtime))
        return await self._changed()

    async def _told(self, work: Awaitable[Any]) -> Any:
        """A download in a thread, told to the window as it goes."""
        task = asyncio.ensure_future(work)
        while not task.done():
            await asyncio.wait({task}, timeout=TICK_S)
            await self._changed()
        return task.result()

    async def _install(self, part: str, load: dict[str, Any], runtime: Path | None) -> None:
        def report(got: int, total: int) -> None:
            load["got"], load["total"] = got, total

        try:
            if on_torch(part):  # (56) an engine on PyTorch: its pins are in AFO's code, GitHub is not asked
                await self._told(asyncio.to_thread(torch_engines.install, part, self.models, load["cancel"], report))
                return
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                found = await assets(http, part, runtime is not None) if not await asyncio.to_thread(
                    find, self.tools, part) else []  # fmt: skip
            await self._told(asyncio.to_thread(install, part, found, self.tools, self.models, load["cancel"], report,
                                               runtime))  # fmt: skip
        except hf.Cancelled:
            pass  # «Стоп»: what came stays on the disk for the next time
        except (EngineError, hf.HubError, httpx.HTTPError, OSError) as exc:
            load["error"] = str(exc) or type(exc).__name__
        except Exception:
            log.exception("enhance part %s", part)
            load["error"] = tr("внутренняя ошибка хаба — подробности в журнале")
        finally:
            load["going"] = False  # before the window is told: the last word is not «loading»
            await self._changed()

    async def stop(self, part: str) -> dict[str, Any]:
        """A part's download stopped; what came stays for the next time."""
        self._known(part)
        if (load := self._loads.get(part)) is not None and load["going"]:
            load["cancel"].set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await load["task"]
        return await self.state()

    async def remove(self, part: str) -> dict[str, Any]:
        """A part gone from the disk: its programs and weights."""
        await self.stop(part)
        self._loads.pop(part, None)
        await asyncio.to_thread(remove, self.tools, self.models, part)
        return await self._changed()

    async def close(self) -> None:
        for part in list(self._loads):
            await self.stop(part)

    async def run(self, part: str, args: list[str], cancel: asyncio.Event, *, timeout: float | None = None) -> str:
        """The part's program at a piece, in a thread; `cancel` kills it. Not here - said so. `timeout` - the
        program's own, when the piece is long (58: a film's windows)."""
        program = await asyncio.to_thread(find, self.tools, part)
        if program is None or not await self.ready(part):
            raise EnhanceError(tr("«{part}» не установлено: «Улучшить ролик» → «Скачать»",
                                  part=tr(PARTS[part]["name"])), 422)  # fmt: skip
        halt = threading.Event()
        limit = timeout or RUN_TIMEOUT_S
        work = (lambda: torch_engines.run(part, args, halt, timeout=limit)) if on_torch(part) else (
            lambda: run(program, args, halt, timeout=limit))  # fmt: skip
        going = asyncio.ensure_future(asyncio.to_thread(work))
        waiting = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({going, waiting}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiting.cancel()
            if not going.done():
                halt.set()  # «Стоп», or the hub closes: the program is killed, its thread ends
        try:
            return await going
        except (Stopped, torch_engines.Stopped) as exc:
            raise EnhanceError(str(exc)) from None
        except EngineError as exc:
            raise EnhanceError(str(exc), 502) from None

    async def make(self, source: Path, target: Path, cancel: asyncio.Event, *, ffmpeg: Path, fps: int,
                   size: str | None = None, motion: str | None = None, select: dict[str, Any] | None = None,
                   draw: dict[str, Any] | None = None, lips: dict[str, Any] | None = None,
                   told: Callable[[float], Awaitable[None]] | None = None) -> int:  # fmt: skip
        """A video improved into `target` (MP4); how many frames it has. Its frames taken out by ffmpeg, made by the
        parts' programs, put back together, its sound with them. `size` (54.4): «sharp» - twice as large by
        Real-ESRGAN (four times, then ffmpeg brings each piece to twice: its frames of four times are not kept),
        «detail» - by RelayVSR, only a video of the usual sizes (`DETAIL_PIXELS`). `motion` (54.3): «fps60» - 60
        frames a second over the same time; «slow2», «slow4» - so many times slower, at its own rate, without sound
        (a sound stretched so is not the sound). `select` (56, `subject`): the object clicked kept, the rest
        darkened - first, at the video's own size. Then larger, then the motion: fewer frames are enlarged. Both go
        shot by shot: nothing is made across a cut. `draw` (58, `drawing`): an object, the background or the
        edges drawn anew by VACE - first, at the video's own size; not with `select`. `lips` (57, `lips_of`): the
        face's mouth made to say the video's own sound or a voice's file by MuseTalk - first, at the video's own
        size; not with `select` or `draw`, nor slowed (a slowed video has no sound); with a file - its voice is the new
        video's sound. `told(share)` - how far, after each piece. The frames lie beside `target` meanwhile and are
        gone after, whatever happened."""
        if (motion is not None and motion not in MOTIONS) or (size is not None and size not in SIZES):
            raise EnhanceError(tr("нет такого улучшения"), 422)
        select, draw, lips = subject(select), drawing(draw), lips_of(lips)
        if motion is None and size is None and select is None and draw is None and lips is None:
            raise EnhanceError(tr("выберите, что улучшить"), 422)
        if (select is not None and draw is not None) or (lips is not None and (
                select is not None or draw is not None or motion in ("slow2", "slow4"))):  # fmt: skip
            raise EnhanceError(tr("нет такого улучшения"), 422)
        if motion == "fps60" and fps >= SMOOTH_FPS:
            raise EnhanceError(tr("в ролике уже {fps} кадров в секунду", fps=fps), 422)
        work = target.with_name(f".{target.stem}.work")
        await asyncio.to_thread(engines.remove_tree, work)
        stages = [s for s in ("draw" if draw else None, "select" if select else None, "lips" if lips else None, size,
                              motion) if s is not None]  # fmt: skip

        async def stage(n: int) -> Callable[[float], Awaitable[None]] | None:
            if told is None:
                return None

            async def share(done: float) -> None:
                await told((n + done) / len(stages))

            return share

        try:
            frames = work / "in"
            count = await asyncio.to_thread(video.frames_out, ffmpeg, source, frames)
            width, height = await asyncio.to_thread(png_size, frames / (FRAMES % 1))
            if draw is not None and draw["kind"] == "edges":  # (58) the new frame's size
                width, height, *_place = canvas_of(width, height, draw["canvas"])
            if size == "detail" and width * height > DETAIL_PIXELS:
                raise EnhanceError(tr("«с дорисовкой» — для роликов обычного размера: этот {width} × {height}, "
                                      "выберите быстрое увеличение", width=width, height=height), 422)  # fmt: skip
            cut = await asyncio.to_thread(video.cuts, ffmpeg, source)
            if draw is not None:
                frames = await self._draw(frames, work, count, fps, cut, draw, cancel, await stage(0))
            if select is not None:
                frames = await self._select(frames, work, count, select, cancel, await stage(0))
            sound: Path | None = source
            if lips is not None:
                frames, sound = await self._lips(frames, work, count, fps, lips, source, ffmpeg, cancel,
                                                 await stage(0))  # fmt: skip
            if size is not None:
                frames = await self._enlarge(frames, work, pieces(count, cut), size, (width, height), fps, ffmpeg,
                                             cancel, await stage(stages.index(size)))  # fmt: skip
            if motion is not None:
                frames = await self._smooth(frames, work, shots(count, cut), motion, fps, cancel,
                                            await stage(len(stages) - 1))  # fmt: skip
            sound = None if motion in ("slow2", "slow4") else sound
            made = len(await asyncio.to_thread(os.listdir, frames))
            await asyncio.to_thread(video.from_frames, ffmpeg, frames, target,
                                    fps=SMOOTH_FPS if motion == "fps60" else fps, sound=sound)  # fmt: skip
        except EngineError as exc:
            raise EnhanceError(str(exc), 502) from None
        finally:
            await asyncio.to_thread(engines.remove_tree, work)
        return made

    async def _draw(self, frames: Path, work: Path, count: int, fps: int, cut: list[int], draw: dict[str, Any],
                    cancel: asyncio.Event, told: Callable[[float], Awaitable[None]] | None) -> Path:  # fmt: skip
        """(58) VACE draws anew: SAM 2.1's masks of the object clicked first (an object, the background); then
        shot by shot - nothing drawn across a cut - the moments it draws: each frame, or at «Быстрее» 16 a second
        (RIFE's half-frames for those between two), in windows of 81 that keep the last moments of the one before;
        the frames between drawn ones by RIFE back at the video's rate; then each frame the source with only the
        drawn area put in, its edge soft (edges: the source in the middle of the new frame). The frames come back
        numbered as they were."""
        kind, step = draw["kind"], stride(fps, draw["fast"])
        width, height = await asyncio.to_thread(png_size, frames / (FRAMES % 1))
        masks = None
        if kind != "edges":
            at = min(count - 1, max(0, round(draw["share"] * (count - 1))))
            masks = work / "masks"
            await self.run("select", torch_engines.select_args(self.models, frames, masks, at=at, x=draw["x"],
                                                               y=draw["y"], masks=True), cancel)  # fmt: skip
            came = len(await asyncio.to_thread(os.listdir, masks)) if await asyncio.to_thread(masks.is_dir) else 0
            if came != count:
                raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=came,
                                     wanted=count))  # fmt: skip
        if told is not None:
            await told(0.05)
        canvas = place = None
        if kind == "edges":
            cw, ch, x, y = canvas_of(width, height, draw["canvas"])
            vw, vh = CANVASES[draw["canvas"]]
            fit = vw / cw  # VACE's frame and the new one are of the same proportions
            canvas, place = [cw, ch, x, y], [round(x * fit), round(y * fit), round(width * fit), round(height * fit)]
        else:
            vw, vh = vace_size(width, height)
        moments: list[dict[str, Any]] = []
        windows: list[tuple[int, int, int]] = []
        shots_made: list[tuple[int, int, int, int, Fraction]] = []  # first, end, its first moment, moments, step
        for n, (first, end) in enumerate(shots(count, cut)):
            own = step if end - first > 1 else Fraction(1)
            times = moments_of(end - first, own)
            if own.denominator == 2:  # RIFE: the shot's half-frames - those at a half of the source's
                piece, half = work / "piece", work / "half" / str(n)
                await asyncio.to_thread(_copied, frames, first, end, piece)
                await asyncio.to_thread(half.mkdir, parents=True)
                await self.run("smooth", smooth_args(piece, half, (end - first) * 2), cancel)
                await asyncio.to_thread(engines.remove_tree, piece)
                pictures = [half / (FRAMES % (int(t * 2) + 1)) for t in times]  # RIFE's -n: input j at 2j (58.1)
            else:
                pictures = [frames / (FRAMES % (first + int(t) + 1)) for t in times]
            base = len(moments)
            for t, picture in zip(times, pictures, strict=True):
                near = dict.fromkeys((first + math.floor(t), first + math.ceil(t)))
                moments.append({"frame": str(picture),
                                "masks": [str(masks / (FRAMES % (j + 1))) for j in near] if masks else []})  # fmt: skip
            windows += [(base + a, base + b, k) for a, b, k in windows_of(len(times))]
            shots_made.append((first, end, base, len(times), own))
        drawn, progress = work / "drawn", work / "progress.txt"
        steps, models = STEPS[draw["fast"]], self.models
        plan = {"mode": kind, "prompt": draw["words"], "negative": NEGATIVE, "steps": steps, "guidance": GUIDANCE,
                "seed": SEED, "size": [vw, vh], "grow": GROW, "place": place,
                "model": str(torch_engines.vace_dir(models)), "encoder": str(torch_engines.encoder_file(models)),
                "moments": moments, "windows": windows, "out": str(drawn), "progress": str(progress)}  # fmt: skip
        await asyncio.to_thread((work / "draw.json").write_bytes, json.dumps(plan).encode())
        limit = LOAD_S + len(windows) * steps * STEP_S
        drawing_task = asyncio.ensure_future(self.run("replace", ["--draw", str(work / "draw.json")], cancel,
                                                      timeout=limit))  # fmt: skip
        try:
            while not drawing_task.done():  # how far it is: the steps it wrote
                await asyncio.wait({drawing_task}, timeout=TICK_S * 4)
                share = await asyncio.to_thread(_progress, progress)
                if told is not None and share is not None and not drawing_task.done():
                    await told(0.05 + 0.85 * share)
            await drawing_task
        finally:
            if not drawing_task.done():  # the job itself cancelled: the engine killed with it
                drawing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await drawing_task
        made = []  # each frame: the source, what VACE drew at its moment, its mask
        for n, (first, end, base, many, own) in enumerate(shots_made):
            if own == 1:
                pictures = [drawn / (FRAMES % (base + j + 1)) for j in range(end - first)]
            else:  # RIFE back: -n many × numerator puts moment k at k × numerator; frame j is at j × denominator
                piece, back = work / "piece", work / "back" / str(n)
                await asyncio.to_thread(_copied, drawn, base, base + many, piece)
                await asyncio.to_thread(back.mkdir, parents=True)
                await self.run("smooth", smooth_args(piece, back, many * own.numerator), cancel)
                await asyncio.to_thread(engines.remove_tree, piece)
                pictures = [back / (FRAMES % (j * own.denominator + 1)) for j in range(end - first)]
            for j, picture in enumerate(pictures, start=first):
                made.append({"source": str(frames / (FRAMES % (j + 1))), "drawn": str(picture),
                             "mask": str(masks / (FRAMES % (j + 1))) if masks else None})  # fmt: skip
        out = work / "drawn-in"
        compose = {"mode": kind, "grow": round(GROW * width / vw), "feather": FEATHER, "frames": made,
                   "canvas": canvas, "out": str(out)}  # fmt: skip
        await asyncio.to_thread((work / "compose.json").write_bytes, json.dumps(compose).encode())
        await self.run("replace", ["--compose", str(work / "compose.json")], cancel)
        came = len(await asyncio.to_thread(os.listdir, out)) if await asyncio.to_thread(out.is_dir) else 0
        if came != count:
            raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=came,
                                 wanted=count))  # fmt: skip
        for done in (frames, masks, drawn, work / "half", work / "back"):
            if done is not None:
                await asyncio.to_thread(engines.remove_tree, done)
        if told is not None:
            await told(1.0)
        return out

    async def _select(self, frames: Path, work: Path, count: int, select: dict[str, float], cancel: asyncio.Event,
                      told: Callable[[float], Awaitable[None]] | None) -> Path:  # fmt: skip
        """(56) The object clicked followed through the whole video by SAM 2.1, forward and back from the frame
        clicked - a piece of frames on the card at a time (hub/torch_scripts/select.py). The frames come back by
        their names: it as it is, the rest darkened."""
        at = min(count - 1, max(0, round(select["share"] * (count - 1))))
        out = work / "selected"
        await self.run("select", torch_engines.select_args(self.models, frames, out, at=at, x=select["x"],
                                                           y=select["y"]), cancel)  # fmt: skip
        came = len(await asyncio.to_thread(os.listdir, out)) if await asyncio.to_thread(out.is_dir) else 0
        if came != count:
            raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=came,
                                 wanted=count))  # fmt: skip
        await asyncio.to_thread(engines.remove_tree, frames)
        if told is not None:
            await told(1.0)
        return out

    async def _lips(self, frames: Path, work: Path, count: int, fps: int, lips: dict[str, Any], source: Path,
                    ffmpeg: Path, cancel: asyncio.Event,
                    told: Callable[[float], Awaitable[None]] | None) -> tuple[Path, Path]:  # fmt: skip
        """(57) The face's mouth made to say the voice - the video's own sound or the person's file - by MuseTalk
        (hub/torch_scripts/lips.py): it hears the voice at 16 kHz, as long as the video. The frames come back by their
        names - where a face is and the voice goes on, its mouth drawn anew; the rest as they were. With the new
        frames - the new video's sound: the video's own, or the file's, padded with silence or cut to the video's
        length (`own` - the video's own still: a film's narrator is mixed in when it is joined). No face anywhere -
        said so."""
        seconds, heard = count / fps, Path(lips["path"]) if lips["voice"] == "file" else source
        voice, sound = work / "voice16.wav", source
        await asyncio.to_thread(video.voice_track, ffmpeg, heard, voice, seconds=seconds, rate=16000, channels=1)
        if lips["voice"] == "file" and not lips.get("own"):
            sound = work / "voice.wav"
            await asyncio.to_thread(video.voice_track, ffmpeg, heard, sound, seconds=seconds, rate=48000, channels=2)
        out = work / "lips"
        said = await self.run("lips", torch_engines.lips_args(self.models, frames, voice, out, fps=fps), cancel)
        came = len(await asyncio.to_thread(os.listdir, out)) if await asyncio.to_thread(out.is_dir) else 0
        if came != count:
            raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=came,
                                 wanted=count))  # fmt: skip
        if not last_said(said).get("faces"):
            raise EnhanceError(tr("на кадрах ролика не найдено лицо — губы подставить некуда"), 422)
        await asyncio.to_thread(engines.remove_tree, frames)
        if told is not None:
            await told(1.0)
        return out, sound

    async def _enlarge(self, frames: Path, work: Path, spans: list[tuple[int, int]], size: str,
                       side: tuple[int, int], fps: int, ffmpeg: Path, cancel: asyncio.Event,
                       told: Callable[[float], Awaitable[None]] | None) -> Path:  # fmt: skip
        """Twice as large, piece by piece: each piece's frames into a folder of its own, enlarged, put one after
        another - Real-ESRGAN's four times brought to twice at once and thrown away."""
        whole, made = work / "large", 0
        await asyncio.to_thread(whole.mkdir)
        for n, (first, end) in enumerate(spans):
            if cancel.is_set():
                raise EnhanceError(tr("остановлено"))
            piece, out = work / "piece", work / "piece-out"
            await asyncio.to_thread(_gather, frames, first, end, piece)
            await asyncio.to_thread(out.mkdir)
            if size == "sharp":
                await self.run("sharp", sharp_args(piece, out), cancel)
                came = len(await asyncio.to_thread(os.listdir, out))
                if came != end - first:
                    raise EngineError(tr("программа улучшения сделала {came} кадров вместо {wanted}", came=came,
                                         wanted=end - first))  # fmt: skip
                await asyncio.to_thread(video.scaled_frames, ffmpeg, out, whole, width=side[0] * 2,
                                        height=side[1] * 2, start=made + 1)  # fmt: skip
                made += end - first
            else:
                await self.run("detail", detail_args(self.models, piece, out, frames=end - first, fps=fps), cancel)
                made = await asyncio.to_thread(_append, out, whole, made, end - first)
            await asyncio.to_thread(engines.remove_tree, piece)
            await asyncio.to_thread(engines.remove_tree, out)
            if told is not None:
                await told((n + 1) / len(spans))
        await asyncio.to_thread(engines.remove_tree, frames)
        return whole

    async def _smooth(self, frames: Path, work: Path, spans: list[tuple[int, int]], motion: str, fps: int,
                      cancel: asyncio.Event, told: Callable[[float], Awaitable[None]] | None) -> Path:  # fmt: skip
        """RIFE shot by shot: each shot's frames into a folder of its own, the frames between them made, the shots
        put one after another. A shot of one frame is that frame held."""
        whole, made = work / "smooth", 0
        await asyncio.to_thread(whole.mkdir)
        for n, (first, end) in enumerate(spans):
            if cancel.is_set():
                raise EnhanceError(tr("остановлено"))
            piece, out = work / "piece", work / "piece-out"
            await asyncio.to_thread(_gather, frames, first, end, piece)
            await asyncio.to_thread(out.mkdir)
            wanted = motion_frames(end - first, motion, fps)
            if end - first == 1:
                for k in range(wanted):
                    await asyncio.to_thread(shutil.copyfile, piece / (FRAMES % 1), out / (FRAMES % (k + 1)))
            else:
                await self.run("smooth", smooth_args(piece, out, wanted), cancel)
            made = await asyncio.to_thread(_append, out, whole, made, wanted)
            await asyncio.to_thread(engines.remove_tree, piece)
            await asyncio.to_thread(engines.remove_tree, out)
            if told is not None:
                await told((n + 1) / len(spans))
        return whole
