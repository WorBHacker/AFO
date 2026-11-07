"""Installing local models (stage 10.1, D37): a model from Hugging Face into llama-swap, one button for the person.

- What to offer: the curated list (model_catalog.json, Q15) first, a search of all Hugging Face as the wider way.
  Each variant of a model is judged against this computer before anything is downloaded - whether it fits in the
  video memory (fast), goes partly into RAM (slower) or does not fit - from its size and its header's attention
  shape (the file's first bytes over the network); the one recommended is the best that fits the video memory.
- Where: the `model` folder in AFO's own folder by default (2026-10-28, the user's word; common/paths.py
  models_home), or a drive the person picks - `X:\\AFO\\models` (on the Windows drive `%LOCALAPPDATA%\\AFO\\models`;
  LocalChat before 10.4b) -, or a folder of their own; a folder per repository.
- The download: one install at a time, resumable (a cut connection is tried again, and a part left by a stopped hub
  goes on after «Продолжить»), each file kept only when its sha256 matches what Hugging Face publishes.
- The config: a block of its own at the end of llama-swap's `models:` (a copy of the file first, llama-swap must list
  every model again - or the copy comes back), with launch settings from the downloaded file's header: the largest
  context that fits the video memory with an 8-bit KV cache, else llama.cpp's --fit with a timer to unload.
- The trial: the model loads and answers one question (the «Проверить» of 8.1); when it does not start, the
  settings get smaller (half the context, then --fit) and it is tried again; still no start - its block goes, the
  files stay for «Повторить» or removal.
- Removal: the model unloaded, its block out of the config, its files deleted - only what this installer put there.
- Picture models (D55, 29.3): the catalog's `pictures`, each a set of files from several repositories (diffusion
  model, text encoder, VAE), for stable-diffusion.cpp's sd-server. The engine comes with the first one - its pinned
  build for this computer (hub/engines.py), downloaded as part of the install and named in the config's `sd_server`
  macro; the trial is one small picture through llama-swap, and a blank one does not count (hub/visual.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from common import disks, hardware
from common.gguf import GGUFError, model_info
from common.i18n import N_, tr
from common.llama_memory import COMPUTE_GIB, GIB, RESERVE_GIB, kv_bytes
from common.paths import NAME, engines_home, models_home

from . import engines, hf, llama_config, store, visual
from .db import utc_now
from .model_scan import SPLIT
from .system_status import memory_sample

log = logging.getLogger("hub.install")

CATALOG = Path(__file__).with_name("model_catalog.json")
PURPOSES = {"chat": N_("Разговоры"), "code": N_("Код"), "vision": N_("Понимать картинки"), "image": N_("Рисовать")}
SD_MACRO = "sd_server"  # the picture models' sd-server in llama-swap's config (29.3)
CONTEXTS = (32768, 16384, 8192, 4096)  # a new model's context: the largest that fits the video memory
FIT_CONTEXT = 8192  # what a variant is judged with before the download
MIN_BITS = 3.5  # below this a quantization loses too much to be recommended
MAX_BITS = 8.6  # above Q8_0 the quality is the same and the model only slower
GOOD_BITS = 4.5  # from about Q4_K_M on a quantization keeps nearly all of the model
OTHER_VRAM_GIB = 1.0  # what the desktop and other programs hold of the video memory while a model is swapped
RAM_RESERVE_GIB = 4.0  # Windows and the browser keep this much of the RAM
DISK_MARGIN_GIB = 2.0
SPLIT_TTL_S = 900  # a model partly in RAM unloads after 15 minutes without requests
RETRIES_S = (5, 15, 30, 60, 120)  # the waits between tries when the connection breaks
REPO_TTL_S = 3600.0  # Hugging Face's answers are kept this long
PROGRESS_S = 1.0  # a download's progress goes to the window this often
LIVE = ("queued", "downloading", "configuring", "trial")
VERDICTS = {
    "gpu": N_("помещается в видеопамять — быстро"),
    "split": N_("часть в ОЗУ — медленнее"),
    "split_moe": N_("часть в ОЗУ — у модели из «экспертов» это замедляет немного"),
    "cpu": N_("без видеокарты — в ОЗУ, медленно"),
    "no": N_("не поместится: не хватает памяти"),
}


class InstallError(Exception):
    """An install cannot be started or go on; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def curated() -> list[dict[str, Any]]:
    """The curated list's models (model_catalog.json)."""
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def picture_sets() -> list[dict[str, Any]]:
    """The curated picture models (model_catalog.json `pictures`, D55): {id, name, purposes, tier, license, original,
    size, files: [{role (sd-server's flag), repo, path}], flags, about}."""
    return json.loads(CATALOG.read_text(encoding="utf-8")).get("pictures") or []


def picture_verdict(entry: dict[str, Any], machine: dict[str, Any]) -> str:
    """How a picture model runs here (hardware.pictures_for): fast | slow - minutes a picture, on the processor or
    an integrated card."""
    if entry["tier"] == "light":
        return "fast"
    if entry["tier"] == "quality":
        return "fast" if machine.get("quality") else "slow"
    return "fast" if machine.get("pictures") == "main" else "slow"


def this_computer() -> dict[str, Any]:
    """What pictures this computer can make (29.2)."""
    return hardware.pictures_here((memory_sample() or {}).get("ram_total_gib") or 0)


def sd_server(text: str) -> Path | None:
    """The sd-server a config's `sd_server` macro names, when it is there."""
    value = llama_config.macros(text).get(SD_MACRO)
    path = Path(value.strip("'\"")) if value else None
    return path if path is not None and path.is_file() else None


# --- this computer against a model ---


def room(status: dict[str, Any] | None) -> dict[str, Any]:
    """The memory a new local model can have: video memory - free now, or all but the desktop's share when llama-swap
    holds a model (it unloads that one to start another) - and the RAM left over Windows' own."""
    gpu = (status or {}).get("gpu") or {}
    memory = (status or {}).get("memory") or {}
    loaded = [r.get("model", "") for r in ((status or {}).get("local") or {}).get("running", [])]
    total = (gpu.get("vram_total_mib") or 0) / 1024
    if not total:
        vram = 0.0
    elif loaded:
        vram = total - RESERVE_GIB - OTHER_VRAM_GIB
    else:
        vram = total - (gpu.get("vram_used_mib") or 0) / 1024 - 0.3
    ram_total = memory.get("ram_total_gib") or 0.0
    ram = ram_total - (memory.get("ram_used_gib") or 0.0) - RAM_RESERVE_GIB
    return {"gpu": gpu.get("name") or "", "vram_total_gib": round(total, 1), "vram_gib": round(max(vram, 0.0), 1),
            "ram_total_gib": round(ram_total, 1), "ram_gib": round(max(ram, 0.0), 1), "loaded": loaded}  # fmt: skip


