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
from pathlib import Path
from typing import Any

import httpx

from common.i18n import N_, decimal, tr
from common.paths import engines_home

from . import engines, hf
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
}  # fmt: skip
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


def find(tools: Path, part: str) -> Path | None:
    program = folder(tools, part) / PARTS[part]["program"]
    return program if program.is_file() else None


def weight_file(models: Path, spec: dict[str, Any]) -> Path:
    """Where a weight of Hugging Face lies: <models>/upscale/<its file>."""
    return models / "upscale" / spec["path"].rsplit("/", 1)[1]


def weights(part: str) -> list[dict[str, Any]]:
    return [f for f in PARTS[part]["files"] if "hf" in f]


def releases(part: str) -> list[dict[str, Any]]:
    return [f for f in PARTS[part]["files"] if "repo" in f]


def has(tools: Path, models: Path, part: str) -> bool:
    """The part's program here, and its weights whole."""
    return find(tools, part) is not None and all(
        (path := weight_file(models, spec)).is_file() and path.stat().st_size == spec["size"] for spec in weights(part)
    )


def runtime_here() -> Path | None:
    """The filming build's folder when it holds the CUDA libraries - those of the same archive (54.1): linked, not
    fetched again. None - not here."""
    found = engines_home() / "stable-diffusion.cpp" / f"{engines.SD_TAG}-cuda12"
    return found if all((found / name).is_file() for name in RUNTIME_DLLS) else None


def size(part: str, linked: bool) -> int:
    """What «Скачать» brings, in bytes: the archives (the CUDA libraries not, when they are linked) and the weights."""
    return sum(f["size"] for f in PARTS[part]["files"] if not (f.get("runtime") and linked))


def usable(part: str, machine: dict[str, Any]) -> str:
    """Why the part cannot work on this computer, in words; '' - it can."""
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
        whole = size(part, linked)
        return {"id": part, "name": tr(spec["name"]), "about": tr(spec["about"]), "size": whole,
                "licence": spec["licence"], "state": state, "why": why, "got": load.get("got", 0) if going else 0,
                "total": load.get("total", whole), "error": "" if going else load.get("error", "")}  # fmt: skip

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

    async def install(self, part: str) -> dict[str, Any]:
        """«Скачать»: the part's programs and weights, in the background. The state as it begins; a computer it
        cannot work on, no room on the disk - refused at once."""
        self._known(part)
        load = self._loads.get(part)
        if (load is not None and load["going"]) or await self.ready(part):
            return await self.state()
        if why := usable(part, await asyncio.to_thread(self.machine)):
            raise EnhanceError(tr("«{part}» на этом компьютере не работает: {why}", part=tr(PARTS[part]["name"]),
                                  why=why), 422)  # fmt: skip
        runtime = await asyncio.to_thread(runtime_here)
        whole = size(part, runtime is not None)
        await asyncio.to_thread(self.tools.mkdir, parents=True, exist_ok=True)
        free = (await asyncio.to_thread(shutil.disk_usage, self.tools)).free
        if free < whole + ROOM:
            raise EnhanceError(tr("на диске мало места: нужно {need} ГБ, свободно {free} ГБ",
                                  need=decimal(f"{(whole + ROOM) / 2**30:.1f}"),
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

    async def run(self, part: str, args: list[str], cancel: asyncio.Event) -> str:
        """The part's program at a piece, in a thread; `cancel` kills it. Not here - said so."""
        program = await asyncio.to_thread(find, self.tools, part)
        if program is None or not await self.ready(part):
            raise EnhanceError(tr("«{part}» не установлено: «Улучшить ролик» → «Скачать»",
                                  part=tr(PARTS[part]["name"])), 422)  # fmt: skip
        halt = threading.Event()
        going = asyncio.ensure_future(asyncio.to_thread(run, program, args, halt))
        waiting = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({going, waiting}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiting.cancel()
            if not going.done():
                halt.set()  # «Стоп», or the hub closes: the program is killed, its thread ends
        try:
            return await going
        except Stopped as exc:
            raise EnhanceError(str(exc)) from None
        except EngineError as exc:
            raise EnhanceError(str(exc), 502) from None

    async def make(self, source: Path, target: Path, cancel: asyncio.Event, *, ffmpeg: Path, fps: int,
                   size: str | None = None, motion: str | None = None,
                   told: Callable[[float], Awaitable[None]] | None = None) -> int:  # fmt: skip
        """A video improved into `target` (MP4); how many frames it has. Its frames taken out by ffmpeg, made by the
        parts' programs, put back together, its sound with them. `size` (54.4): «sharp» - twice as large by
        Real-ESRGAN (four times, then ffmpeg brings each piece to twice: its frames of four times are not kept),
        «detail» - by RelayVSR, only a video of the usual sizes (`DETAIL_PIXELS`). `motion` (54.3): «fps60» - 60
        frames a second over the same time; «slow2», «slow4» - so many times slower, at its own rate, without sound
        (a sound stretched so is not the sound). Larger first, then the motion: fewer frames are enlarged. Both go
        shot by shot: nothing is made across a cut. `told(share)` - how far, after each piece. The frames lie beside
        `target` meanwhile and are gone after, whatever happened."""
        if (motion is not None and motion not in MOTIONS) or (size is not None and size not in SIZES):
            raise EnhanceError(tr("нет такого улучшения"), 422)
        if motion is None and size is None:
            raise EnhanceError(tr("выберите, что улучшить"), 422)
        if motion == "fps60" and fps >= SMOOTH_FPS:
            raise EnhanceError(tr("в ролике уже {fps} кадров в секунду", fps=fps), 422)
        work = target.with_name(f".{target.stem}.work")
        await asyncio.to_thread(engines.remove_tree, work)
        stages = [s for s in (size, motion) if s is not None]

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
            if size == "detail" and width * height > DETAIL_PIXELS:
                raise EnhanceError(tr("«с дорисовкой» — для роликов обычного размера: этот {width} × {height}, "
                                      "выберите быстрое увеличение", width=width, height=height), 422)  # fmt: skip
            cut = await asyncio.to_thread(video.cuts, ffmpeg, source)
            if size is not None:
                frames = await self._enlarge(frames, work, pieces(count, cut), size, (width, height), fps, ffmpeg,
                                             cancel, await stage(0))  # fmt: skip
            if motion is not None:
                frames = await self._smooth(frames, work, shots(count, cut), motion, fps, cancel,
                                            await stage(len(stages) - 1))  # fmt: skip
            sound = None if motion in ("slow2", "slow4") else source
            made = len(await asyncio.to_thread(os.listdir, frames))
            await asyncio.to_thread(video.from_frames, ffmpeg, frames, target,
                                    fps=SMOOTH_FPS if motion == "fps60" else fps, sound=sound)  # fmt: skip
        except EngineError as exc:
            raise EnhanceError(str(exc), 502) from None
        finally:
            await asyncio.to_thread(engines.remove_tree, work)
        return made

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
