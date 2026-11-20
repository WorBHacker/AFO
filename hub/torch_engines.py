"""Engines on PyTorch (stage 56): programs on Python that AFO fetches when the person asks - each in an environment
of its own, all on one Python and one PyTorch, all in AFO's folder (<engines>/torch). Separate programs AFO runs,
never imported: the hub has no PyTorch.

What the probe found (56.1, TechDoc «Этап 56»):
- uv (astral-sh/uv) makes the environments, with its own Python (python-build-standalone) - not the person's. Each
  call is given its cache and its Pythons' folder (UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR), `python install` gets
  `--no-bin --no-registry` (else uv puts python3.10.exe in %USERPROFILE%\\.local\\bin and writes the registry), and it
  runs in this folder with `--no-project` (else it takes a pyproject.toml found above it for its project). The
  person's own uv settings (UV_*, uv.toml) are not read.
- PyTorch 2.8.0 + cu128: the RTX 50s need 2.7 + cu128 at least. Its wheels are asked by their URL with their sha256
  (uv checks it): uv keeps what it unpacked in its cache by that URL, and an environment links its files from there -
  a second engine on the same PyTorch costs only its own files (56.1: 0.01 GiB more).
- An engine's caches (Hugging Face, PyTorch's hub, matplotlib) lie in its own folder and it runs offline: else
  transformers writes into the person's profile (56.1).
- What AFO starts here runs in a Windows job: whatever it starts goes with it - at «Стоп», and when the hub stops.

uv's archive and the weights come by AFO's own download (sha256, «Стоп» goes on from where it was), each byte told to
the window; what uv fetches (Python, PyTorch, the libraries, the engine's code) is told by how much the disk took.

The engines (`ENGINES`):
- «Выделение предмета» (`select`): SAM 2.1 large (facebookresearch/sam2, Apache-2.0, code and weights) - an object
  followed through a video from one point (hub/torch_scripts/select.py). Its code - the archive of its commit,
  built without its CUDA extension (`SAM2_BUILD_CUDA=0`: only a mask's small holes are not filled; no compiler), its
  weights from Hugging Face by revision.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.i18n import N_, tr
from common.paths import engines_home

from . import engines, hf, winjob
from .engines import EngineError

SOURCES_ENV = "LOCALCHAT_ENGINE_FILES"  # tests point it at a stand-in: <it>/<host>/<path>
UV = {"url": "https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-pc-windows-msvc.zip",
      "name": "uv-x86_64-pc-windows-msvc.zip", "size": 15567875,
      "sha256": "edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb"}  # fmt: skip
PYTHON = "3.10.22"  # python-build-standalone, as uv 0.13.9 has it (56.1)
PYTHON_SIZE = 22020096  # what uv fetches of it (56.1: 21.0 MiB)
TORCH = (
    {"name": "torch", "size": 3461427674,
     "url": "https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp310-cp310-win_amd64.whl",
     "sha256": "43938e9a174c90e5eb9e906532b2f1e21532bbfa5a61b65193b4f54714d34f9e"},
    {"name": "torchvision", "size": 7540139,
     "url": "https://download-r2.pytorch.org/whl/cu128/torchvision-0.23.0%2Bcu128-cp310-cp310-win_amd64.whl",
     "sha256": "8ec6f2281ef5d52471b01b99eb04243d0c2cccb1972ba43217085025fe5a6c3f"},
)  # fmt: skip
# what PyTorch takes, as 56.1 had it - the same on every computer; setuptools and wheel: an engine's code is built here
TORCH_PINS = ("filelock==4.1.0", "fsspec==2026.9.0", "jinja2==3.1.6", "markupsafe==3.0.3", "mpmath==1.3.0",
              "networkx==3.4.2", "numpy==2.2.6", "pillow==12.3.0", "sympy==1.14.0", "typing-extensions==4.16.0",
              "setuptools==84.2.1", "wheel==0.48.0")  # fmt: skip
LIBRARIES_SIZE = 40 << 20  # ≈ what those libraries and an engine's own come to from PyPI (56.1)
TORCH_DISK = int(7.05 * 2**30)  # an environment with PyTorch on the disk (56.1: 7.01 GiB) - once, in the uv cache
BASE_DISK = 100 << 20  # uv and its Python (56.1: 0.03 + 0.06 GiB)
SCRIPTS = Path(__file__).with_name("torch_scripts")  # what an engine runs: AFO's own, in the engine's Python
ENGINES: dict[str, dict[str, Any]] = {
    "select": {
        "name": N_("Выделение предмета"),
        "about": N_("SAM 2.1: предмет на всём ролике по одному щелчку — видеокарта NVIDIA от 8 ГБ"),
        "licence": "Apache-2.0", "card_gib": 8.0, "script": "select.py",
        "code": {"package": "sam-2", "size": 55952852, "disk": 250 << 20,
                 "url": "https://github.com/facebookresearch/sam2/archive/2b90b9f5ceec907a1c18123530e92e794ad901a4.zip",
                 "sha256": "fe93082a71a885a427894b1eab76341768781b6b58a298a0717e03862097d137"},
        "pins": ("antlr4-python3-runtime==4.9.3", "colorama==0.4.6", "hydra-core==1.3.8", "iopath==0.1.10",
                 "omegaconf==2.3.1", "packaging==26.3", "portalocker==4.4.2", "pyyaml==6.0.3", "tqdm==4.70.1"),
        "build": {"SAM2_BUILD_CUDA": "0", "SAM2_BUILD_ALLOW_ERRORS": "1"},
        "weights": ({"hf": "facebook/sam2.1-hiera-large", "revision": "665f8e2ad61cf5f53d65644ff27c8ee525124610",
                     "path": "sam2.1_hiera_large.pt", "size": 898083611,
                     "sha256": "2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318"},),
    },
}  # fmt: skip
UV_TIMEOUT_S = 3600.0  # PyTorch's 3.5 GB over a slow line
CHECK_TIMEOUT_S = 600.0  # the first import of PyTorch reads its 7 GB
RUN_TIMEOUT_S = 3600.0
JOB_MEMORY = 1 << 40  # the job is for «Стоп», not for a limit: an engine takes what its video needs
OUT_OF_CARD = ("CUDA out of memory", "OutOfMemoryError")  # PyTorch's words when the card is too small
LOOK_S = 0.5  # how often what the disk took is told while uv works
SCRATCH = "tmp"  # (56.6) where uv's and an engine's temporary files go - a folder a run, gone after it
DROPPED = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX", "PIP_REQUIRE_VIRTUALENV"}


class Stopped(EngineError):
    """«Стоп»: the program was killed, with all it started."""


def home() -> Path:
    """Where the engines on PyTorch lie: <engines>/torch - AFO's own for the card's quiet (hub/gpu_prefs.py)."""
    return engines_home() / "torch"


def source(url: str) -> str:
    """Where a pinned file is fetched from: its own address, or the tests' stand-in's (`SOURCES_ENV`)."""
    stand_in = os.environ.get(SOURCES_ENV)
    return f"{stand_in.rstrip('/')}/{url.split('://', 1)[1]}" if stand_in else url


def uv_exe(base: Path) -> Path:
    return base / "uv" / "uv.exe"


def python_here(base: Path) -> bool:
    return any((found / "python.exe").is_file() for found in (base / "python").glob(f"cpython-{PYTHON}-*"))


def engine_dir(base: Path, engine: str) -> Path:
    return base / engine


def python_of(base: Path, engine: str) -> Path:
    return engine_dir(base, engine) / "env" / "Scripts" / "python.exe"


def home_of(base: Path, engine: str) -> Path:
    """The engine's own folder for its caches - and where it runs."""
    return engine_dir(base, engine) / "home"