def _kv_gib(info: dict[str, Any] | None, context: int, cache: str = "q8_0") -> float:
    if not info or not info.get("layers"):
        return context * 0.1 * 2**20 / GIB  # no header: about 0.1 MiB a token, more than most models need
    return kv_bytes(info, context, cache) / GIB


def verdict(size: int, info: dict[str, Any] | None, space: dict[str, Any], *, context: int = FIT_CONTEXT,
            mmproj: int = 0, moe: bool = False) -> str:  # fmt: skip
    """gpu (all in the video memory), split (partly in RAM), cpu (no graphics card: all in RAM), no."""
    need = size / GIB + _kv_gib(info, context) + mmproj / GIB
    if space["vram_gib"] and need + COMPUTE_GIB <= space["vram_gib"]:
        return "gpu"
    spill = need + COMPUTE_GIB - space["vram_gib"] if space["vram_gib"] else need
    if spill > space["ram_gib"]:
        return "no"
    if not space["vram_gib"]:
        return "cpu"
    return "split_moe" if moe else "split"


def recommend(found: list[dict[str, Any]], judged: dict[str, str], *, moe: bool = False) -> str | None:
    """The variant to offer: the best that fits the video memory; else the smallest decent one that fits at all.
    A mixture-of-experts model loses little speed with its experts in RAM: rather a 4-bit one partly there than a
    3-bit one in the video memory."""
    usable = [v for v in found if v["quant"] not in hf.SIXTEEN_BITS] or found
    decent = [v for v in usable if v.get("bits") is None or MIN_BITS <= v["bits"] <= MAX_BITS] or usable
    fast = [v for v in decent if judged.get(v["quant"]) == "gpu"]
    if fast:
        best = max(fast, key=lambda v: v["size"])
        if moe and (best.get("bits") or GOOD_BITS) < GOOD_BITS:
            better = [v for v in decent if judged.get(v["quant"]) == "split_moe"
                      and GOOD_BITS <= (v.get("bits") or 0) <= GOOD_BITS + 0.6]  # fmt: skip
            if better:
                return min(better, key=lambda v: v["size"])["quant"]
        return best["quant"]
    slow = [v for v in decent if judged.get(v["quant"]) not in (None, "no")]
    if slow:
        return min(slow, key=lambda v: v["size"])["quant"]
    anyhow = [v for v in usable if judged.get(v["quant"]) not in (None, "no")]
    return min(anyhow, key=lambda v: v["size"])["quant"] if anyhow else None


def extremes(found: list[dict[str, Any]], judged: dict[str, str]) -> dict[str, str | None]:
    """«Быстрее ↔ Качественнее» (10.6): the smallest decent variant that fits the video memory (else the smallest that
    runs at all), and the best one that runs at all, even partly in RAM."""
    usable = [v for v in found if v["quant"] not in hf.SIXTEEN_BITS] or found
    decent = [v for v in usable if v.get("bits") is None or MIN_BITS <= v["bits"] <= MAX_BITS] or usable
    runs = [v for v in decent if judged.get(v["quant"]) not in (None, "no")]
    fast = [v for v in runs if judged.get(v["quant"]) == "gpu"] or runs
    return {"fast": min(fast, key=lambda v: v["size"])["quant"] if fast else None,
            "quality": max(runs, key=lambda v: v["size"])["quant"] if runs else None}  # fmt: skip


def projector(found: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The vision projector to take: an 8-bit one, else the smallest."""
    if not found:
        return None
    return next((p for p in found if p["quant"] == "Q8_0"), None) or found[0]


def launch_plan(info: dict[str, Any], size: int, space: dict[str, Any], *, mmproj: int = 0) -> dict[str, Any]:
    """The new model's launch settings: all of it in the video memory with the largest context that fits (the
    vision projector in RAM if that is what lets it fit), else llama.cpp's --fit and a timer to unload."""
    trained = info.get("context_trained") or CONTEXTS[0]
    contexts = sorted({min(c, trained) for c in CONTEXTS}, reverse=True)
    for on_gpu in (True, False) if mmproj else (True,):
        for context in contexts:
            need = size / GIB + _kv_gib(info, context) + COMPUTE_GIB + (mmproj / GIB if on_gpu else 0)
            if space["vram_gib"] and need <= space["vram_gib"]:
                return {"context": context, "gpu_layers": "all", "cache_type": "q8_0", "mmproj_on_gpu": on_gpu,
                        "ttl": 0, "where": "gpu"}  # fmt: skip
    return {"context": min(FIT_CONTEXT, trained), "gpu_layers": "auto", "cache_type": "q8_0",
            "mmproj_on_gpu": False, "ttl": SPLIT_TTL_S, "where": "split"}  # fmt: skip


