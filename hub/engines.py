"""The engines local models run on (stage 10.2, D37, Q19): a llama.cpp build and llama-swap, from their GitHub
releases, for a computer that has neither yet.

- llama.cpp publishes a build per commit, tagged `b<number>` and marked as a pre-release (`/releases/latest` is not
  one of them): the newest `b…` release with the Windows archive for this computer is taken - CUDA 13 or 12 for
  NVIDIA (with the CUDA runtime archive beside it), Vulkan for AMD and Intel, the processor's otherwise.
- llama-swap: its latest release's Windows archive.
- Each archive is checked against the sha256 GitHub publishes for it (`digest`), unpacked into a folder of its own
  (`<engines>/llama.cpp/b<number>-<build>`, `<engines>/llama-swap`), and llama-swap gets a config of its own that
  names llama-server in a macro - the installer (10.1) adds the models.
- stable-diffusion.cpp (pictures and videos, D55, 29.3): its sd-server, from the one release AFO was checked on - it
  publishes a build per commit, several a day, and some are broken (29.1); CUDA 12 for NVIDIA with its runtime
  beside it, Vulkan, the processor's. Into `<engines>/stable-diffusion.cpp/<tag>-<build>`.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from common.i18n import tr

from . import hf

GITHUB_ENV = "LOCALCHAT_GITHUB_API"  # tests point it at a stand-in
GITHUB_API = "https://api.github.com"


def github_api() -> str:
    return hf.safe_base(os.environ.get(GITHUB_ENV), GITHUB_API)


LLAMA_CPP = "ggml-org/llama.cpp"
LLAMA_SWAP = "mostlygeek/llama-swap"
ASSET = re.compile(r"^llama-(b\d+)-bin-win-(cuda-(\d+)\.(\d+)|vulkan|cpu)-x64\.zip$")
SD_CPP = "leejet/stable-diffusion.cpp"
SD_TAG = "master-1053-5a1ba5d"  # checked on 2026-11-07 (29.1, docs/visual.md §2a); a newer one after the same trials
SD_RUNTIME = "cudart-sd-bin-win-cu12-x64.zip"
CONFIG = """# llama-swap: one OpenAI-compatible address (http://127.0.0.1:8080/v1) for every local model.
# Written by AFO's first-run wizard; models are added by {installer}. The model asked for is started,
# the one running before it stopped (one graphics card).

healthCheckTimeout: 600      # a large model's first load from disk can take minutes
startPort: 10001
globalTTL: 0                 # a model stays loaded until another one is asked for
logLevel: info

macros:
  llama_server: '"{server}"'

models:
"""


class EngineError(Exception):
    """A build cannot be found, fetched or unpacked; the message is for the window."""


async def github(http: httpx.AsyncClient, repo: str, path: str = "releases?per_page=30", *,
                 missing_ok: bool = False) -> Any:  # fmt: skip
    """GitHub's answer about a repository (its releases); `missing_ok` - None when there is none (404)."""
    try:
        wanted = {"Accept": "application/vnd.github+json", **hf.headers()}
        response = await http.get(f"{github_api()}/repos/{repo}/{path}", headers=wanted)
    except httpx.HTTPError as exc:
        raise EngineError(tr("нет связи с GitHub ({error})", error=exc.__class__.__name__)) from exc
    if response.status_code == 404 and missing_ok:
        return None
    if response.status_code != 200:
        if response.status_code == 403:
            raise EngineError(tr("GitHub ответил {status} — слишком много запросов, попробуйте через час",
                                 status=response.status_code))  # fmt: skip
        raise EngineError(tr("GitHub ответил {status}", status=response.status_code))
    return response.json()


