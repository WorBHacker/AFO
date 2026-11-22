"""Strata (stage 45): the engine of Qwen3.8-Flash-Next's own packs - the experts kept in RAM, guessing ahead (MTP,
DFlash2) - by Niko1221, MIT (github.com/Niko1221/Strata). AFO keeps a copy of its own in <engines>/strata, pinned to
the release checked on the stand (45.0): its control program (with its own Python) and the engine for this
computer's card.

Strata takes its folders from the environment: every one of them points into AFO's copy, so the person's own Strata
(%LOCALAPPDATA%\\Strata, %APPDATA%\\Strata, the installs those remember) is neither seen nor changed. PATH comes
without any folder that holds a strata.cmd: a newer Strata renames an older one's strata.ps1 it finds there (45.0 -
the person's 0.1.51 was healed so once on the stand, and put back).

The download is AFO's - each file's sha256 from GitHub, its progress in the window; Strata chooses the engine for this
computer (`update --check`), checks its signed feed and every file, test-starts it and only then switches to it. With
a model already prepared that test start loads the model (45.0): an update then goes through the card's queue."""

from __future__ import annotations

import json
import os
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

from common import hardware
from common.i18n import tr
from common.paths import engines_home

from . import engines, hf, winjob
from .engines import EngineError
from .system_status import memory_sample

REPO = "Niko1221/Strata"
TAG = "v0.2.18"  # checked on the stand 2026-11-22 (45.0); Strata itself is never asked to update (`strata update`)
CONTROL_VERSION = "0.1.64"  # the control program of that release, by which its folder is named
CONTROL = f"strata-control-{CONTROL_VERSION}-windows-x86_64.zip"
FEED = ("strata-feed.json", "strata-feed.json.sig")  # the signed list of engines: a folder with it is a feed
READY = "ready.json"
RUN_TIMEOUT_S = 900.0  # an engine's install with its test start
JOB_MEMORY = 1 << 40  # the job is for killing the whole tree; Strata minds its own memory
DROPPED = ("PYTHON", "VIRTUAL_ENV", "CONDA", "STRATA_", "UV_", "CLAUDE", "ANTHROPIC", "LOCALCHAT_")
ROOM_GIB = 3.0  # the program, the engine's archive and the engine unpacked (0.07 + 0.74 + 0.7 GB at most), twice over
# (45.2в) the models AFO installs through Strata - its own packs of Qwen3.8-Flash-Next (the user's choice: a plain
# Flash-Next file stays llama.cpp's), as Strata's own setup.py of control 0.1.64 lists them: the family and size it
# is asked for, the RAM its experts want («ram_gb»), the download («download_gb»). The stand ran the Coder: 30 GB of
# RAM, a 16 GB card almost whole (45.0).
MODELS: dict[str, dict[str, Any]] = {
    "flash-next-coder-iq1_m": {"family": "coder", "model": "IQ1_M", "name": "Qwen3.8-Flash-Next Coder IQ1_M",
                               "ram_gib": 32.0, "download_gb": 58.4, "purposes": ["code", "chat"]},
    "flash-next-q2_0": {"family": "qwen", "model": "Q2_0", "name": "Qwen3.8-Flash-Next Q2_0", "ram_gib": 48.0,
                        "download_gb": 66.4, "purposes": ["chat", "code"]},
    "flash-next-iq2_xs": {"family": "qwen", "model": "IQ2_XS", "name": "Qwen3.8-Flash-Next IQ2_XS", "ram_gib": 48.0,
                          "download_gb": 68.0, "purposes": ["chat", "code"]},
    "flash-next-iq3_xxs": {"family": "qwen", "model": "IQ3_XXS", "name": "Qwen3.8-Flash-Next IQ3_XXS",
                           "ram_gib": 60.0, "download_gb": 75.8, "purposes": ["chat", "code"]},
    "flash-next-iq3_s": {"family": "qwen", "model": "IQ3_S", "name": "Qwen3.8-Flash-Next IQ3_S", "ram_gib": 62.0,
                         "download_gb": 83.6, "purposes": ["chat", "code"]},
}  # fmt: skip
# (45.2б) where AFO puts Strata in by itself: its smallest model fits
SMALLEST = "flash-next-coder-iq1_m"
CONTEXT = 65536  # what a model is made ready with: the stand's run (45.0); the window changes it later (45.1в)
PREPARE_TIMEOUT_S = 12 * 3600.0  # tens of GB from Hugging Face on a slow line
CARD_MIN_GIB = 8.0  # setup.py's SPLIT_MIN_VRAM_GB: a card holds the dense weights and the prompt's work
EXTRA_GB = 8.0  # what Strata adds to a model in its folder: the MTP layer 5.2, DFlash2 1.2, the pack 1.5 GB (45.0)
RAM_ROOM_GIB = 3.0  # AFO's rule for what fits: the RAM less 3 GB - Windows 11 keeps about that (the user, 2026-11-12)