def weight_file(models: Path, engine: str, spec: dict[str, Any]) -> Path:
    return models / engine / spec["path"].rsplit("/", 1)[-1]


def script_of(engine: str) -> Path:
    """What the engine runs; a stand-in of the tests is given its own."""
    return SCRIPTS / ENGINES[engine]["script"]


def fingerprint(engine: str) -> dict[str, Any]:
    """What an engine was put in with: another one in AFO's code - it is put in anew."""
    spec = ENGINES[engine]
    return {"python": PYTHON, "torch": [t["sha256"] for t in TORCH], "code": spec["code"]["sha256"],
            "pins": [*TORCH_PINS, *spec["pins"]], "weights": [w["sha256"] for w in spec["weights"]]}  # fmt: skip


def torch_here(base: Path) -> bool:
    """PyTorch is in the uv cache already: an engine put in with it before."""
    try:
        return json.loads((base / "torch.json").read_text(encoding="utf-8")) == [t["sha256"] for t in TORCH]
    except OSError, ValueError:
        return False


def has(models: Path, engine: str) -> bool:
    """The engine here, put in by this AFO's pins, and its weights whole."""
    base = home()
    try:
        ready = json.loads((engine_dir(base, engine) / "ready.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    return (ready.get("fingerprint") == fingerprint(engine) and python_of(base, engine).is_file()
            and all(_whole(models, engine, w) for w in ENGINES[engine]["weights"]))  # fmt: skip


def _whole(models: Path | None, engine: str, spec: dict[str, Any]) -> bool:
    """A weights file here at its size: not fetched again."""
    if models is None:
        return False
    path = weight_file(models, engine, spec)
    return path.is_file() and path.stat().st_size == spec["size"]


def size(engine: str, models: Path | None = None) -> int:
    """What «Скачать» brings, in bytes: what is not here yet of uv, its Python and PyTorch, the libraries, the
    engine's code and weights (56.6: weights already whole in `models` are not fetched - not counted)."""
    base, spec = home(), ENGINES[engine]
    shared = (0 if uv_exe(base).is_file() else UV["size"]) + (0 if python_here(base) else PYTHON_SIZE)
    shared += 0 if torch_here(base) else sum(t["size"] for t in TORCH)
    weights = sum(w["size"] for w in spec["weights"] if not _whole(models, engine, w))
    return shared + LIBRARIES_SIZE + spec["code"]["size"] + weights


def disk(engine: str) -> int:
    """What it takes on the disk once in: put in - as measured then (56.6: the window said «установлено · 98 МБ»
    of 8.6 GB); not yet - PyTorch unpacked is twice its download (56.1)."""
    base, spec = home(), ENGINES[engine]
    with contextlib.suppress(OSError, ValueError, TypeError):
        ready = json.loads((engine_dir(base, engine) / "ready.json").read_text(encoding="utf-8"))
        if ready.get("fingerprint") == fingerprint(engine) and ready.get("disk"):
            return int(ready["disk"])
    shared = (0 if python_here(base) else BASE_DISK) + (0 if torch_here(base) else TORCH_DISK)
    return shared + spec["code"]["disk"] + sum(w["size"] for w in spec["weights"])


def measured(base: Path, engine: str) -> int:
    """What an engine put in takes on the disk: the folder of the engines on PyTorch - uv, its Python, its cache, the
    environments; a file uv linked in several places once - and the engine's weights."""
    seen, total = set(), 0
    for path in base.rglob("*"):
        with contextlib.suppress(OSError):
            found = path.lstat()
            if stat.S_ISREG(found.st_mode) and (found.st_dev, found.st_ino) not in seen:
                seen.add((found.st_dev, found.st_ino))
                total += found.st_size
    return total + sum(w["size"] for w in ENGINES[engine]["weights"])


def usable(engine: str, machine: dict[str, Any]) -> str:
    """Why the engine cannot work on this computer, in words; '' - it can. PyTorch here is CUDA's: an NVIDIA card."""
    need = ENGINES[engine]["card_gib"]
    if machine.get("build") != "cuda12" or float(machine.get("memory_gib") or 0) < need:
        return tr("нужна видеокарта NVIDIA от {gib} ГБ", gib=round(need))
    return ""


# --- the programs ---


def command(program: Path) -> list[str]:
    """How uv is started; a stand-in of the tests is started its own way."""
    return [str(program)]


def _env(add: dict[str, str]) -> dict[str, str]:
    """The hub's environment without what would make a program take another Python or uv's other settings."""
    kept = {k: v for k, v in os.environ.items() if k.upper() not in DROPPED and not k.upper().startswith("UV_")}
    return kept | add


def uv_env(base: Path, add: dict[str, str] | None = None) -> dict[str, str]:
    return _env({"UV_CACHE_DIR": str(base / "cache"), "UV_PYTHON_INSTALL_DIR": str(base / "python"),
                 "UV_NO_CONFIG": "1", "UV_PYTHON_PREFERENCE": "only-managed", "UV_PYTHON_DOWNLOADS": "manual",
                 "UV_LINK_MODE": "hardlink", "UV_NO_PROGRESS": "1", **(add or {})})  # fmt: skip


def engine_env(base: Path, engine: str) -> dict[str, str]:
    """An engine's run: its caches in its folder, nothing fetched (its weights lie in AFO's), UTF-8."""
    place = home_of(base, engine)
    return _env({"HF_HOME": str(place / "hf"), "TORCH_HOME": str(place / "torch"), "XDG_CACHE_HOME": str(place),
                 "MPLCONFIGDIR": str(place / "matplotlib"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                 "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})  # fmt: skip


def _tail(text: str) -> str:
    return " ".join(text.split())[-300:]


def _started(args: list[str], *, env: dict[str, str], cwd: Path, cancel: threading.Event, timeout: float,
             failed: str, name: str, short: tuple[str, ...] = (), scratch: Path) -> str:  # fmt: skip
    """A program once (blocking), in a Windows job: what it said. `cancel` set, or the time out - the job is killed,
    with everything the program started. It failed - `failed` with its words ({name}, {said}); the card too small
    (`short`) - said so. Its temporary files (TEMP, TMP) - in a folder of its own under `scratch`, in AFO's folder,
    gone after it (56.6: SAM's frames of a run killed by ✕ stayed in the person's TEMP)."""
    scratch.mkdir(parents=True, exist_ok=True)
    own = Path(tempfile.mkdtemp(dir=scratch))
    try:
        return _once(args, env=env | {"TEMP": str(own), "TMP": str(own), "TMPDIR": str(own)}, cwd=cwd, cancel=cancel,
                     timeout=timeout, failed=failed, name=name, short=short)  # fmt: skip
    finally:
        engines.remove_tree(own)


def _once(args: list[str], *, env: dict[str, str], cwd: Path, cancel: threading.Event, timeout: float,
          failed: str, name: str, short: tuple[str, ...]) -> str:  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    began = time.monotonic()
    with tempfile.TemporaryFile() as said, winjob.Job(JOB_MEMORY) as job:  # a file, not a pipe: nothing reads one
        try:
            process = subprocess.Popen(args, stdout=said, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       cwd=str(cwd), env=env, creationflags=flags)  # fmt: skip
        except OSError as exc:
            raise EngineError(tr(failed, name=name, said=exc)) from exc
        try:
            job.add(process)
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() - began > timeout:
                    job.kill()
                    process.wait(timeout=30)
                    if cancel.is_set():
                        raise Stopped(tr("остановлено"))
                    raise EngineError(tr("«{name}» не закончило за {minutes} мин", name=name,
                                         minutes=round(timeout / 60)))  # fmt: skip
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                job.kill()
                process.wait(timeout=30)
        said.seek(0)
        text = said.read().decode("utf-8", "replace")
    if process.returncode != 0:
        if any(word in text for word in short):
            raise EngineError(tr("не хватило памяти видеокарты — закройте программы, которые её занимают, или "
                                 "возьмите ролик короче или меньше"))  # fmt: skip
        raise EngineError(tr(failed, name=name, said=_tail(text) or process.returncode))
    return text


def _uv(base: Path, args: list[str], cancel: threading.Event, *, add: dict[str, str] | None = None,
        told: Callable[[float], None] | None = None, takes: int = 0) -> str:  # fmt: skip
    """uv once, in its folder, with its folders; how far it is - what the disk took of the `takes` bytes it will -
    told meanwhile."""
    done = threading.Event()

    def look() -> None:
        before = shutil.disk_usage(base).free
        while not done.wait(LOOK_S):
            with contextlib.suppress(OSError):
                told(min(max(before - shutil.disk_usage(base).free, 0) / takes, 0.99))  # type: ignore[misc]

    watcher = threading.Thread(target=look, daemon=True) if told is not None and takes else None
    if watcher is not None:
        watcher.start()
    try:
        return _started([*command(uv_exe(base)), *args], env=uv_env(base, add), cwd=base, cancel=cancel,
                        timeout=UV_TIMEOUT_S, failed=N_("uv не поставил «{name}»: {said}"), name=args[0],
                        scratch=base / SCRATCH)  # fmt: skip
    finally:
        done.set()
        if watcher is not None:
            watcher.join()


def _fetch_uv(base: Path, cancel: threading.Event, report: Callable[[int], None]) -> None:
    """uv's archive (sha256 checked), of it only uv.exe - put in whole."""
    downloads = base / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / UV["name"]
    hf.download(source(UV["url"]), archive, size=UV["size"], sha256=UV["sha256"], token=None, cancel=cancel,
                report=report)  # fmt: skip
    part = base / "uv.part"
    engines.remove_tree(part)
    part.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        name = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "uv.exe"), None)
        if name is None:
            raise EngineError(tr("в архиве {archive} нет uv.exe", archive=UV["name"]))
        (part / "uv.exe").write_bytes(zf.read(name))
    engines.remove_tree(base / "uv")
    part.replace(base / "uv")


def install(engine: str, models: Path, cancel: threading.Event, report: Callable[[int, int], None]) -> None:
    """The engine put in (blocking): uv and its Python once, the engine's environment - PyTorch, the libraries by
    their pins, its code -, its weights, then a look that it starts and sees the card. «Стоп» (hf.Cancelled): what
    came stays for the next time; the environment is made anew then - from uv's cache, without fetching again."""
    base, spec = home(), ENGINES[engine]
    total, got = size(engine, models), 0
    base.mkdir(parents=True, exist_ok=True)

    def at(n: float) -> None:
        report(min(got + int(n), total), total)

    try:
        if not uv_exe(base).is_file():
            _fetch_uv(base, cancel, at)
            got += UV["size"]
        if not python_here(base):
            _uv(base, ["python", "install", PYTHON, "--no-bin", "--no-registry"], cancel,
                told=lambda share: at(share * PYTHON_SIZE), takes=BASE_DISK)  # fmt: skip
            got += PYTHON_SIZE
        place, py = engine_dir(base, engine), python_of(base, engine)
        (place / "ready.json").unlink(missing_ok=True)
        engines.remove_tree(place / "env")
        place.mkdir(parents=True, exist_ok=True)
        wanted = [f"{t['name']} @ {source(t['url'])}#sha256={t['sha256']}" for t in TORCH]
        (place / "requirements.txt").write_bytes(("\n".join([*wanted, *TORCH_PINS[-2:]]) + "\n").encode())
        (place / "pins.txt").write_bytes(("\n".join([*TORCH_PINS, *spec["pins"]]) + "\n").encode())
        _uv(base, ["venv", str(place / "env"), "--python", PYTHON, "--no-project"], cancel)
        shared = torch_here(base)
        bring = (0 if shared else sum(t["size"] for t in TORCH)) + LIBRARIES_SIZE // 2
        _uv(base, ["pip", "install", "--python", str(py), "-r", str(place / "requirements.txt"), "-c",
                   str(place / "pins.txt")], cancel, told=lambda share: at(share * bring),
            takes=(0 if shared else TORCH_DISK) + (LIBRARIES_SIZE // 2) * 3)  # fmt: skip
        got += bring
        (base / "torch.json").write_bytes(json.dumps([t["sha256"] for t in TORCH]).encode())
        code = spec["code"]
        bring = code["size"] + LIBRARIES_SIZE - LIBRARIES_SIZE // 2
        _uv(base, ["pip", "install", "--python", str(py), "--no-build-isolation", "-c", str(place / "pins.txt"),
                   f"{code['package']} @ {source(code['url'])}#sha256={code['sha256']}"], cancel, add=spec["build"],
            told=lambda share: at(share * bring), takes=code["disk"])  # fmt: skip
        got += bring
        for weight in spec["weights"]:
            if not _whole(models, engine, weight):  # whole - not fetched, not counted in `total`
                path = weight_file(models, engine, weight)
                path.parent.mkdir(parents=True, exist_ok=True)
                hf.download(hf.file_url(weight["hf"], weight["path"], weight["revision"]), path, size=weight["size"],
                            sha256=weight["sha256"], token=None, cancel=cancel, report=at)  # fmt: skip
                got += weight["size"]
        home_of(base, engine).mkdir(parents=True, exist_ok=True)
        looked = _started([str(py), "-I", str(script_of(engine)), "--check"], env=engine_env(base, engine),
                          cwd=home_of(base, engine), cancel=cancel, timeout=CHECK_TIMEOUT_S,
                          failed=N_("«{name}» не запустилось: {said}"), name=tr(spec["name"]),
                          scratch=engine_dir(base, engine) / SCRATCH)  # fmt: skip
        engines.remove_tree(base / "downloads")
        (place / "ready.json").write_bytes(json.dumps({"fingerprint": fingerprint(engine), "check": _tail(looked),
                                                       "disk": measured(base, engine)}).encode())  # fmt: skip
        report(total, total)
    except Stopped:
        raise hf.Cancelled() from None


def remove(models: Path, engine: str) -> None:
    """An engine gone from the disk: its environment and weights; the last one takes uv, its Python and the cache
    with it - nothing of the engines on PyTorch is left."""
    base = home()
    engines.remove_tree(engine_dir(base, engine))
    for weight in ENGINES[engine]["weights"]:
        with contextlib.suppress(OSError):
            weight_file(models, engine, weight).unlink(missing_ok=True)
    if not any((engine_dir(base, other) / "env").exists() for other in ENGINES):
        engines.remove_tree(base)


def run(engine: str, args: list[str], cancel: threading.Event, *, timeout: float = RUN_TIMEOUT_S) -> str:
    """The engine once at a piece of work (blocking), with its Python, in its folder: what it said."""
    base = home()
    home_of(base, engine).mkdir(parents=True, exist_ok=True)
    return _started([str(python_of(base, engine)), "-I", str(script_of(engine)), *args],
                    env=engine_env(base, engine), cwd=home_of(base, engine), cancel=cancel, timeout=timeout,
                    failed=N_("«{name}» не справилось: {said}"), name=tr(ENGINES[engine]["name"]),
                    short=OUT_OF_CARD, scratch=engine_dir(base, engine) / SCRATCH)  # fmt: skip


def select_args(models: Path, frames: Path, out: Path, *, at: int, x: float, y: float) -> list[str]:
    """SAM 2.1: the object at (x, y) - shares of the frame's width and height - of frame `at` (from 0) followed
    through the frames of `frames` (FRAME_NAME) into `out` by the same names: it as it is, the rest darkened."""
    weights = weight_file(models, "select", ENGINES["select"]["weights"][0])
    return ["--weights", str(weights), "--frames", str(frames), "--out", str(out), "--at", str(at), "--x",
            f"{x:.4f}", "--y", f"{y:.4f}"]  # fmt: skip