def smaller(plan: dict[str, Any]) -> dict[str, Any] | None:
    """The next try after a model did not start: half the context, then llama.cpp's --fit; None when nothing is
    left to give up."""
    if plan["gpu_layers"] != "auto" and plan["context"] > CONTEXTS[-1]:
        return {**plan, "context": max(plan["context"] // 2, CONTEXTS[-1])}
    if plan["gpu_layers"] != "auto":
        return {**plan, "gpu_layers": "auto", "context": min(plan["context"], FIT_CONTEXT), "ttl": SPLIT_TTL_S,
                "where": "split"}  # fmt: skip
    if plan["context"] > CONTEXTS[-1]:
        return {**plan, "context": max(plan["context"] // 2, CONTEXTS[-1])}
    return None


def models_root(drive: dict[str, Any]) -> Path:
    """Where models go on a drive: `X:\\AFO\\models`; on the Windows drive the user's own app data."""
    if drive.get("system") and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / NAME / "models"
    return Path(drive["root"]) / NAME / "models"


def pick_drive(found: list[dict[str, Any]], size_gib: float) -> dict[str, Any] | None:
    """The fastest drive with room for the files (the list comes fastest first)."""
    return next((d for d in found if d["free_gib"] >= size_gib + DISK_MARGIN_GIB), None)


def repo_name(repo: str) -> str:
    """A repository's model name without the format: "ggml-org/Qwen3-0.6B-GGUF" -> "Qwen3-0.6B"."""
    name = repo.split("/")[-1]
    for suffix in ("-GGUF", "-gguf", "_GGUF", ".GGUF"):
        name = name.removesuffix(suffix)
    return name


def id_base(repo: str, quant: str) -> str:
    return f"{repo_name(repo)}-{quant}"


# --- the rows ---


def _row(row) -> dict[str, Any]:
    out = dict(row)
    for key in ("files", "purposes", "plan", "trial"):
        out[key] = json.loads(out[key] or ("[]" if key in ("files", "purposes") else "{}"))
    out["percent"] = round(100 * out["done_bytes"] / out["total_bytes"], 1) if out["total_bytes"] else 0.0
    return out


def jobs(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM model_installs WHERE status != 'removed' ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def job(conn, job_id: str) -> dict[str, Any] | None:
    found = conn.execute("SELECT * FROM model_installs WHERE id = ?", (job_id,)).fetchone()
    return _row(found) if found else None


def _update(conn, job_id: str, **fields: Any) -> None:
    for key in ("files", "purposes", "plan", "trial"):
        if key in fields:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated_at"] = utc_now()
    names = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE model_installs SET {names} WHERE id = ?", (*fields.values(), job_id))


def installed(conn) -> list[dict[str, Any]]:
    return [_row(r) for r in conn.execute("SELECT * FROM model_installs WHERE status = 'done' ORDER BY name")]


class Installer:
    """The hub's model installs: Hugging Face asked and cached, one download at a time, the config and the trial."""

    def __init__(self, db, bus, gateway, secrets, *, llama_swap_url: str, status: Callable[[], dict[str, Any] | None],
                 drives: Callable[[], list[dict]] = disks.drives):  # fmt: skip
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self._secrets = secrets
        self._url = llama_swap_url
        self._status = status
        self.drives = drives  # the disks a model may go to (common/disks.py; tests give their own)
        # the trial run (the hub's «Проверить», hub/api/model_settings.run_check): set by the server
        self.trial: Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]] | None = None
        self.on_done: Callable[[str], Any] | None = None  # a model installed or added: its tuning (15.2)
        self.machine: Callable[[], dict[str, Any]] = this_computer  # what pictures it can make (tests give their own)
        self._task: asyncio.Task | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._wanted: dict[str, str] = {}  # job -> "paused" | "cancelled", what a stopped download becomes
        self._clients: dict[str, Any] = {}  # job -> who started it (the trial is recorded as theirs)
        self._repos: dict[str, tuple[float, dict[str, Any]]] = {}
        self._headers: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._shown: dict[str, tuple[float, int]] = {}  # job -> (when, bytes) of the last progress sent
        self._sending: set[asyncio.Task] = set()  # progress on its way (kept: a task nobody holds may vanish)
        self.speed: dict[str, float] = {}  # job -> bytes a second

    async def start(self) -> int:
        """Installs a stopped hub left going wait for «Продолжить» (their parts stay on disk)."""

        def pause(conn) -> int:
            return conn.execute(
                "UPDATE model_installs SET status = 'paused', error = ?, updated_at = ? WHERE status IN "
                "('queued', 'downloading', 'configuring', 'trial')",
                (tr("хаб перезапускался — загрузка продолжится по кнопке «Продолжить»"), utc_now()),
            ).rowcount

        return await self._db.acall(pause)

    async def stop(self) -> None:
        for event in self._cancel.values():
            event.set()
        for job_id in self._cancel:
            self._wanted.setdefault(job_id, "paused")
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 10.0)

    def token(self) -> str | None:
        return self._secrets.get(hf.TOKEN) or None

    # --- Hugging Face, cached ---

    async def repository(self, repo: str, *, fresh: bool = False) -> dict[str, Any]:
        kept = self._repos.get(repo)
        if kept and not fresh and time.monotonic() - kept[0] < REPO_TTL_S:
            return kept[1]
        async with httpx.AsyncClient(timeout=30.0) as http:
            found = await hf.repository(http, repo, self.token())
        self._repos[repo] = (time.monotonic(), found)
        return found

    async def header(self, repo: str, path: str) -> dict[str, Any] | None:
        """A model's attention shape from its first bytes; None when it cannot be read (the estimate guesses)."""
        if (repo, path) not in self._headers:
            try:
                self._headers[repo, path] = await asyncio.to_thread(hf.header, repo, path, self.token())
            except (hf.HubError, GGUFError) as exc:
                log.info("no header of %s/%s: %s", repo, path, exc)
                return None  # not kept: the next look tries again
        return self._headers[repo, path]

    def space(self) -> dict[str, Any]:
        return room(self._status())

    async def details(self, repo: str, entry: dict[str, Any] | None = None) -> dict[str, Any]:
        """A repository as the window shows it: each variant judged against this computer, the one recommended,
        the vision projector, the drives."""
        found = await self.repository(repo)
        variants = found["variants"]
        if not variants:
            raise InstallError(tr("в этом репозитории нет файлов GGUF для llama.cpp"), 404)
        info = await self.header(repo, variants[0]["files"][0]["path"])
        lens = projector(found["projectors"])
        space = self.space()
        moe = bool((entry or {}).get("moe")) or "moe" in found["architecture"].lower()
        judged = {v["quant"]: verdict(v["size"], info, space, mmproj=lens["size"] if lens else 0, moe=moe)
                  for v in variants}  # fmt: skip
        best = recommend(variants, judged, moe=moe)
        ends = extremes(variants, judged)
        shown = [{**v, "verdict": judged[v["quant"]], "words": tr(VERDICTS[judged[v["quant"]]]),
                  "gb": round(v["size"] / 1e9, 1), "recommended": v["quant"] == best,
                  "pick": "fast" if v["quant"] == ends["fast"] and ends["fast"] != best else
                  "quality" if v["quant"] == ends["quality"] and ends["quality"] != best else ""}
                 for v in variants]  # fmt: skip
        return {**found, "variants": shown, "recommended": best, "projector": lens, "space": space,
                "header": info, "moe": moe, "drives": await asyncio.to_thread(self.drives)}  # fmt: skip

    async def catalog(self) -> list[dict[str, Any]]:
        """The curated list, each with the variant recommended for this computer (or why it cannot say)."""

        async def one(entry: dict[str, Any]) -> dict[str, Any]:
            shown = {**entry, "purpose_words": [tr(PURPOSES[p]) for p in entry["purposes"]]}
            try:
                found = await self.details(entry["repo"], entry)
            except (hf.HubError, InstallError) as exc:
                return {**shown, "error": str(exc)}
            best = next((v for v in found["variants"] if v["recommended"]), None)
            smallest = min(found["variants"], key=lambda v: v["size"])  # when none fits: how far it is
            return {**shown, "license": found["license"], "gated": found["gated"], "page": found["page"],
                    "recommended": best, "smallest": smallest, "sizes_gb": [v["gb"] for v in found["variants"]],
                    "vision": bool(found["projector"])}  # fmt: skip

        listed = await asyncio.gather(*(one(e) for e in curated()))
        mine = {(r["repo"], r["quant"]): r for r in await self._db.acall(installed)}
        for item in listed:
            best = item.get("recommended")
            item["installed"] = [r["model_id"] for (repo, _q), r in mine.items() if repo == item["repo"]]
            if best:
                item["installed_recommended"] = (item["repo"], best["quant"]) in mine
        return listed

    async def search(self, query: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as http:
            return await hf.search(http, query, token=self.token())

    # --- picture models (29.3) ---

    async def _listed(self, repo: str) -> dict[str, Any]:
        kept = self._repos.get(f"files:{repo}")
        if kept and time.monotonic() - kept[0] < REPO_TTL_S:
            return kept[1]
        async with httpx.AsyncClient(timeout=30.0) as http:
            found = await hf.files(http, repo, self.token())
        self._repos[f"files:{repo}"] = (time.monotonic(), found)
        return found

    async def picture_files(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """A picture model's files as Hugging Face has them now: each with its repository, commit, size, sha256."""
        found = {repo: await self._listed(repo) for repo in dict.fromkeys(f["repo"] for f in entry["files"])}
        out = []
        for f in entry["files"]:
            listed = found[f["repo"]]["files"].get(f["path"])
            if listed is None:
                raise InstallError(tr("в {repo} нет файла {path}", repo=f["repo"], path=f["path"]), 404)
            out.append({"path": f["path"], "repo": f["repo"], "revision": found[f["repo"]]["revision"], "done": False,
                        "size": listed["size"], "sha256": listed["sha256"], "role": f["role"]})  # fmt: skip
        return out

    async def picture_catalog(self) -> list[dict[str, Any]]:
        """The curated picture models, each with its size and how it runs here."""
        machine = await asyncio.to_thread(self.machine)
        mine = {r["plan"].get("set"): r for r in await self._db.acall(installed)}
        listed = []
        for entry in picture_sets():
            verdict = picture_verdict(entry, machine)
            shown = {**entry, "purpose_words": [tr(PURPOSES[p]) for p in entry["purposes"]], "verdict": verdict,
                     "words": tr("быстро") if verdict == "fast" else tr("медленно — минуты на картинку"),
                     "recommended": entry["tier"] == ("main" if machine.get("pictures") == "main" else "light"),
                     "installed": [mine[entry["id"]]["model_id"]] if entry["id"] in mine else []}  # fmt: skip
            try:
                size = sum(f["size"] for f in await self.picture_files(entry))
                shown.update(size=size, gb=round(size / 1e9, 1))
            except (hf.HubError, InstallError) as exc:
                shown["error"] = str(exc)
            listed.append(shown)
        return listed

    async def _engine_files(self, build: str) -> list[dict[str, Any]]:
        """The picture engine's archives when the config names no sd-server yet: its pinned build for this computer."""
        if sd_server(await asyncio.to_thread(config_text)) is not None:
            return []
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                release = await engines.sd_cpp_release(http, build)
        except engines.EngineError as exc:
            raise InstallError(str(exc), 502) from exc
        missing = [a["name"] for a in release["assets"] if not a["sha256"]]
        if missing:  # as llama.cpp's (D46): nothing to run without it
            raise InstallError(tr("GitHub не дал контрольную сумму для {files}: без неё программа не ставится",
                                  files=", ".join(missing)), 502)  # fmt: skip
        return [{"path": a["name"], "url": a["url"], "size": a["size"], "sha256": a["sha256"], "role": "engine",
                 "tag": release["tag"], "build": build, "done": False} for a in release["assets"]]  # fmt: skip

    # --- installs ---

    async def _publish(self, job_id: str) -> None:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is not None:
            await self._bus.publish("install.changed", {"job": found})

    async def _set(self, job_id: str, **fields: Any) -> None:
        await self._db.acall(lambda conn: _update(conn, job_id, **fields))
        await self._publish(job_id)

    async def install(self, spec: dict[str, Any], client: Any) -> dict[str, Any]:
        """A new install: {repo, quant, projector (true/false/None: as the model has), drive or folder, name}."""
        repo, quant = spec["repo"], spec["quant"]
        entry = next((e for e in curated() if e["repo"] == repo), None)
        found = await self.details(repo, entry)
        variant = next((v for v in found["variants"] if v["quant"] == quant), None)
        if variant is None:
            raise InstallError(tr("у {repo} нет варианта {quant}", repo=repo, quant=quant), 404)
        rows = await self._db.acall(jobs)
        same = next((r for r in rows if r["repo"] == repo and r["quant"] == quant
                     and r["status"] not in ("cancelled", "removed")), None)  # fmt: skip
        if same and same["status"] == "done":
            raise InstallError(tr("эта модель уже установлена — «{name}»", name=same["name"]))
        if same:
            return same
        lens = found["projector"] if spec.get("projector") is not False else None
        if spec.get("projector") is None and entry is not None and "vision" not in entry["purposes"]:
            lens = None  # a model the catalog does not offer for pictures: no projector unless asked
        files = [{**f, "role": "model", "done": False} for f in variant["files"]]
        if lens:
            files.append({"path": lens["file"], "size": lens["size"], "sha256": lens["sha256"], "role": "mmproj",
                          "done": False})  # fmt: skip
        total = sum(f["size"] for f in files)
        root = self._root(spec, found["drives"])  # AFO's own `model` folder by default; too little room - said below
        folder = root / repo.replace("/", os.sep)
        try:
            _check_room(folder, total)
        except OSError as exc:
            raise InstallError(tr("папка {folder} недоступна: {error}", folder=folder, error=exc.strerror or exc),
                               422) from exc  # fmt: skip
        text = config_text()
        llama_config.server_command(text)  # no llama.cpp in the config: nothing to install into
        model_id = llama_config.free_id(text, id_base(repo, quant))
        name = spec.get("name") or f"{(entry or {}).get('name') or repo_name(repo)} {quant}"
        purposes = list((entry or {}).get("purposes") or ["chat"])
        if lens and "vision" not in purposes:
            purposes.append("vision")
        job_id = uuid.uuid4().hex[:12]

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO model_installs (id, repo, revision, quant, files, folder, model_id, name, purposes, "
                "license, status, total_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
                (job_id, repo, found["revision"], quant, json.dumps(files), str(folder), model_id, name,
                 json.dumps(purposes), found["license"], total),
            )  # fmt: skip

        await self._db.acall(add)
        self._clients[job_id] = client
        await self._publish(job_id)
        self._kick()
        return await self._db.acall(lambda conn: job(conn, job_id))

    async def install_pictures(self, spec: dict[str, Any], client: Any) -> dict[str, Any]:
        """A picture model from the curated list: {id, drive or folder}; the engine comes with the first one."""
        entry = next((e for e in picture_sets() if e["id"] == spec["id"]), None)
        if entry is None:
            raise InstallError(tr("в списке нет модели {model}", model=spec["id"]), 404)
        rows = await self._db.acall(jobs)
        same = next((r for r in rows if r["plan"].get("set") == entry["id"]
                     and r["status"] not in ("cancelled", "removed")), None)  # fmt: skip
        if same and same["status"] == "done":
            raise InstallError(tr("эта модель уже установлена — «{name}»", name=same["name"]))
        if same:
            return same
        files = await self.picture_files(entry)
        machine = await asyncio.to_thread(self.machine)
        engine = await self._engine_files(machine["build"])
        root = self._root(spec, await asyncio.to_thread(self.drives))
        folder = root / "pictures" / entry["id"]
        try:
            _check_room(folder, sum(f["size"] for f in files))
        except OSError as exc:
            raise InstallError(tr("папка {folder} недоступна: {error}", folder=folder, error=exc.strerror or exc),
                               422) from exc  # fmt: skip
        model_id = llama_config.free_id(await asyncio.to_thread(config_text), entry["id"])
        plan = {"engine": "sd", "set": entry["id"], "tier": entry["tier"], "build": machine["build"],
                "flags": entry["flags"], "size": entry["size"]}  # fmt: skip
        job_id = uuid.uuid4().hex[:12]
        everything = [*engine, *files]

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO model_installs (id, repo, revision, quant, files, folder, model_id, name, purposes, "
                "license, status, total_bytes, plan) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, files[0]["repo"], files[0]["revision"], hf.quant_of(files[0]["path"]), json.dumps(everything),
                 str(folder), model_id, entry["name"], json.dumps(entry["purposes"]), entry["license"],
                 sum(f["size"] for f in everything), json.dumps(plan)),
            )  # fmt: skip

        await self._db.acall(add)
        self._clients[job_id] = client
        await self._publish(job_id)
        self._kick()
        return await self._db.acall(lambda conn: job(conn, job_id))

    def _root(self, spec: dict[str, Any], drives: list[dict[str, Any]]) -> Path:
        """Where a model goes: the folder asked for, the drive's AFO folder, or AFO's own `model` folder."""
        if spec.get("folder"):
            root = Path(spec["folder"]).expanduser()
            if not root.is_absolute():
                raise InstallError(tr("папка для моделей — полный путь"), 422)
            return root
        if spec.get("drive"):
            drive = next((d for d in drives if d["letter"] == spec["drive"].upper()[:1]), None)
            if drive is None:
                raise InstallError(tr("нет диска {drive}", drive=spec["drive"]), 422)
            return models_root(drive)
        return models_home()

    async def add_local(self, spec: dict[str, Any], client: Any) -> dict[str, Any]:
        """A model already on this computer (15.1, D49): {path (its file; the first part of a split one), projector
        (a file, or ''), name} - written into llama-swap's config where it lies, without a copy, and tried as an
        install is. Its files stay the person's: removing it never deletes them."""
        path = Path(spec["path"]).expanduser()
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in (".gguf", ""):
            raise InstallError(tr("нет такого файла модели: {path}", path=spec["path"]), 422)
        try:
            info = await asyncio.to_thread(model_info, path)
        except (GGUFError, OSError) as exc:
            raise InstallError(tr("{path} — не модель GGUF: {error}", path=path, error=exc), 422) from exc
        rows = await self._db.acall(jobs)
        same = next((r for r in rows if r["status"] not in ("cancelled", "removed", "failed")
                     and Path(r["folder"]) / PurePosixPath(r["files"][0]["path"]).name == path), None)  # fmt: skip
        if same is not None:
            raise InstallError(tr("эта модель уже добавлена — «{name}»", name=same["name"]))
        split = SPLIT.match(path.name)
        parts = sorted(path.parent.glob(f"{split['stem']}-*-of-{split['parts']}.gguf")) if split else [path]
        files = [{"path": p.name, "size": p.stat().st_size, "sha256": None, "role": "model", "done": True}
                 for p in parts]  # fmt: skip
        lens = Path(spec["projector"]) if spec.get("projector") else None
        if lens is not None:
            if lens.parent != path.parent or not lens.is_file():
                raise InstallError(tr("проектор картинок должен лежать рядом с моделью: {path}", path=lens), 422)
            files.append({"path": lens.name, "size": lens.stat().st_size, "sha256": None, "role": "mmproj",
                          "done": True})  # fmt: skip
        stem = split["stem"] if split else path.stem
        quant = hf.quant_of(path.name) or ""
        text = config_text()
        llama_config.server_command(text)  # no llama.cpp in the config: nothing to add into
        model_id = llama_config.free_id(text, re.sub(r"[^a-z0-9._-]+", "-", stem.lower()).strip("-") or "model")
        name = spec.get("name") or info.get("name") or stem
        total = sum(f["size"] for f in files)
        job_id = uuid.uuid4().hex[:12]

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO model_installs (id, repo, revision, quant, files, folder, model_id, name, purposes, "
                "license, status, total_bytes, done_bytes, own_files) VALUES (?, '', '', ?, ?, ?, ?, ?, ?, '', "
                "'queued', ?, ?, 0)",
                (job_id, quant, json.dumps(files), str(path.parent), model_id, name,
                 json.dumps(["chat", "vision"] if lens else ["chat"]), total, total),
            )  # fmt: skip

        await self._db.acall(add)
        self._clients[job_id] = client
        await self._publish(job_id)
        self._kick()
        return await self._db.acall(lambda conn: job(conn, job_id))

    async def config_files(self) -> dict[str, str]:
        """The model files llama-swap's config runs (normalised path -> model id): what is in it already."""
        try:
            text = await asyncio.to_thread(config_text)
        except InstallError:
            return {}
        out = {}
        for model_id in llama_config.model_blocks(text):
            with contextlib.suppress(llama_config.ConfigError):
                file = llama_config.model_view(text, model_id).get("file") or ""
                if file:
                    out[os.path.normcase(os.path.normpath(file))] = model_id
        return out

    async def _get(self, job_id: str) -> dict[str, Any]:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is None:
            raise InstallError(tr("нет такой установки"), 404)
        return found

    async def pause(self, job_id: str) -> dict[str, Any]:
        found = await self._get(job_id)
        if found["status"] == "queued":
            await self._set(job_id, status="paused", error="")
        elif found["status"] == "downloading" and job_id in self._cancel:
            self._wanted[job_id] = "paused"
            self._cancel[job_id].set()
        else:
            raise InstallError(tr("приостановить можно только загрузку"))
        return await self._get(job_id)

    async def resume(self, job_id: str, client: Any) -> dict[str, Any]:
        found = await self._get(job_id)
        if found["status"] not in ("paused", "failed"):
            raise InstallError(tr("продолжить можно приостановленную или неудачную установку"))
        self._clients[job_id] = client
        await self._set(job_id, status="queued", error="")
        self._kick()
        return await self._get(job_id)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Stops an install and throws its files away; an installed model is removed with `remove`."""
        found = await self._get(job_id)
        if found["status"] == "done":
            raise InstallError(tr("модель уже установлена — её можно удалить"))
        if found["status"] in ("cancelled", "removed"):
            return found
        if job_id in self._cancel:
            self._wanted[job_id] = "cancelled"
            self._cancel[job_id].set()
            return found
        await asyncio.to_thread(_delete_files, found)
        await self._set(job_id, status="cancelled", error="")
        return await self._get(job_id)

    async def forget(self, job_id: str) -> None:
        """An install that did not finish off the list - with its files."""
        found = await self._get(job_id)
        if found["status"] in (*LIVE, "done"):
            raise InstallError(tr("эта установка ещё идёт или модель установлена"))
        await asyncio.to_thread(_delete_files, found)
        await self._db.acall(lambda conn: conn.execute("DELETE FROM model_installs WHERE id = ?", (job_id,)))
        await self._bus.publish("install.forgotten", {"id": job_id})

    async def remove(self, model_id: str) -> dict[str, Any]:
        """An installed model away: unloaded, its block out of the config, its files deleted."""
        mine = await self._db.acall(installed)
        found = next((r for r in mine if r["model_id"] == model_id), None)
        if found is None:
            going = next((r for r in await self._db.acall(jobs) if r["model_id"] == model_id), None)
            if going is not None:
                raise InstallError(tr("«{name}» ещё не установлена — её установку можно отменить", name=going["name"]))
            raise InstallError(tr("«{model}» ставил не AFO — удалите её из config.yaml сами", model=model_id), 404)
        await self._unload(model_id)
        path = llama_config.config_path()
        text = await asyncio.to_thread(config_text)
        if model_id in llama_config.model_blocks(text):
            new = llama_config.remove_model(text, model_id)
            backup = await asyncio.to_thread(llama_config.write, path, new)
            ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
            if not ok and problem != tr(llama_config.NOT_ANSWERING):
                await asyncio.to_thread(llama_config.restore, path, backup)
                raise InstallError(tr("llama-swap не принял правку ({problem}) — конфиг возвращён", problem=problem),
                                   502)  # fmt: skip
        await asyncio.to_thread(_delete_files, found)
        await self._set(found["id"], status="removed", error="")
        await self._sync()
        return await self._get(found["id"])

    async def _unload(self, model_id: str) -> None:
        running = [r.get("model") for r in ((self._status() or {}).get("local") or {}).get("running", [])]
        if model_id not in running:
            return
        with contextlib.suppress(httpx.HTTPError):
            async with httpx.AsyncClient(timeout=30.0) as http:
                await http.post(f"{self._url.rstrip('/')}/api/models/unload")
        await asyncio.sleep(1.0)  # the server lets go of its files

    async def _sync(self) -> None:
        with contextlib.suppress(Exception):
            await self._gateway.sync_catalog()

    # --- the worker ---

    def _kick(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())

    async def _work(self) -> None:
        while True:

            def oldest(conn):
                found = conn.execute("SELECT * FROM model_installs WHERE status = 'queued' ORDER BY updated_at "
                                     "LIMIT 1").fetchone()  # fmt: skip
                return _row(found) if found else None

            found = await self._db.acall(oldest)
            if found is None:
                return
            try:
                await self._run(found)
            except Exception:  # one install's trouble must not stop the rest
                log.exception("install %s", found["id"])
                await self._set(
                    found["id"], status="failed", error=tr("внутренняя ошибка хаба — подробности в журнале")
                )

    async def _run(self, found: dict[str, Any]) -> None:
        job_id = found["id"]
        cancel = self._cancel[job_id] = threading.Event()
        self._wanted.pop(job_id, None)
        try:
            await self._download(found, cancel)
            found = await self._get(job_id)
            if found["plan"].get("engine") == "sd":
                reachable = await self._configure_pictures(found)
                await self._try_picture(await self._get(job_id), reachable)
            else:
                reachable = await self._configure(found)
                await self._try(await self._get(job_id), reachable)
        except hf.Cancelled:
            wanted = self._wanted.pop(job_id, "paused")
            if wanted == "cancelled":
                await asyncio.to_thread(_delete_files, await self._get(job_id))
                await self._set(job_id, status="cancelled", error="")
            else:
                await self._set(job_id, status="paused", error="")
        except (hf.HubError, InstallError, llama_config.ConfigError, GGUFError, OSError) as exc:
            log.info("install %s failed: %s", job_id, exc)
            await self._set(job_id, status="failed", error=str(exc))
        finally:
            self._cancel.pop(job_id, None)
            self.speed.pop(job_id, None)

    def _reporter(self, job_id: str, base: int, total: int) -> Callable[[int], None]:
        loop = asyncio.get_running_loop()

        def report(done: int) -> None:  # in the download's thread
            now = time.monotonic()
            when, before = self._shown.get(job_id, (0.0, 0))
            if now - when < PROGRESS_S:
                return
            if when:
                self.speed[job_id] = max(base + done - before, 0) / (now - when)
            self._shown[job_id] = (now, base + done)
            loop.call_soon_threadsafe(self._send, job_id, base + done, total)

        return report

    def _send(self, job_id: str, done: int, total: int) -> None:
        task = asyncio.create_task(self._progress(job_id, done, total))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def _progress(self, job_id: str, done: int, total: int) -> None:
        await self._db.acall(lambda conn: _update(conn, job_id, done_bytes=done))
        shown = {"id": job_id, "done_bytes": done, "total_bytes": total, "speed": round(self.speed.get(job_id, 0.0))}
        await self._bus.publish("install.progress", shown, persist=False)

    async def _download(self, found: dict[str, Any], cancel: threading.Event) -> None:
        job_id, files = found["id"], found["files"]
        folder = Path(found["folder"])
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        left = sum(f["size"] for f in files if not f["done"])
        await asyncio.to_thread(_check_room, folder, left)
        await self._set(job_id, status="downloading", error="")
        done = sum(f["size"] for f in files if f["done"])
        for f in files:
            dest = _dest(folder, f)
            if f["done"] and dest.exists() and dest.stat().st_size == f["size"]:
                continue
            if dest.exists() and dest.stat().st_size == f["size"]:  # got its name only once its sha256 matched
                f["done"] = True
            else:
                await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)  # the engines' downloads
                await self._fetch(found, f, dest, done, cancel)
            done += f["size"]
            f["done"] = True
            await self._set(job_id, files=files, done_bytes=done)

    async def _fetch(self, found: dict[str, Any], f: dict[str, Any], dest: Path, base: int,
                     cancel: threading.Event) -> None:  # fmt: skip
        # a picture model's file names its repository and commit; the engine's archive is GitHub's (no token there)
        url = f.get("url") or hf.file_url(f.get("repo") or found["repo"], f["path"], f.get("revision") or
                                          found["revision"])  # fmt: skip
        progress = hf.Progress()
        report = self._reporter(found["id"], base, found["total_bytes"])
        broken = 0
        for attempt in range(len(RETRIES_S) + 1):
            try:
                await asyncio.to_thread(hf.download, url, dest, size=f["size"], sha256=f.get("sha256"),
                                        token=None if f.get("url") else self.token(), report=report, cancel=cancel,
                                        progress=progress)  # fmt: skip
                return
            except hf.HubError as exc:
                broken += exc.status == 409
                if exc.status in (403, 404, 429) or broken > 1 or attempt == len(RETRIES_S):
                    raise
                wait = RETRIES_S[attempt]
                await self._set(found["id"], error=tr("{error} — новая попытка через {wait} с", error=exc, wait=wait))
                if await asyncio.to_thread(cancel.wait, wait):
                    raise hf.Cancelled() from exc
                await self._set(found["id"], error="")

    async def _configure(self, found: dict[str, Any]) -> bool:
        """The model's block in llama-swap's config, with launch settings for this computer; whether llama-swap
        answered (and read it)."""
        job_id = found["id"]
        await self._set(job_id, status="configuring", error="")
        folder = Path(found["folder"])
        model_file = folder / PurePosixPath(next(f for f in found["files"] if f["role"] == "model")["path"]).name
        lens = next((f for f in found["files"] if f["role"] == "mmproj"), None)
        lens_file = folder / PurePosixPath(lens["path"]).name if lens else None
        info = await asyncio.to_thread(model_info, model_file)
        size = sum(f["size"] for f in found["files"] if f["role"] == "model")
        plan = launch_plan(info, size, self.space(), mmproj=lens["size"] if lens else 0)
        path = llama_config.config_path()
        text = await asyncio.to_thread(config_text)
        model_id = found["model_id"]
        if model_id in llama_config.model_blocks(text):
            if _ours(text, model_id, job_id):
                text = llama_config.remove_model(text, model_id)  # an earlier try of this install: written anew
            else:
                model_id = llama_config.free_id(text, model_id)  # someone else's model took the name meanwhile
        block = _block(found, model_id, llama_config.server_command(text), model_file, lens_file, plan)
        new = llama_config.add_model(text, model_id, block)
        backup = await asyncio.to_thread(llama_config.write, path, new)
        ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
        if not ok and problem != tr(llama_config.NOT_ANSWERING):
            await asyncio.to_thread(llama_config.restore, path, backup)
            raise InstallError(tr("llama-swap не принял новую модель ({problem}) — конфиг возвращён",
                                  problem=problem), 502)  # fmt: skip
        await self._set(job_id, model_id=model_id, plan={**plan, "info": info})
        await self._sync()
        await self._bus.publish("llama.config", {"model_id": model_id, "installed": True, "backup": backup.name})
        return ok

    async def _try(self, found: dict[str, Any], reachable: bool) -> None:
        """The trial run: the model answers one question; when it does not start, smaller settings and again."""
        job_id, model_id = found["id"], found["model_id"]
        client = self._clients.get(job_id)
        if not reachable or self.trial is None or client is None:
            why = tr("llama-swap не отвечает — модель проверится при первом разговоре") if not reachable else \
                tr("проверка запуска не выполнялась")  # fmt: skip
            await self._set(job_id, status="done", trial={"skipped": why})
            self._done(model_id)
            return
        await self._set(job_id, status="trial")
        plan = {k: v for k, v in found["plan"].items() if k != "info"}
        tries: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        while True:
            model = await self._model(model_id)
            if model is None:
                result = {"ok": False, "error": tr("хаб не видит модель в списке llama-swap")}
            else:
                try:
                    result = await self.trial(model, client)
                except Exception as exc:  # the check's own refusal (HTTPException) or a provider's trouble
                    result = {"ok": False, "error": str(getattr(exc, "detail", "") or exc)}
            tries.append({"context": plan["context"], "gpu_layers": plan["gpu_layers"], "ok": bool(result.get("ok")),
                          "seconds": result.get("seconds"), "error": result.get("error") or ""})  # fmt: skip
            if result.get("ok") or model is None:
                break
            plan = smaller(plan)
            if plan is None:
                break
            await self._relaunch(model_id, plan)
        if result.get("ok"):
            await self._set(job_id, status="done", plan={**found["plan"], **plan}, trial={
                "seconds": result.get("seconds"), "tok_s": result.get("tok_s"), "ttft_s": result.get("ttft_s"),
                "tries": tries, "where": plan["where"]})  # fmt: skip
            self._done(model_id)
            return
        await self._withdraw(model_id)
        await self._set(job_id, status="failed", trial={"tries": tries},
                        error=tr("модель скачана, но не запустилась: {error}. Файлы остались — «Повторить» "
                                 "попробует снова; или удалите",
                                 error=result.get("error") or tr("нет ответа")))  # fmt: skip

    async def _configure_pictures(self, found: dict[str, Any]) -> bool:
        """A picture model's block (and, with the first one, the engine unpacked and named in the `sd_server` macro);
        whether llama-swap answered (and read it)."""
        job_id = found["id"]
        await self._set(job_id, status="configuring", error="")
        folder = Path(found["folder"])
        text = await asyncio.to_thread(config_text)
        engine = [f for f in found["files"] if f["role"] == "engine"]
        if engine:
            target = engines_home() / "stable-diffusion.cpp" / f"{engine[0]['tag']}-{engine[0]['build']}"
            try:
                for f in engine:
                    await asyncio.to_thread(engines.unpack, _dest(folder, f), target)
                server = engines.sd_server_in(target)
            except engines.EngineError as exc:
                raise InstallError(str(exc), 502) from exc
            text = llama_config.with_macro(text, SD_MACRO, "'\"" + str(server).replace("\\", "/") + "\"'")
        elif sd_server(text) is None:
            raise InstallError(tr("в config.yaml нет движка картинок (sd-server) — поставьте модель заново"))
        model_id = found["model_id"]
        if model_id in llama_config.model_blocks(text):
            if _ours(text, model_id, job_id):
                text = llama_config.remove_model(text, model_id)
            else:
                model_id = llama_config.free_id(text, model_id)
        block = llama_config.sd_block(
            model_id,
            name=found["name"],
            description=tr("{name}, установлена AFO: рисует картинки.", name=found["name"]),
            server=f"${{{SD_MACRO}}}",
            files={f["role"]: str(_dest(folder, f)) for f in found["files"] if f["role"] != "engine"},
            flags=list(found["plan"].get("flags") or []),
            metadata={
                "source": f"huggingface.co/{found['repo']}",
                "license": found["license"] or "?",
                "localchat_install": job_id,
            },
        )
        path = llama_config.config_path()
        new = llama_config.add_model(text, model_id, block)
        backup = await asyncio.to_thread(llama_config.write, path, new)
        ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
        if not ok and problem != tr(llama_config.NOT_ANSWERING):
            await asyncio.to_thread(llama_config.restore, path, backup)
            raise InstallError(tr("llama-swap не принял новую модель ({problem}) — конфиг возвращён",
                                  problem=problem), 502)  # fmt: skip
        await self._set(job_id, model_id=model_id)
        await self._bus.publish("llama.config", {"model_id": model_id, "installed": True, "backup": backup.name})
        return ok

    async def _try_picture(self, found: dict[str, Any], reachable: bool) -> None:
        """The trial picture: one small picture through llama-swap; a blank one does not count (hub/visual.py)."""
        job_id, model_id = found["id"], found["model_id"]
        if not reachable:
            await self._set(job_id, status="done", trial={"skipped": tr("llama-swap не отвечает — модель проверится "
                                                                        "при первой картинке")})  # fmt: skip
            return
        await self._set(job_id, status="trial")
        result = await visual.trial(self._url, model_id)
        if result["ok"]:
            await self._set(job_id, status="done", trial={"seconds": result["seconds"], "size": result["size"]})
            return
        await self._withdraw(model_id)
        await self._set(job_id, status="failed", trial={"seconds": result.get("seconds")},
                        error=tr("модель скачана, но пробная картинка не вышла: {error}. Файлы остались — "
                                 "«Повторить» попробует снова; или удалите", error=result["error"]))  # fmt: skip

    def _done(self, model_id: str) -> None:
        if self.on_done is not None:
            self.on_done(model_id)

    async def _model(self, model_id: str) -> dict[str, Any] | None:
        for _ in range(5):
            model = await self._db.acall(lambda conn: store.get_model(conn, f"llama-swap/{model_id}"))
            if model is not None and model["enabled"]:
                return model
            await self._sync()
            await asyncio.sleep(0.5)
        return None

    async def _relaunch(self, model_id: str, plan: dict[str, Any]) -> None:
        path = llama_config.config_path()
        text = await asyncio.to_thread(config_text)
        changes = {"context": plan["context"], "gpu_layers": plan["gpu_layers"], "ttl": plan["ttl"]}
        new = llama_config.edit(text, model_id, changes)
        await asyncio.to_thread(llama_config.write, path, new)
        await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)), (model_id, plan["context"]))
        await self._sync()

    async def _withdraw(self, model_id: str) -> None:
        """A model that would not start leaves the config (its files stay)."""
        path = llama_config.config_path()
        text = await asyncio.to_thread(config_text)
        if model_id in llama_config.model_blocks(text):
            new = llama_config.remove_model(text, model_id)
            await asyncio.to_thread(llama_config.write, path, new)
            await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
            await self._sync()


def config_text() -> str:
    path = llama_config.config_path()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(tr("нет конфига llama-swap ({path}): сначала нужен llama-swap (мастер первого запуска)",
                              path=path), 409) from exc  # fmt: skip


def _ours(text: str, model_id: str, job_id: str) -> bool:
    start, end = llama_config.model_blocks(text)[model_id]
    return any(f'localchat_install: "{job_id}"' in line for line in text.splitlines()[start:end])


def _block(found: dict[str, Any], model_id: str, server: str, model_file: Path, lens_file: Path | None,
           plan: dict[str, Any]) -> str:  # fmt: skip
    purposes = ", ".join(tr(PURPOSES[p]).lower() for p in found["purposes"] if p in PURPOSES)
    if not found.get("own_files", 1):  # found on the drives (15.1): where it lies, the person's file
        description = tr("{name}: файл на этом компьютере, добавлен AFO: {purposes}.", name=found["name"],
                         purposes=purposes)  # fmt: skip
    else:
        description = tr("{repo} {quant}, установлена AFO: {purposes}.", repo=found["repo"], quant=found["quant"],
                         purposes=purposes)  # fmt: skip
    return llama_config.model_block(
        model_id,
        name=found["name"],
        description=description,
        server=server,
        model=str(model_file),
        mmproj=str(lens_file) if lens_file else "",
        mmproj_on_gpu=plan["mmproj_on_gpu"],
        context=plan["context"],
        gpu_layers=plan["gpu_layers"],
        cache_type=plan["cache_type"],
        ttl=plan["ttl"],
        metadata={
            "source": f"huggingface.co/{found['repo']}" if found["repo"] else str(model_file),
            "license": found["license"] or "?",
            "localchat_install": found["id"],
        },
    )


def _dest(folder: Path, f: dict[str, Any]) -> Path:
    """Where a file of an install goes: the model's folder; the picture engine's archive - the engines' downloads."""
    if f["role"] == "engine":
        return engines_home() / "downloads" / f["path"]
    return folder / PurePosixPath(f["path"]).name


def _check_room(folder: Path, size: int) -> None:
    anchor = Path(folder.anchor or folder)
    free = shutil.disk_usage(anchor).free
    if free < size + DISK_MARGIN_GIB * GIB:
        raise InstallError(tr("на диске {disk} не хватает места: нужно {need:.1f} ГБ, свободно {free:.1f} ГБ",
                              disk=anchor, need=size / GIB + DISK_MARGIN_GIB, free=free / GIB), 507)  # fmt: skip


def _delete_files(found: dict[str, Any]) -> None:
    """This install's files (and their parts) gone, then its folder and the author's folder if they are empty - only
    files AFO downloaded: a model found on the drives keeps its files whatever happens (15.1)."""
    if not found.get("own_files", 1):
        return
    folder = Path(found["folder"])
    for f in found["files"]:
        if f["role"] == "engine":
            continue  # the picture engine stays: the other picture models run on it
        dest = folder / PurePosixPath(f["path"]).name
        for path in (dest, dest.with_name(dest.name + ".part")):
            for _ in range(10):  # a server that just let go of the file: Windows may hold it a moment longer
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.5)
    for empty in (folder, folder.parent):
        with contextlib.suppress(OSError):
            empty.rmdir()