class StrataError(EngineError):
    """Strata cannot be fetched, put in or run; the message is for the window."""


def home() -> Path:
    return engines_home() / "strata"


def fits(build: str, vram_total_gib: float, ram_total_gib: float) -> bool:
    """Whether Strata's smallest model is for this computer: llama.cpp's CUDA build (an NVIDIA card with its
    driver - Strata's engine AFO fetches is CUDA's), 8 GB on the card, its experts in the RAM less AFO's room."""
    return verdict(SMALLEST, build, vram_total_gib, ram_total_gib) == "fits"


def persons_own(environ: dict[str, str] | None = None) -> Path | None:
    """The person's own Strata - the one-line installer's terminal in %LOCALAPPDATA%\\Strata - or None. Where there
    is one, AFO puts none of its own in: two would share the person's remembered installs and models."""
    environ = os.environ if environ is None else environ
    local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    found = Path(local) / "Strata" / "terminal"
    return found if found.is_dir() and any(found.glob("*/strata.py")) else None


def data(base: Path) -> Path:
    return base / "data"


def terminal(base: Path) -> Path:
    """The control program's folder: strata.py, its Python, bin/strata.cmd."""
    return data(base) / "terminal" / CONTROL_VERSION


def command(base: Path) -> list[str]:
    """How AFO's Strata is run - by AFO and by llama-swap (`… serve --profile …`): its own Python with strata.py, what
    its bin/strata.cmd does for the Python it carries (the environment below does the rest), and how the stand ran it
    under llama-swap (45.0). No cmd.exe: it takes quotes off a path with a space (a Windows user's name), and between
    llama-swap and the server it would stand between the server's lifeline and its parent."""
    return [str(terminal(base) / "python" / "python.exe"), str(terminal(base) / "strata.py")]


def _without_strata(path: str) -> str:
    return os.pathsep.join(p for p in path.split(os.pathsep) if p and not (Path(p.strip('"')) / "strata.cmd").is_file())