def _asset(asset: dict[str, Any]) -> dict[str, Any]:
    digest = str(asset.get("digest") or "")
    return {"name": asset["name"], "url": asset["browser_download_url"], "size": int(asset.get("size") or 0),
            "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None}  # fmt: skip


def pick_llama_cpp(releases: list[dict[str, Any]], build: str) -> dict[str, Any] | None:
    """The newest `b…` release with this build's Windows archive: {tag, build, assets: [the archive, the CUDA
    runtime]}. `build`: cuda-13 | cuda-12 | vulkan | cpu."""
    for release in releases:
        tag = release.get("tag_name") or ""
        if not re.fullmatch(r"b\d+", tag) or release.get("draft"):
            continue
        found = []
        for asset in release.get("assets") or []:
            match = ASSET.match(asset["name"])
            if not match or match.group(1) != tag:
                continue
            kind = match.group(2)
            if build.startswith("cuda-") and match.group(3) == build.removeprefix("cuda-"):
                found.append((int(match.group(4)), asset))
            elif kind == build:
                found.append((0, asset))
        if not found:
            continue
        minor, archive = max(found, key=lambda f: f[0])
        assets = [_asset(archive)]
        if build.startswith("cuda-"):
            runtime_name = f"cudart-llama-bin-win-cuda-{build.removeprefix('cuda-')}.{minor}-x64.zip"
            runtime = next((a for a in release.get("assets") or [] if a["name"] == runtime_name), None)
            if runtime is None:
                continue
            assets.append(_asset(runtime))
        return {"tag": tag, "build": build, "assets": assets}
    return None


def pick_llama_swap(release: dict[str, Any]) -> dict[str, Any] | None:
    asset = next((a for a in release.get("assets") or [] if re.fullmatch(r"llama-swap_[\w.]+_windows_amd64\.zip",
                                                                         a["name"])), None)  # fmt: skip
    return {"tag": release.get("tag_name") or "", "assets": [_asset(asset)]} if asset else None


def pick_sd_cpp(release: dict[str, Any], build: str) -> dict[str, Any] | None:
    """The pinned release's Windows archive for this build (cuda12 | vulkan | cpu) and, for CUDA, its runtime:
    {tag, build, assets}; None when the release is another or lacks them."""
    if release.get("tag_name") != SD_TAG:
        return None
    named = {a["name"]: a for a in release.get("assets") or []}
    archive = named.get(f"sd-master-{SD_TAG.rsplit('-', 1)[1]}-bin-win-{build}-x64.zip")
    runtime = named.get(SD_RUNTIME) if build == "cuda12" else None
    if archive is None or (build == "cuda12" and runtime is None):
        return None
    return {"tag": SD_TAG, "build": build, "assets": [_asset(a) for a in (archive, runtime) if a]}


async def sd_cpp_release(http: httpx.AsyncClient, build: str) -> dict[str, Any]:
    found = pick_sd_cpp(await github(http, SD_CPP, f"releases/tags/{SD_TAG}"), build)
    if found is None:
        raise EngineError(tr("в выпуске stable-diffusion.cpp {tag} нет сборки {build} для Windows", tag=SD_TAG,
                             build=build))  # fmt: skip
    return found


def sd_server_in(folder: Path) -> Path:
    """sd-server.exe in an unpacked stable-diffusion.cpp build."""
    found = next(iter(sorted(folder.rglob("sd-server.exe"), key=lambda p: len(p.parts))), None)
    if found is None:
        raise EngineError(tr("в {folder} нет sd-server.exe", folder=folder))
    return found


async def llama_cpp_release(http: httpx.AsyncClient, build: str) -> dict[str, Any]:
    found = pick_llama_cpp(await github(http, LLAMA_CPP), build)
    if found is None:
        raise EngineError(tr("в последних выпусках llama.cpp нет сборки {build} для Windows", build=build))
    return found


async def llama_swap_release(http: httpx.AsyncClient) -> dict[str, Any]:
    found = pick_llama_swap(await github(http, LLAMA_SWAP, "releases/latest"))
    if found is None:
        raise EngineError(tr("в последнем выпуске llama-swap нет архива для Windows"))
    return found


def unpack(archive: Path, folder: Path) -> list[str]:
    """The archive's files into the folder (blocking); a name that would leave the folder is refused."""
    folder.mkdir(parents=True, exist_ok=True)
    root = folder.resolve()
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            target = (folder / name).resolve()
            if not target.is_relative_to(root):
                raise EngineError(tr("в архиве {archive} файл вне папки: {name}", archive=archive.name, name=name))
        zf.extractall(folder)
        return zf.namelist()


def fetch(assets: list[dict[str, Any]], downloads: Path, folder: Path, *, report: Callable[[int, int], None],
          cancel: threading.Event) -> None:  # fmt: skip
    """Each archive downloaded (sha256 checked - an archive GitHub gives none for is refused) and unpacked into
    `folder` (blocking: in a thread)."""
    missing = [a["name"] for a in assets if not a.get("sha256")]
    if missing:  # without it a changed or broken file would be run (D46): as ripgrep, nothing is put in
        raise EngineError(tr("GitHub не дал контрольную сумму для {files}: без неё программа не ставится — "
                             "попробуйте позже или выберите другую сборку", files=", ".join(missing)))  # fmt: skip
    total = sum(a["size"] for a in assets)
    base = 0
    downloads.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        dest = downloads / asset["name"]
        if not (dest.exists() and dest.stat().st_size == asset["size"]):
            hf.download(asset["url"], dest, size=asset["size"], sha256=asset["sha256"], token=None,
                        report=lambda n, b=base: report(b + n, total), cancel=cancel)  # fmt: skip
        base += asset["size"]
        unpack(dest, folder)


def running_build(server: Path) -> dict[str, str] | None:
    """Which llama.cpp build a llama-server is - its tag (b<number>, from its folder) and kind (by the libraries
    beside it: CUDA 13 or 12, Vulkan, the processor's); None when it is not there."""
    if not server.is_file():
        return None
    tag = next((m.group(1) for part in reversed(server.parent.parts) if (m := re.match(r"^(b\d+)", part))), "")
    names = {p.name.lower() for p in server.parent.iterdir()}
    if any(n.startswith("ggml-cuda") for n in names):
        build = "cuda-12" if any(re.match(r"(cudart|cublas)64_12", n) for n in names) else "cuda-13"
    elif any(n.startswith("ggml-vulkan") for n in names):
        build = "vulkan"
    else:
        build = "cpu"
    return {"tag": tag, "build": build}


def newer(latest: str, current: str) -> bool:
    numbers = [int(t[1:]) if re.fullmatch(r"b\d+", t or "") else -1 for t in (latest, current)]
    return numbers[0] > numbers[1] >= 0


def server_in(folder: Path) -> Path:
    """llama-server.exe in an unpacked build (at its top or one folder down)."""
    found = next(iter(sorted(folder.rglob("llama-server.exe"), key=lambda p: len(p.parts))), None)
    if found is None:
        raise EngineError(tr("в {folder} нет llama-server.exe", folder=folder))
    return found


def write_config(path: Path, server: Path) -> None:
    """llama-swap's first config: no models yet, llama-server in the macro the installer looks for. An existing
    config is never overwritten."""
    if path.exists():
        raise EngineError(tr("{path} уже есть — его не трогаю", path=path))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = CONFIG.format(server=str(server).replace("\\", "/"), installer=tr("«Установка моделей»"))
    path.write_bytes(text.encode("utf-8"))


def remove_tree(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