def environment(base: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment AFO's Strata runs in: every folder of its own in `base`, nothing of the person's Strata - nor
    a Python variable of the hub's (Strata has its own Python, 3.12)."""
    environ = dict(os.environ if environ is None else environ)
    out = {k: v for k, v in environ.items() if not k.upper().startswith(DROPPED)}
    for key in [k for k in out if k.upper() == "PATH"]:
        out[key] = _without_strata(out[key])
    own = base / "home"
    for sub in ("Local", "Roaming"):
        (own / sub).mkdir(parents=True, exist_ok=True)
    root = data(base)
    out.update({
        "USERPROFILE": str(own), "HOME": str(own), "LOCALAPPDATA": str(own / "Local"), "APPDATA": str(own / "Roaming"),
        "STRATA_DATA_DIR": str(root), "STRATA_INSTALL_DIR": str(root / "install"),
        "STRATA_COMPONENTS_DIR": str(root / "components"), "STRATA_PROFILE_DIR": str(root / "profiles"),
        "STRATA_PRESET_DIR": str(root / "presets"), "STRATA_RUNTIME_DIR": str(root / "runtime"),
        "STRATA_SETTINGS_FILE": str(root / "settings.json"), "STRATA_NO_PATH": "1",
        "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
    })  # fmt: skip
    return out


def run(base: Path, args: list[str], *, cancel: threading.Event | None = None,
        timeout: float = RUN_TIMEOUT_S) -> str:  # fmt: skip
    """`strata <args>` of AFO's copy (blocking), in a Windows job: what it said. Cancelled or out of time - the job is
    killed with all Strata started; a non-zero end - StrataError with its last words."""
    return _started(base, [str(terminal(base) / "strata.py"), *args], cancel=cancel, timeout=timeout)


def _started(base: Path, argv: list[str], *, cancel: threading.Event | None, timeout: float,
             on_line: Callable[[str], None] | None = None) -> str:  # fmt: skip
    """Strata's Python with `argv`: what it said, each line given to `on_line` as it comes. Its words go to a file,
    read through a handle of its own - one shared with the program would move its place in the file."""
    cancel = cancel or threading.Event()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    began = time.monotonic()
    runs = data(base) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=runs, suffix=".log", delete=False) as said:
        log_file = Path(said.name)
    lines: list[str] = []
    try:
        with open(log_file, "wb") as out, open(log_file, "rb") as seen, winjob.Job(JOB_MEMORY) as job:
            try:
                process = subprocess.Popen([command(base)[0], *argv], stdout=out, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, cwd=str(terminal(base)), env=environment(base),
                                           creationflags=flags)  # fmt: skip
            except OSError as exc:
                raise StrataError(tr("Strata не запустилась: {error}", error=exc)) from exc
            rest = b""

            def read() -> None:
                nonlocal rest
                rest += seen.read()
                *whole, rest = rest.split(b"\n")
                for line in whole:
                    lines.append(line.decode("utf-8", "replace").rstrip("\r"))
                    if on_line is not None:
                        on_line(lines[-1])

            try:
                job.add(process)
                while process.poll() is None:
                    if cancel.is_set() or time.monotonic() - began > timeout:
                        job.kill()
                        process.wait(timeout=30)
                        if cancel.is_set():
                            raise StrataError(tr("остановлено"))
                        raise StrataError(tr("Strata не закончила за {minutes} мин", minutes=round(timeout / 60)))
                    read()
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    job.kill()
                    process.wait(timeout=30)
            read()
            if rest:
                lines.append(rest.decode("utf-8", "replace"))
    finally:
        log_file.unlink(missing_ok=True)
    text = "\n".join(lines)
    if process.returncode != 0:
        raise StrataError(tr("Strata ответила ошибкой ({code}): {said}", code=process.returncode,
                             said=" ".join(text.split())[-400:]))  # fmt: skip
    return text


def run_json(base: Path, args: list[str], **kwargs: Any) -> dict[str, Any]:
    """`strata <args> --output json`: its one JSON answer (lines it says to the side are left out)."""
    text = run(base, [*args, "--output", "json"], **kwargs)
    try:
        answer = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError as exc:
        raise StrataError(tr("Strata ответила не JSON: {said}", said=" ".join(text.split())[-300:])) from exc
    if not answer.get("ok", False):
        raise StrataError(tr("Strata: {said}", said=answer.get("error") or answer.get("message") or "ok: false"))
    return answer


async def release(http: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """The pinned release's files as GitHub has them, by name - the control program and the feed with their sha256."""
    found = await engines.github(http, REPO, f"releases/tags/{TAG}")
    assets = {a["name"]: engines._asset(a) for a in found.get("assets") or []}
    for name in (CONTROL, *FEED):
        if name not in assets:
            raise StrataError(tr("в выпуске Strata {tag} нет файла {name}", tag=TAG, name=name))
        if not assets[name]["sha256"]:
            raise StrataError(tr("GitHub не дал контрольную сумму для {name}: непроверенное не ставлю", name=name))
    return assets


def installed(base: Path) -> dict[str, Any] | None:
    """What AFO's Strata is - {tag, control, engine} - when it is the pinned one and whole; None - it is not there."""
    try:
        ready = json.loads((base / READY).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return ready if ready.get("tag") == TAG and all(Path(p).is_file() for p in command(base)) else None


def install(assets: dict[str, dict[str, Any]], base: Path, *, report: Callable[[int, int], None],
            cancel: threading.Event) -> dict[str, Any]:  # fmt: skip
    """AFO's Strata put in (blocking): the control program and the feed downloaded and checked, the program unpacked,
    the engine Strata names for this computer downloaded (its sha256 - the feed's and GitHub's, the same) and put in
    by Strata from the downloads folder - its own feed. The downloads go afterwards."""
    downloads = base / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(downloads).free / 2**30
    if free < ROOM_GIB:
        raise StrataError(tr("для Strata нужно {need:g} ГБ на диске, свободно {free:.1f} ГБ", need=ROOM_GIB, free=free))
    first = [assets[n] for n in (CONTROL, *FEED)]
    total = sum(a["size"] for a in first)
    done = _download(first, downloads, 0, lambda n, extra=0: report(n, total + extra), cancel)
    _unpack_control(downloads / CONTROL, base)
    check = run_json(base, ["update", "--check", "--no-control", "--feed", str(downloads)], cancel=cancel)
    engine = check.get("engine") or {}
    offered = engine.get("offered") or {}
    if engine.get("status") != "current":
        if engine.get("problem") or not offered:
            raise StrataError(tr("Strata не нашла движок для этого компьютера: {said}",
                                 said=engine.get("problem") or tr("лента его не называет")))  # fmt: skip
        named = assets.get(str(offered.get("url") or ""))
        if named is None or named["sha256"] != offered.get("sha256"):
            raise StrataError(tr("движок Strata {name} не совпадает с выпуском {tag}: не ставлю",
                                 name=offered.get("url"), tag=TAG))  # fmt: skip
        _download([named], downloads, done, lambda n: report(n, total + named["size"]), cancel)
        answer = run_json(base, ["update", "--no-control", "--yes", "--feed", str(downloads)], cancel=cancel)
        if not any(r.get("component") == "engine" and r.get("installed_after") for r in answer.get("results") or []):
            raise StrataError(tr("Strata не поставила движок: {said}", said=json.dumps(answer)[:300]))
    ready = {"tag": TAG, "control": CONTROL_VERSION, "engine": f"{offered.get('version')}-{offered.get('variant')}"}
    (base / READY).write_text(json.dumps(ready), encoding="utf-8")
    engines.remove_tree(downloads)
    return ready


def verdict(key: str, build: str, vram_total_gib: float, ram_total_gib: float) -> str:
    """(45.2в) A model of MODELS on this computer: «fits» - its experts in the RAM less AFO's room; «slower» -
    Strata still runs it, reading the rest from the disk (its own word: «may run slower», never a refusal - 45.0);
    «no» - not a card Strata's engine is for (llama.cpp's CUDA build, CARD_MIN_GIB)."""
    if not build.startswith("cuda") or vram_total_gib < CARD_MIN_GIB:
        return "no"
    return "fits" if ram_total_gib - RAM_ROOM_GIB >= MODELS[key]["ram_gib"] else "slower"


def computer() -> tuple[str, float, float]:
    """(45.2в) This computer as `verdict` reads it: llama.cpp's build for its cards, the NVIDIA card's memory and the
    RAM, GiB."""
    cards = hardware.adapters()
    build = hardware.build_for(cards, hardware.nvidia_driver())["build"]
    vram = max((c.get("vram_gib") or 0.0 for c in cards if c.get("vendor") == "nvidia"), default=0.0)
    return build, vram, (memory_sample() or {}).get("ram_total_gib") or 0.0


def prepare(base: Path, key: str, data_dir: Path, *, report: Callable[[str, float | None, str], None],
            cancel: threading.Event, context: int = CONTEXT) -> dict[str, str]:  # fmt: skip
    """(45.2в) A model of MODELS made ready by Strata without starting it (blocking, strata_prepare.py on its
    Python): its files downloaded by Strata into `data_dir` (`models`, `mtp`, `packs` there), the draft layer, the
    pack, the run config - each step's name, share (0-100 or None) and words to `report` as they come. Not tuned:
    Strata's tuning loads the model, past the card's queue. {profile, config}: what `serve --profile` takes."""
    model = MODELS[key]
    script = Path(__file__).with_name("strata_prepare.py")
    args = ["-I", "-X", "utf8", str(script), str(terminal(base)), "--model", model["model"], "--family",
            model["family"], "--data-dir", str(data_dir), "--context", str(context), "--vision", "off", "--access",
            "local", "--port", "18080", "--tune", "skip"]  # fmt: skip
    prepared: dict[str, str] = {}
    said: list[str] = []

    def line(text: str) -> None:
        if not text.startswith("{"):
            return
        try:
            event = json.loads(text)
        except ValueError:
            return
        if "prepared" in event:
            prepared.update(event["prepared"])
        elif event.get("text"):
            said.append(event["text"])
            report(str(event.get("stage") or ""), event.get("pct"), event["text"])

    try:
        _started(base, args, cancel=cancel, timeout=PREPARE_TIMEOUT_S, on_line=line)
    except StrataError as exc:
        raise StrataError(tr("Strata не подготовила {model}: {said}", model=model["name"],
                             said=said[-1] if said else exc)) from exc  # fmt: skip
    if not prepared.get("config"):
        raise StrataError(tr("Strata не подготовила {model}: {said}", model=model["name"],
                             said=said[-1] if said else tr("нет ответа")))  # fmt: skip
    return prepared


def serve_env(base: Path) -> list[str]:
    """(45.2в) What llama-swap gives a model of AFO's Strata (its `env:`): every folder of Strata's own and a PATH
    without the person's Strata - `serve` heals a strata.ps1 on PATH too (45.0) - and no Python of the hub's."""
    own = environment(base)
    keys = ("USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", *sorted(k for k in own if k.startswith("STRATA_")),
            "PYTHONUTF8", "PYTHONNOUSERSITE")  # fmt: skip
    path = next((v for k, v in own.items() if k.upper() == "PATH"), "")
    return [f"{k}={own[k]}" for k in keys] + [f"PATH={path}", "PYTHONHOME=", "PYTHONPATH=", "VIRTUAL_ENV="]


def forget(base: Path, prepared: dict[str, str]) -> None:
    """(45.2в) A removed model's run config, its engine's log and its profile out of AFO's Strata; its files went
    with the install's folder. Only what lies inside `base`."""
    config = Path(prepared.get("config") or "")
    places = [config, config.with_name(config.stem + ".engine.log")] if config.name else []
    if prepared.get("profile"):
        places.append(data(base) / "profiles" / f"{prepared['profile']}.json")
    for place in places:
        if base.resolve() in place.resolve().parents:
            place.unlink(missing_ok=True)


def _download(files: list[dict[str, Any]], downloads: Path, base_bytes: int, report: Callable[..., None],
              cancel: threading.Event) -> int:  # fmt: skip
    done = base_bytes
    for asset in files:
        dest = downloads / asset["name"]
        hf.download(asset["url"], dest, size=asset["size"], sha256=asset["sha256"], token=None,
                    report=lambda n, b=done: report(b + n), cancel=cancel)  # fmt: skip
        done += asset["size"]
    return done


def _unpack_control(archive: Path, base: Path) -> None:
    """The control package into its folder - a new one each time: the old one goes first (an older program's files
    would stay beside the new ones). Its root is the bundle itself (strata.py, bin/strata.cmd)."""
    folder = terminal(base)
    incoming = folder.with_name(folder.name + ".incoming")
    engines.remove_tree(incoming)
    engines.unpack(archive, incoming)
    if not ((incoming / "strata.py").is_file() and (incoming / "bin" / "strata.cmd").is_file()):
        engines.remove_tree(incoming)
        raise StrataError(tr("в {archive} нет strata.py и bin\\strata.cmd", archive=archive.name))
    engines.remove_tree(folder)
    shutil.move(str(incoming), str(folder))
