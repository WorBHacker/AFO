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
  files stay for «Повторить» or removal. A model from the drives is not tried when added: «Пробный запуск» tries
  those added one after another, in the order added (34.5, D57). A trial waits while «Подбор параметров» measures
  and while a chat answers - one model at a time on the graphics card (34.4).
- Removal: the model unloaded, its block out of the config, its files deleted - only what this installer put there.
- Picture models (D55, 29.3): the catalog's `pictures`, each a set of files from several repositories (diffusion
  model, text encoder, VAE), for stable-diffusion.cpp's sd-server. The engine comes with the first one - its pinned
  build for this computer (hub/engines.py), downloaded as part of the install and named in the config's `sd_server`
  macro; the trial is one small picture through llama-swap, and a blank one does not count (hub/visual.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from common import disks, hardware, kept_json
from common.gguf import GGUFError, model_info, same_vocabulary, tokenizer_info
from common.i18n import N_, tr
from common.llama_memory import (
    COMPUTE_GIB,
    GIB,
    RAM_RESERVE_GIB,
    RESERVE_GIB,
    draft_load,
    estimate,
    kv_bytes,
    moe_load,
    quantized_cache,
    short_now,
)
from common.paths import NAME, engines_home, models_home

from . import engines, hf, llama_config, store, visual
from .db import utc_now
from .gpu_card import GIVEN_WORDS, TRIAL, GpuCard, Played, Quiet, named
from .model_scan import SPLIT, ollama_names
from .system_status import memory_sample
from .tuning import server_path

log = logging.getLogger("hub.install")

CATALOG = Path(__file__).with_name("model_catalog.json")
PURPOSES = {"chat": N_("Разговоры"), "code": N_("Код"), "vision": N_("Понимать картинки"), "image": N_("Рисовать"),
            "video": N_("Видео")}  # fmt: skip
SD_MACRO = "sd_server"  # the picture models' sd-server in llama-swap's config (29.3)
# a picture set's own model among its files (the rest: encoders, VAE) - and a video model's other half (48.2)
PICTURE_MAIN = ("model", "diffusion-model", "high-noise-diffusion-model")
SD_CPU_MACRO = "sd_server_cpu"  # the processor's build beside the card's: the ladder's last rung (29.4в)
SHARED = "_shared"  # under `pictures`: a family's encoders and VAE, one copy for all its models (47.3)
# a picture model judged by its sizes (47.4) as stable-diffusion.cpp's auto-fit places it when it starts (master-1053,
# backend_fit.cpp) - before any picture's size is asked, the computing cut into pieces later to fit what is left: the
# card's free memory less its 512 MiB; the model there when it leaves room for computing - 2 GiB, one for all the
# parts, they compute in turn - and for the encoder's largest tensor, staged beside it (~0.5 GiB for Qwen's). 47.9,
# measured: Qwen-Image-2512 Q4_K_M (12.4 GiB) on a 16 GB card did not fit - all in RAM, 37 s a 1024² picture
PICTURE_COMPUTE_GIB = 2.0
PICTURE_STAGING_GIB = 0.5  # when the encoder's own largest tensor is not read (the fix after 0.1.15: it is, mostly)
SD_MARGIN_GIB = 0.5
TIGHT_GIB = 0.5  # less than this left on the card beside a picture model: «впритык» (the fix after 0.1.15)
CONTEXTS = (32768, 16384, 8192, 4096)  # a new model's context: the largest that fits the video memory
FIT_CONTEXT = 8192  # what a variant is judged with before the download
MIN_BITS = 3.5  # below this a quantization loses too much to be recommended
MAX_BITS = 8.6  # above Q8_0 the quality is the same and the model only slower
GOOD_BITS = 4.5  # from about Q4_K_M on a quantization keeps nearly all of the model
SAME_BITS = 7.5  # from about Q8_0 on - the same as the original (41.3)
FIT_BITS = 4.9  # a search's «поместится» (32.5): a model taken as it usually is, about Q4_K_M
FIT_QUANT = "Q4_K_M"  # what the estimate beside each found model is of (41.1)
# a search's sizes (32.5): parameters from - to (0: no bound); "fits" - as much as this computer holds (fits_params)
SIZES = {"small": (0, 4 * 10**9), "medium": (4 * 10**9, 15 * 10**9), "large": (15 * 10**9, 40 * 10**9),
         "huge": (40 * 10**9, 0)}  # fmt: skip
DISK_MARGIN_GIB = 2.0
BUSY_SLACK_GIB = 0.5  # the desktop holding a little more video memory than its share is not worth a word (38.2)
# what a model may not count on of the computer's memory (38.1), the user's to change (38.3): hub.db's settings
RESERVE_SETTING = "install.reserve"
DEFAULT_RESERVE = {"vram_gib": RESERVE_GIB, "ram_gib": RAM_RESERVE_GIB}
RESERVE_BOUNDS = {"vram_gib": (0.0, 8.0), "ram_gib": (0.0, 32.0)}
SPLIT_TTL_S = 900  # a model partly in RAM unloads after 15 minutes without requests
RETRIES_S = (5, 15, 30, 60, 120)  # the waits between tries when the connection breaks
REPO_TTL_S = 3600.0  # an answer of Hugging Face older than this is shown and asked again in the background (35.3)
CLOSED_TTL_S = 60.0  # «closed to this token» believed this long - the person may accept the terms meanwhile
PROGRESS_S = 1.0  # a download's progress goes to the window this often
CARD_WAIT_S = 1.0  # how often a trial waiting for the graphics card looks again (34.4)
GAME_WORDS = N_("ждёт: на видеокарте игра")  # a trial waiting for the quiet while a game is on the card (48.9б5)
ENGINE_WAIT_S = 2.0  # how often a model downloaded before the engine looks for it again (39.3)
ENGINE_WAIT = N_("ждёт программу для локальных моделей: она скачивается")
# a draft model guesses ahead for a model at least this many times its size (42.3): a larger one guesses too slowly to
# give anything (Qwen3-8B in Q4_K_M is 6,5 times Qwen3-0.6B in Q8_0)
DRAFT_RATIO = 5
# the draft models AFO downloaded (42.5), hub.db's settings: their file -> {repo, file, sha256, size, waiting - the
# models whose tuning has not measured it yet}
DRAFTS_SETTING = "install.drafts"
LIVE = ("queued", "downloading", "trialwait", "configuring", "trial")
IN_CONFIG = ("done", "added")  # an install whose block llama-swap's config has (34.5: added - not tried yet)
VERDICTS = {
    "gpu": N_("помещается в видеопамять — быстро"),
    "split": N_("часть в ОЗУ — медленнее"),
    "split_moe": N_("часть в ОЗУ — у модели из «экспертов» это замедляет немного"),
    "cpu": N_("без видеокарты — в ОЗУ, медленно"),
    "no": N_("не поместится: не хватает памяти"),
}
# a picture or video model's verdict (the fix after 0.1.15): beside it the window says how much goes on the card and
# how much into the computer's memory - picture_place
PICTURE_VERDICTS = {
    "gpu": N_("быстро"),
    "tight": N_("впритык: если другие программы займут чуть больше видеопамяти — медленнее"),
    "split": N_("медленнее: модель не помещается в видеопамять, видеокарта считает её по частям"),
    "cpu": N_("медленно: без видеокарты"),
    "no": N_("не хватит памяти компьютера"),
}


class InstallError(Exception):
    """An install cannot be started or go on; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class _Deferred(Exception):
    """A senior needed the card (48.9в), or a game was on it (48.9б4): the trial going on was stopped - not failed,
    nothing of it kept."""


def curated() -> list[dict[str, Any]]:
    """The curated list's models (model_catalog.json)."""
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def drafts() -> list[dict[str, Any]]:
    """The draft models for guessing ahead (model_catalog.json `drafts`, 42.3): {id, repo, file, name,
    architectures - the families of models each is tried for, names - only for model files named so}, in the order
    they are tried."""
    return json.loads(CATALOG.read_text(encoding="utf-8")).get("drafts") or []


def picture_sets() -> list[dict[str, Any]]:
    """The curated picture models (model_catalog.json `pictures`, D55): {id, name, purposes, tier, license, original,
    bases - the models whose quantizations and finetunes its flags and other parts fit (47.2), card - false: a family
    found by search only, not a card among «Рисовать» (47.8), size, files: [{role (sd-server's flag), repo, path}],
    flags, about - what it is, not how fast it is here (the verdict beside it says that), checked - run on a real
    card (29, 47.9, 48.8): its verdict is not «по расчёту» (the fix after 0.1.15)}."""
    return json.loads(CATALOG.read_text(encoding="utf-8")).get("pictures") or []


def picture_verdict(entry: dict[str, Any], machine: dict[str, Any]) -> str:
    """How a picture model runs here (hardware.pictures_for): fast | slow - minutes a picture, on the processor or
    an integrated card; a video model (29.8): fast | slow | no - videos here only in the cloud."""
    if entry.get("kind") == "video":
        return {"local": "fast", "slow": "slow"}.get(machine.get("video", ""), "no")
    if entry["tier"] == "light":
        return "fast"
    if entry["tier"] == "quality":
        return "fast" if machine.get("quality") else "slow"
    return "fast" if machine.get("pictures") == "main" else "slow"


def _card_left(model: int, space: dict[str, Any], staging: int | None) -> tuple[bool, float]:
    """Whether there is a graphics card, and what of it, GiB, sd.cpp's auto-fit would have left beside a model of
    `model` bytes with its room for computing and the encoder's staged tensor (`staging`, bytes; None -
    PICTURE_STAGING_GIB). The card as auto-fit sees it at its start (the fix after 0.1.15): less what the desktop and
    other programs hold now (`picture_gib`, room()), not the reserve alone."""
    vram = space.get("picture_gib", space["vram_gib"])
    card = bool(space["vram_gib"] or space.get("vram_total_gib"))
    stage = PICTURE_STAGING_GIB if staging is None else staging / GIB
    return card, max(vram - SD_MARGIN_GIB, 0.0) - model / GIB - PICTURE_COMPUTE_GIB - stage


def picture_fit(model: int, parts: int, space: dict[str, Any], staging: int | None = None) -> str:
    """How a picture model of `model` bytes with its encoders and VAE (`parts`) runs here (47.4, 47.9), as sd.cpp's
    auto-fit places them: gpu - the model with its room for computing on the card (the parts on the card's rest or in
    RAM: they run once a picture); tight - the same with less than TIGHT_GIB to spare (the fix after 0.1.15: a little
    more held by another program and it is split); split - the model in RAM, on the card in pieces: slower - and the
    parts with it, an encoder on the card would want room for the model's pieces too; cpu - no graphics card, all in
    RAM; no - not even the RAM holds them all. Whatever the picture's size: the places are chosen before it is asked.
    `staging` - the encoder's largest tensor (Installer._staging)."""
    card, left = _card_left(model, space, staging)
    if card and left >= 0 and parts / GIB <= left + space["ram_gib"]:
        return "tight" if left < TIGHT_GIB else "gpu"
    if (model + parts) / GIB > space["ram_gib"]:
        return "no"
    return "split" if card else "cpu"


def picture_place(model: int, parts: int, space: dict[str, Any], staging: int | None = None) -> dict[str, Any]:
    """Where a picture model and its parts would be here (the fix after 0.1.15: the words said the whole set fits
    the video memory, and only the model goes on the card): picture_fit's verdict, and bytes - `card`, the model when
    it fits there (gpu, tight) and its encoders and VAE beside it when the card's rest holds them; `ram`, the rest in
    the computer's memory (split, cpu: all; no: all it would want)."""
    verdict = picture_fit(model, parts, space, staging)
    card = 0
    if verdict in ("gpu", "tight"):
        card = model + (parts if parts <= _card_left(model, space, staging)[1] * GIB else 0)
    return {"verdict": verdict, "card": card, "ram": model + parts - card}


def variant_place(files: list[dict[str, Any]], parts: int, space: dict[str, Any],
                  staging: int | None = None) -> dict[str, Any]:  # fmt: skip
    """picture_place of a variant (48.4): one file - the model; a pair's halves - sd.cpp's auto-fit plans the low half
    alone (`model.diffusion_model.`), the high one (`model.high_noise_diffusion_model.`) is the same module's: one on
    the card while it computes - the larger half, the other waiting in RAM with the parts."""
    largest = max(f["size"] for f in files)
    return picture_place(largest, parts + sum(f["size"] for f in files) - largest, space, staging)


def variant_fit(files: list[dict[str, Any]], parts: int, space: dict[str, Any], staging: int | None = None) -> str:
    """variant_place's verdict alone."""
    return variant_place(files, parts, space, staging)["verdict"]


def picture_families(repo: str, bases: list[str], listed: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The picture sets whose family a found repository's models are (47.2): the sets whose main repository it is, or
    the base model its files are quantized or trained from (hf.bases) - the set's encoders, VAE and flags are then its
    too. Several (48.2): unsloth's LTX-2.3 holds the dev model and the distilled one - one base, two families."""
    listed = picture_sets() if listed is None else listed
    own = [e for e in listed if any(f["repo"] == repo and f["role"] in PICTURE_MAIN for f in e["files"])]
    if own:
        return own
    wanted = {b.lower() for b in bases}
    return [e for e in listed if wanted & {b.lower() for b in e.get("bases") or []}]


def picture_family(repo: str, bases: list[str], listed: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The first of picture_families: the set a search row names."""
    return next(iter(picture_families(repo, bases, listed)), None)


def family_variants(entry: dict[str, Any], files: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """A found repository's variants of a family's model (48.2): its files that `names` says (words the path has and
    has not), one file or a pair of halves (hf.picture_variants)."""
    names = entry.get("names") or {}
    return hf.picture_variants([{"path": path, **f} for path, f in files.items()], has=names.get("has") or (),
                               lacks=names.get("lacks") or ())  # fmt: skip


def picture_offer(entry: dict[str, Any]) -> dict[str, Any]:
    """A picture model of picture_catalog() as the first-run wizard offers a model (hub/setup.py suggestions): its
    row installs it by `pictures:<id>`."""
    return {"id": entry["id"], "name": entry["name"], "repo": f"pictures:{entry['id']}", "purposes": entry["purposes"],
            "installed": entry["installed"], "recommended": {"quant": "", "size": entry.get("size", 0),
            "verdict": "gpu" if entry["verdict"] == "fast" else "cpu", "words": entry["words"]}}  # fmt: skip


def this_computer() -> dict[str, Any]:
    """What pictures this computer can make (29.2)."""
    return hardware.pictures_here((memory_sample() or {}).get("ram_total_gib") or 0)


def sd_server(text: str, macro: str = SD_MACRO) -> Path | None:
    """The sd-server a config's `sd_server` macro (or `sd_server_cpu`) names, when it is there."""
    value = llama_config.macros(text).get(macro)
    path = Path(value.strip("'\"")) if value else None
    return path if path is not None and path.is_file() else None


# --- this computer against a model ---


def reserve_saved(conn) -> dict[str, float]:
    """The memory reserves as the user set them (38.3), the defaults for what is not set."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RESERVE_SETTING,)).fetchone()
    saved = json.loads(row["value"]) if row else {}
    return {key: float(saved[key]) if isinstance(saved.get(key), int | float) and not isinstance(saved[key], bool)
            else value for key, value in DEFAULT_RESERVE.items()}  # fmt: skip


def reserve_save(conn, vram_gib: Any, ram_gib: Any) -> dict[str, float]:
    """The user's reserves into the settings (38.3); out of bounds - InstallError in words, nothing written."""
    value = {"vram_gib": vram_gib, "ram_gib": ram_gib}
    words = {"vram_gib": N_("запас видеопамяти: от {low:g} до {high:g} ГБ"),
             "ram_gib": N_("запас ОЗУ: от {low:g} до {high:g} ГБ")}  # fmt: skip
    for key, (low, high) in RESERVE_BOUNDS.items():
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int | float) or not low <= number <= high:
            raise InstallError(tr(words[key], low=low, high=high), 422)
    value = {key: round(float(number), 1) for key, number in value.items()}
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                 "value = excluded.value, updated_at = excluded.updated_at",
                 (RESERVE_SETTING, json.dumps(value), utc_now()))  # fmt: skip
    return value


def room(status: dict[str, Any] | None, reserve: dict[str, float] | None = None,
         others: float | None = None) -> dict[str, Any]:  # fmt: skip
    """The memory a new local model can have, by the computer itself (38.1): its video memory less the desktop's
    share, its RAM less Windows' own - the user's reserves (38.3) or the defaults. Not what is taken right now:
    another program holding memory (Ollama, a game) is a reason to close it, not to judge that a model does not
    fit. But a picture or video model's (`picture_gib`, the fix after 0.1.15): sd.cpp's auto-fit places it by what
    the card has free when it starts - the video memory less what the desktop and other programs hold (`others`,
    GiB: Installer._others), not less than the reserve; 48.8: the desktop held 1,3 GB, the reserve said 1, and LTX
    judged «быстро» went all into RAM."""
    reserve = reserve or DEFAULT_RESERVE
    gpu = (status or {}).get("gpu") or {}
    memory = (status or {}).get("memory") or {}
    loaded = [r.get("model", "") for r in ((status or {}).get("local") or {}).get("running", [])]
    total = (gpu.get("vram_total_mib") or 0) / 1024
    vram = total - reserve["vram_gib"] if total else 0.0
    ram_total = memory.get("ram_total_gib") or 0.0
    ram = ram_total - reserve["ram_gib"]
    # other programs hold the video memory past the desktop's share, by more than BUSY_SLACK_GIB (38.2): words, not
    # a verdict; while llama-swap holds a model its share is not told from theirs - the window says that one unloads
    busy = short_now({"vram_gb": vram - BUSY_SLACK_GIB}, status) if vram > BUSY_SLACK_GIB and not loaded else ""
    picture = total - max(reserve["vram_gib"], others or 0.0) if total else 0.0
    return {"gpu": gpu.get("name") or "", "vram_total_gib": round(total, 1), "vram_gib": round(max(vram, 0.0), 1),
            "ram_total_gib": round(ram_total, 1), "ram_gib": round(max(ram, 0.0), 1), "loaded": loaded,
            "busy": busy, "picture_gib": round(max(picture, 0.0), 1)}  # fmt: skip


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


def fits_params(space: dict[str, Any]) -> int:
    """The most parameters a model of about Q4_K_M (FIT_BITS) may have and still be judged «gpu» here with the context
    a variant is judged with - or, with no graphics card, «cpu» (verdict)."""
    room_gib = space["vram_gib"] - COMPUTE_GIB if space["vram_gib"] else space["ram_gib"]
    return max(int((room_gib - _kv_gib(None, FIT_CONTEXT)) * GIB * 8 / FIT_BITS), 0)


def quality_of(bits: float | None) -> str:
    """How much of the model a variant kept (41.3), for words instead of «bits per weight»: same (about Q8_0 and up),
    near (from about Q4_K_M), less (down to MIN_BITS), worse (below it); '' - its bits are not known."""
    if bits is None:
        return ""
    if bits >= SAME_BITS:
        return "same"
    if bits >= GOOD_BITS:
        return "near"
    return "less" if bits >= MIN_BITS else "worse"


def search_fit(found: dict[str, Any], space: dict[str, Any]) -> dict[str, Any] | None:
    """A repository a search lists, judged against this computer with no more requests (41.1): its count of weights
    as it usually is (about Q4_K_M, FIT_BITS) - an estimate, the open repository judges each variant. None - nobody
    read its count."""
    if not found.get("params"):
        return None
    size = int(found["params"] * FIT_BITS / 8)
    judged = verdict(size, None, space, moe=bool(found.get("moe")))
    return {"gb": round(size / 1e9, 1), "quant": FIT_QUANT, "verdict": judged, "words": tr(VERDICTS[judged])}


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
    cache = "q8_0" if quantized_cache(info) else "f16"  # 0.1.12: a head q8_0's blocks do not divide - f16
    for on_gpu in (True, False) if mmproj else (True,):
        for context in contexts:
            need = size / GIB + _kv_gib(info, context, cache) + COMPUTE_GIB + (mmproj / GIB if on_gpu else 0)
            if space["vram_gib"] and need <= space["vram_gib"]:
                return {"context": context, "gpu_layers": "all", "cache_type": cache, "mmproj_on_gpu": on_gpu,
                        "ttl": 0, "where": "gpu"}  # fmt: skip
    return {"context": min(FIT_CONTEXT, trained), "gpu_layers": "auto", "cache_type": cache,
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


def closed_words(repos: list[str], token: bool) -> str:
    """What to do about the repositories Hugging Face keeps closed (the fix after 0.1.16): where the terms are
    accepted, and - with no token yet - where it goes."""
    pages = ", ".join(f"huggingface.co/{r}" for r in repos)
    if token:
        return tr("Hugging Face не даёт скачать {pages}: ваш ключ туда не пускает — примите условия на этой странице, "
                  "войдя тем же аккаунтом, что и ключ", pages=pages)  # fmt: skip
    return tr("Hugging Face не даёт скачать {pages} без согласия с условиями: войдите на huggingface.co, примите "
              "условия на этой странице и вставьте свой ключ Hugging Face (тип Read) в «Настройки → Подключения»",
              pages=pages)  # fmt: skip


def id_base(repo: str, quant: str) -> str:
    return f"{repo_name(repo)}-{quant}"


BLOB = re.compile(r"sha256-[0-9a-f]{64}")  # how Ollama names a model's file


def local_id(name: str) -> str:
    """A model's id in the config from its name in the list (34.2): Ollama's by its own name
    (`hf.co/IlyaGusev/saiga_nemo_12b-GGUF:Q4_K_M` -> `saiga_nemo_12b-gguf-q4_k_m`, `mistral-nemo:latest` ->
    `mistral-nemo`), not by its file - a blob's `sha256-…`."""
    base = name.rsplit("/", 1)[-1].removesuffix(":latest")
    return re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-") or "model"


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


def sound_of(row: dict[str, Any], sets: list[dict[str, Any]]) -> str:
    """What an install's row says of sound (52.4): "on" - a video model with its audio VAE among its files; "add" -
    one installed without, whose family in the catalog has the file now: «Добавить звук»; "" - nothing to say."""
    plan = row.get("plan") or {}
    if plan.get("engine") != "sd" or plan.get("kind") != "video":
        return ""
    if any(f.get("role") == visual.SOUND_ROLE for f in row.get("files") or []):
        return "on"
    family = next((e for e in sets if e["id"] == plan.get("set")), None)
    can = family is not None and any(f["role"] == visual.SOUND_ROLE for f in family["files"])
    return "add" if can and row.get("status") == "done" else ""


def flags_now(plan: dict[str, Any], sets: list[dict[str, Any]]) -> list[str]:
    """The flags a picture model's block is written with: its family's in the catalog of today and what the install
    added for this computer (`--vae-tiling`); a family the catalog has no more - as it was installed. (52.7 live: a
    block written again - «Добавить звук», «Повторить» - kept the flags of the day of the install, and LTX without
    its later `--flash-attn auto,te=off` filmed one still picture.)"""
    was = list(plan.get("flags") or [])
    family = next((e for e in sets if e["id"] == plan.get("set")), None)
    if family is None:
        return was
    tiles = "--vae-tiling" in was and "--vae-tiling" not in family["flags"]
    return [*family["flags"], *(["--vae-tiling"] if tiles else [])]


def _pictures_root(folder: Path) -> Path:
    """The «pictures» folder a picture install lies in (its own folder is one or two below it: a set's, a found
    repository's): the family's shared files are beside, in `SHARED`."""
    return next((p for p in (folder, *folder.parents) if p.name == "pictures"), folder.parent)


def installed(conn) -> list[dict[str, Any]]:
    """The models in llama-swap's config by AFO - tried, or added and not tried yet (34.5)."""
    return [_row(r) for r in conn.execute("SELECT * FROM model_installs WHERE status IN ('done', 'added') ORDER BY "
                                          "name")]  # fmt: skip


class Installer:
    """The hub's model installs: Hugging Face asked and cached, one download at a time, the config and the trial."""

    def __init__(self, db, bus, gateway, secrets, *, llama_swap_url: str, status: Callable[[], dict[str, Any] | None],
                 drives: Callable[[], list[dict]] = disks.drives, computer: Path | None = None,
                 answers: Path | None = None):  # fmt: skip
        self._computer_file = computer  # which computer this is, between runs (computer.json, 35.2, D58)
        self._computer = kept_json.read(computer) if computer is not None else {}
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self._secrets = secrets
        self._url = llama_swap_url
        self._status = status
        self.reserve: dict[str, float] = dict(DEFAULT_RESERVE)  # the user's memory reserves (38.3): start() reads them
        self.drives = drives  # the disks a model may go to (common/disks.py; tests give their own)
        # the trial run (the hub's «Проверить», hub/api/model_settings.run_check): set by the server
        self.trial: Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]] | None = None
        self.on_done: Callable[[str], Any] | None = None  # a model installed or added: its tuning (15.2)
        # its draft model on the disk (42.5): then its tuning, model id and whether it waits for a quiet computer
        self.on_speed_up: Callable[[str, bool], Any] | None = None
        # a draft on its way: {model_id, looking} (0.1.15 step 6: from the first second) | {model_id, name, done,
        # total} | {model_id, error}
        self.drafting: dict[str, Any] = {}
        self._drafts: dict[str, dict[str, Any]] = {}  # DRAFTS_SETTING
        self._speeding: set[asyncio.Task] = set()
        # 0.1.15 step 6: one «Ускорить» a model at a time (model -> its tuning waits for a quiet computer), one draft's
        # download at a time in the whole hub - a second click downloaded it again into the same file
        self._speedups: dict[str, bool] = {}
        self._draft_lock = asyncio.Lock()
        self._draft_cancel = threading.Event()
        self.on_renamed: Callable[[str, str], Awaitable[None]] | None = None  # a model under a new id (36.1)
        self._naming: asyncio.Task | None = None
        # a trial waits while a local model is asked (48.9а: the card's count - a chat, the editor's completion, an
        # outside client; not a cloud chat) and while the Studio holds the card (its model would be swapped out); it
        # stops tuning (48.9б1: llama-bench runs past llama-swap - two models at once filled the memory); then the card
        # is the trial's to its end; set by the server (34.4, 47.1, stage 48's review)
        self.card = GpuCard()
        # 48.9в: a senior that needs the card stops the trial going on - its question let go (`_asking`); the install
        # is «ждёт пробного запуска» again and waits as background work, for the card still (`_quiet`)
        self._deferring = False
        self._asking: asyncio.Future | None = None
        self._quiet: set[str] = set()
        # 48.9б4: a game on the card while the trial goes on - its answers not taken, no verdict (`_played`, looked at
        # beside it by `_seeing`); put off as by a senior
        self._played: Played | None = None
        self._seeing: asyncio.Task | None = None
        # a picture install choosing its files and a deletion counting who else names a file (47.3): one at a time
        self._files = asyncio.Lock()
        # the engine on its way (39.3: the scanner, the wizard): an install waits for it, not a 409; set by the server
        self.engine_coming: Callable[[], bool] = lambda: False
        self.machine: Callable[[], dict[str, Any]] = this_computer  # what pictures it can make (tests give their own)
        self._task: asyncio.Task | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._wanted: dict[str, str] = {}  # job -> "paused" | "cancelled", what a stopped download becomes
        self._clients: dict[str, Any] = {}  # job -> who started it (the trial is recorded as theirs)
        self._repos: dict[str, tuple[float, dict[str, Any]]] = {}
        self._headers: dict[tuple[str, str], dict[str, Any] | None] = {}
        # the fix after 0.1.15: an encoder file's largest tensor (None - not read), and what the desktop and other
        # programs held of the video memory when llama-swap last held no model
        self._largest: dict[str, int | None] = {}
        self._others_gib: float | None = None
        # the fix after 0.1.16: (a gated repository, the token's mark) -> (when, whether Hugging Face gives its files)
        self._readable: dict[tuple[str, str], tuple[float, bool]] = {}
        self._answers_file = answers  # what Hugging Face said, between runs (hf_answers.json, 35.3, D58)
        self._answers_new = False  # something to write
        self._answers_lock = asyncio.Lock()
        self._renewing: set[str] = set()  # answers older than REPO_TTL_S on their way in the background
        self._load_answers()
        self._shown: dict[str, tuple[float, int]] = {}  # job -> (when, bytes) of the last progress sent
        self._sending: set[asyncio.Task] = set()  # progress on its way (kept: a task nobody holds may vanish)
        self.speed: dict[str, float] = {}  # job -> bytes a second
        # the fix after 0.1.16: job -> where the picture engine's archives end, while they come from GitHub - its speed
        # is not Hugging Face's, the time said is theirs
        self._engine_end: dict[str, int] = {}

    async def start(self) -> int:
        """Installs a stopped hub left going wait for «Продолжить» (their parts stay on disk); a model from the drives
        whose trial it left waiting or going is «добавлена, не проверена» again (34.5) - it is in the config."""

        def pause(conn) -> int:
            conn.execute("UPDATE model_installs SET status = 'added', updated_at = ? WHERE own_files = 0 AND status IN "
                         "('trialwait', 'trial')", (utc_now(),))  # fmt: skip
            return conn.execute(
                "UPDATE model_installs SET status = 'paused', error = ?, updated_at = ? WHERE status IN "
                "('queued', 'downloading', 'trialwait', 'configuring', 'trial')",
                (tr("хаб перезапускался — загрузка продолжится по кнопке «Продолжить»"), utc_now()),
            ).rowcount

        await self.load_reserve()
        self._drafts = await self._db.acall(lambda conn: _setting(conn, DRAFTS_SETTING))
        left = await self._db.acall(pause)
        self._naming = asyncio.create_task(self.name_ollama_models())  # in the background: llama-swap may not be up
        self._keep(self._quietly(self.unhook_lost_drafts()))  # a draft's file gone while the hub was off (42.5)
        self._keep(self._quietly(self.fit_to_build()))  # a flag the build took away (0.1.12)
        return left

    async def load_reserve(self) -> None:
        self.reserve = await self._db.acall(reserve_saved)

    async def set_reserve(self, vram_gib: Any, ram_gib: Any) -> dict[str, Any]:
        """The user's memory reserves (38.3): saved, then judged by from the next look on."""
        self.reserve = await self._db.acall(lambda conn: reserve_save(conn, vram_gib, ram_gib))
        return self.reserve_view()

    def reserve_view(self) -> dict[str, Any]:
        return {**self.reserve, "default": dict(DEFAULT_RESERVE)}

    async def name_ollama_models(self) -> list[tuple[str, str]]:
        """Models of Ollama's added under the file's name (`sha256-…`; before 34.2, and «Вернуть в llama-swap» brought
        them back so): each gets Ollama's own name - in the config, with a copy of it beside, in its install and in
        all that names it (36.1). [(old id, new id)]; one Ollama does not know keeps what it has."""
        rows = await self._db.acall(lambda conn: [r for r in jobs(conn) if BLOB.fullmatch(r["model_id"])
                                                  and not r["own_files"] and r["status"] not in LIVE])  # fmt: skip
        names = await asyncio.to_thread(ollama_names) if rows else {}

        def file_of(row: dict[str, Any]) -> str:  # as ollama_names() has it
            return os.path.normcase(os.path.normpath(Path(row["folder"]) / row["files"][0]["path"]))

        wanted = [(r, names[file_of(r)]) for r in rows if file_of(r) in names]
        if not wanted:
            return []
        path = llama_config.config_path()
        new_ids: list[tuple[dict[str, Any], str, str]] = []
        async with llama_config.writing():
            text = new = await asyncio.to_thread(config_text)
            for row, name in wanted:
                model_id = llama_config.free_id(new, local_id(name))
                try:
                    if row["model_id"] in llama_config.model_blocks(new):
                        new = llama_config.rename_model(new, row["model_id"], model_id, name)
                except llama_config.ConfigError as exc:
                    log.info("model %s keeps its id: %s", row["model_id"], exc)
                    continue
                new_ids.append((row, model_id, name))
            if new != text:
                backup = await asyncio.to_thread(llama_config.write, path, new)
                ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
                if not ok and problem != tr(llama_config.NOT_ANSWERING):
                    await asyncio.to_thread(llama_config.restore, path, backup)
                    log.warning("the models' own names: llama-swap did not take the config (%s)", problem)
                    return []

        def apply(conn) -> None:
            for row, model_id, name in new_ids:
                _update(conn, row["id"], model_id=model_id, name=name)
                store.rename_model_references(conn, f"llama-swap/{row['model_id']}", f"llama-swap/{model_id}")

        await self._db.acall(apply)
        for row, model_id, _name in new_ids:
            if self.on_renamed is not None:
                await self.on_renamed(row["model_id"], model_id)
            await self._publish(row["id"])
        await self._sync()
        await self._bus.publish("llama.config", {"renamed": len(new_ids)})
        log.info("models named by Ollama: %s", [model_id for _row, model_id, _name in new_ids])
        return [(row["model_id"], model_id) for row, model_id, _name in new_ids]

    def busy(self) -> bool:
        """The installs have work: their worker goes on (34.4 - «Подбор параметров» waits for it)."""
        return self._task is not None and not self._task.done()

    async def missing(self, model_id: str) -> str:
        """The first file a model's install names that is not on the disk whole, by its name - or "" (48's review,
        finding 8): a pair's high half deleted by hand, sd.cpp only warns and the low one films alone, «готово»."""
        found = next((r for r in await self._db.acall(installed) if r["model_id"] == model_id), None)
        if found is None:
            return ""
        folder = Path(found["folder"])

        def look() -> str:
            for f in found["files"]:
                if f["role"] != "engine" and not _whole(_dest(folder, f), f["size"]):
                    return PurePosixPath(f["path"]).name
            return ""

        return await asyncio.to_thread(look)

    async def mark_lost(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each install whose block should be in llama-swap's config says whether it is not (34.4: tuning wrote over
        the file, and three models added meanwhile were gone - their rows said «установлена»)."""
        try:
            ids = llama_config.model_blocks(await asyncio.to_thread(config_text))
        except InstallError, llama_config.ConfigError:
            ids = None  # no config to read: nothing to say
        for row in rows:
            row["lost"] = ids is not None and row["status"] in IN_CONFIG and row["model_id"] not in ids
        return rows

    async def stop(self) -> None:
        if self._naming is not None:
            self._naming.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._naming
        self._draft_cancel.set()  # a draft's download: its part stays, the next «Ускорить» goes on with it
        for task in list(self._speeding):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
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

    def _load_answers(self) -> None:
        """hf_answers.json into memory, with how old each answer is; anything odd in it is left out: only a memory - an
        answer that does not say whether Hugging Face hid its sums too (kept before the fix after 0.1.17: they may be
        its asterisks, taken for sums)."""
        kept = kept_json.read(self._answers_file) if self._answers_file is not None else {}
        shift = time.time() - time.monotonic()  # the file's clock against this run's
        with contextlib.suppress(AttributeError, KeyError, TypeError):
            repos = {key: (entry["at"] - shift, entry["found"]) for key, entry in kept.get("repos", {}).items()
                     if "hidden" in entry["found"]}  # fmt: skip
            headers = {(entry["repo"], entry["path"]): entry["info"] for entry in kept.get("headers", [])}
            self._repos, self._headers = repos, headers

    async def _keep_answers(self) -> None:
        """What Hugging Face said, on the disk: the next start has it at once (35.3, D58)."""
        if self._answers_file is None:
            return
        async with self._answers_lock:
            if not self._answers_new:
                return
            self._answers_new = False
            shift = time.time() - time.monotonic()
            data = {"repos": {key: {"at": when + shift, "found": found} for key, (when, found) in self._repos.items()},
                    "headers": [{"repo": repo, "path": path, "info": info}
                                for (repo, path), info in self._headers.items()]}  # fmt: skip
            with contextlib.suppress(OSError):
                await asyncio.to_thread(kept_json.write, self._answers_file, data)

    def _renew(self, key: str, answer: Coroutine[Any, Any, Any]) -> None:
        """An answer older than REPO_TTL_S was given: Hugging Face's new one comes for the next look (35.3)."""
        if key in self._renewing:
            answer.close()
            return
        self._renewing.add(key)

        async def renew() -> None:
            try:
                await answer
                await self._keep_answers()
            except Exception as exc:  # no network: the old answer stays, the next look tries again
                log.info("Hugging Face's newer answer on %s: %s", key, exc)
            finally:
                self._renewing.discard(key)

        task = asyncio.create_task(renew())
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def repository(self, repo: str, *, fresh: bool = False) -> dict[str, Any]:
        kept = self._repos.get(repo)
        if kept and not fresh:
            if time.monotonic() - kept[0] >= REPO_TTL_S:
                self._renew(repo, self._ask_repository(repo))
            return kept[1]
        return await self._ask_repository(repo)

    async def _ask_repository(self, repo: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as http:
            found = await hf.repository(http, repo, self.token())
        self._repos[repo] = (time.monotonic(), found)
        self._answers_new = True
        return found

    async def header(self, repo: str, path: str) -> dict[str, Any] | None:
        """A model's attention shape from its first bytes; None when it cannot be read (the estimate guesses)."""
        if (repo, path) not in self._headers:
            try:
                self._headers[repo, path] = await asyncio.to_thread(hf.header, repo, path, self.token())
            except (hf.HubError, GGUFError) as exc:
                log.info("no header of %s/%s: %s", repo, path, exc)
                return None  # not kept: the next look tries again
            self._answers_new = True
        return self._headers[repo, path]

    async def drafts_for(self, model_file: Path, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """The catalog's draft models for this model (42.3): DRAFT_RATIO times smaller at least, its vocabulary the
        model's - read from the first bytes of its file on Hugging Face, before any download; with its file as Hugging
        Face has it now (size, sha256, commit). Those made for this one model (44.4: DFlash, EAGLE-3; 44.8: an MTP
        head - the catalog's `kind`, the model's files its `names`) whose kind the build knows (`kinds`:
        llama-server's --spec-type; None - any) - each one, the tuning measures them all; none such - the first of its
        family that fits. [] - none fits; a repository that does not answer is passed over."""
        info = await asyncio.to_thread(model_info, model_file)
        vocabulary: list[dict[str, Any]] = []  # the model's own, read once when a draft needs it
        made: list[dict[str, Any]] = []
        for entry in sorted(drafts(), key=lambda d: (d.get("kind") or "simple") == "simple"):
            entry = {**entry, "kind": entry.get("kind") or "simple"}
            if made and entry["kind"] == "simple":  # those made for it: the family's is not looked at
                break
            if info.get("architecture") not in entry["architectures"]:
                continue
            if kinds is not None and f"draft-{entry['kind']}" not in kinds:
                continue
            if entry.get("names") and not re.search(entry["names"], model_file.name):  # a coder's draft for a coder
                continue
            found = await self._draft_fits(entry, info, model_file, vocabulary)
            if found is not None and entry["kind"] == "simple":
                return [found]
            made += [found] if found is not None else []
        return made

    async def _draft_fits(self, entry: dict[str, Any], info: dict[str, Any], model_file: Path,
                          vocabulary: list[dict[str, Any]]) -> dict[str, Any] | None:  # fmt: skip
        """A catalog's draft with its file as Hugging Face has it now - when it is DRAFT_RATIO times smaller than the
        model and its vocabulary the model's (42.3); None - not."""
        try:
            listed = await self._listed(entry["repo"], sums=True)  # the download checks its sum (_download_draft)
        except hf.HubError as exc:
            log.info("no draft %s: %s", entry["repo"], exc)
            return None
        file = listed["files"].get(entry["file"])
        if file is None or file["size"] * DRAFT_RATIO > info["size_bytes"]:
            return None
        if not vocabulary:
            vocabulary.append(await asyncio.to_thread(tokenizer_info, model_file))
        try:
            theirs = await asyncio.to_thread(hf.tokenizer, entry["repo"], entry["file"], self.token(),
                                             listed["revision"])  # fmt: skip
        except (hf.HubError, GGUFError) as exc:
            log.info("no vocabulary of %s/%s: %s", entry["repo"], entry["file"], exc)
            return None
        if not same_vocabulary(vocabulary[0], theirs):
            return None
        return {**entry, "size": file["size"], "sha256": file["sha256"], "revision": listed["revision"]}

    # --- the draft model guessing ahead (42.5) ---

    def _keep(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._speeding.add(task)  # kept: a task nobody holds may vanish
        task.add_done_callback(self._speeding.discard)
        return task

    async def _quietly(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        try:
            await coroutine
        except (InstallError, llama_config.ConfigError, OSError) as exc:
            log.info("drafts: %s", exc)

    def speed_up(self, model_id: str, automatic: bool = False) -> dict[str, Any]:
        """«Ускорить» (42.5), and by itself after an install: the draft model paired with this one onto the disk (once:
        it serves its whole family), then the model's tuning measures guessing ahead with it - at once, or when the
        computer is quiet (`automatic`). In the background; what goes on - `drafting`. Once a model's goes on, another
        call starts nothing (0.1.15 step 6) - the person's over an automatic one makes its tuning go at once."""
        if model_id in self._speedups:
            self._speedups[model_id] = self._speedups[model_id] and automatic
            return {"model_id": model_id, "drafting": self.drafting}
        self._speedups[model_id] = automatic
        self.drafting = {"model_id": model_id, "looking": True}
        self._keep(self._speed_up(model_id))
        return {"model_id": model_id, "drafting": self.drafting}

    async def _speed_up(self, model_id: str) -> None:
        try:
            await self._bus.publish("install.draft", {"drafting": self.drafting})
            await self._fetch_draft(model_id)
        except (InstallError, hf.HubError, hf.Cancelled, llama_config.ConfigError, OSError, GGUFError) as exc:
            log.info("no draft model for %s: %s", model_id, exc)
            self.drafting = {"model_id": model_id, "error": str(exc) or tr("загрузка остановлена")}
            await self._bus.publish("install.draft", {"drafting": self.drafting})
        finally:
            automatic = self._speedups.pop(model_id, False)
            if self.drafting == {"model_id": model_id, "looking": True}:  # nothing to download: nothing on its way
                self.drafting = {}
                await self._bus.publish("install.draft", {"drafting": self.drafting})
        if self.on_speed_up is not None:
            self.on_speed_up(model_id, automatic)

    async def _fetch_draft(self, model_id: str) -> None:
        """The catalog's drafts paired with this model on the disk (drafts_for; 44.8: each of those made for it): one
        AFO has is taken again, another waits for the installs' downloads (one at a time) and comes to the fastest
        drive with room, its sha256 checked; the model waits for its tuning to measure it. Nothing without a graphics
        card (a draft only slows the processor down - ngram is measured) or for a model not llama.cpp's."""
        if not self.space().get("vram_gib"):
            return
        text = await asyncio.to_thread(config_text)
        view = llama_config.model_view(text, model_id)
        if view["engine"] != "llama.cpp" or not view.get("file"):
            return
        build = await asyncio.to_thread(llama_config.server_flags, server_path(text))
        for entry in await self.drafts_for(Path(view["file"]), build["spec_types"] if build else None):  # 44.4
            await self._fetch_one_draft(model_id, entry)

    async def _fetch_one_draft(self, model_id: str, entry: dict[str, Any]) -> None:
        same = (entry["repo"], entry["file"])
        async with self._draft_lock:  # one at a time (0.1.15 step 6): the one before may have brought this one
            dest = next((Path(file) for file, d in self._drafts.items()
                         if (d["repo"], d["file"]) == same and Path(file).is_file()), None)  # fmt: skip
            if dest is None:
                while self.busy():
                    await asyncio.sleep(ENGINE_WAIT_S)
                drive = pick_drive(await asyncio.to_thread(self.drives), entry["size"] / GIB)
                if drive is None:
                    raise InstallError(tr("нет места для черновой модели: нужно {size:.1f} ГБ",
                                          size=entry["size"] / GIB + DISK_MARGIN_GIB), 507)  # fmt: skip
                dest = models_root(drive) / "drafts" / PurePosixPath(entry["file"]).name
                await self._download_draft(model_id, entry, dest)
            record = self._drafts.get(str(dest)) or {key: entry[key] for key in ("repo", "file", "sha256", "size")}
            record["waiting"] = sorted({*record.get("waiting", []), model_id})
            self._drafts[str(dest)] = record
            await self._save_drafts()

    async def _download_draft(self, model_id: str, entry: dict[str, Any], dest: Path) -> None:
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_check_room, dest.parent, entry["size"])
        coming = {"model_id": model_id, "name": entry["name"], "done": 0, "total": entry["size"]}
        self.drafting = coming
        await self._bus.publish("install.draft", {"drafting": self.drafting})
        loop, shown = asyncio.get_running_loop(), [0.0]

        def report(done: int) -> None:  # in the download's thread; its own - `drafting` may be another's by now
            coming["done"] = done
            if time.monotonic() - shown[0] >= PROGRESS_S:
                shown[0] = time.monotonic()
                said = self._bus.publish("install.draft", {"drafting": dict(coming)}, persist=False)
                asyncio.run_coroutine_threadsafe(said, loop)

        self._draft_cancel.clear()
        url, progress = hf.file_url(entry["repo"], entry["file"], entry["revision"]), hf.Progress()
        for attempt in range(len(RETRIES_S) + 1):
            try:
                await asyncio.to_thread(hf.download, url, dest, size=entry["size"], sha256=entry["sha256"],
                                        token=self.token(), report=report, cancel=self._draft_cancel,
                                        progress=progress)  # fmt: skip
                break
            except hf.HubError as exc:
                if exc.status in (403, 404, 429) or attempt == len(RETRIES_S):
                    raise
                if await asyncio.to_thread(self._draft_cancel.wait, RETRIES_S[attempt]):
                    raise hf.Cancelled() from exc
        self.drafting = {}
        await self._bus.publish("install.draft", {"drafting": self.drafting})

    async def _save_drafts(self) -> None:
        drafts = json.loads(json.dumps(self._drafts))
        await self._db.acall(lambda conn: _put_setting(conn, DRAFTS_SETTING, drafts))

    async def draft_paths(self, model_id: str, model_file: Path) -> list[Path]:
        """The drafts downloaded for this model whose tuning has not measured them yet (42.5; 44.8: each one) - the
        tuner's `drafts`."""
        return [Path(file) for file, d in self._drafts.items()
                if model_id in d.get("waiting", []) and Path(file).is_file()]  # fmt: skip

    async def tuned(self, model_id: str, result: dict[str, Any]) -> None:
        """A model's tuning ended (42.5): it waits for its draft no more. A draft no model's command uses and none
        waits for gave nothing - it is deleted; a draft's file gone from the disk is taken off each command."""
        for record in self._drafts.values():
            if model_id in record.get("waiting", []):
                record["waiting"].remove(model_id)
        await self.prune_drafts()

    async def prune_drafts(self) -> None:
        """Each downloaded draft no model's command uses and none waits for - deleted (a file in use stays for the
        next time); a draft's file gone - off the commands (unhook_lost_drafts)."""
        try:
            text = await asyncio.to_thread(config_text)
        except InstallError:
            return
        used = {Path(v["spec"]["draft"]) for v in _views(text) if v.get("spec", {}).get("draft")}
        for file, record in list(self._drafts.items()):
            if record.get("waiting") or Path(file) in used:
                continue
            try:
                await asyncio.to_thread(_delete_draft, Path(file))
            except OSError as exc:  # llama-server still holds it (Windows): the next time
                log.info("the draft %s stays for now: %s", file, exc)
                continue
            del self._drafts[file]
        await self._save_drafts()
        await self._quietly(self.unhook_lost_drafts())

    async def unhook_lost_drafts(self) -> list[str]:
        """Each command whose draft model's file is gone (deleted, an antivirus) without its guessing ahead - llama-swap
        would not start the model with it (the stage's risk, 42.5); under the writers' lock. The models changed."""
        path = llama_config.config_path()
        async with llama_config.writing():
            text = await asyncio.to_thread(config_text)
            new, changed = await asyncio.to_thread(_without_lost_drafts, text)
            if not changed:
                return []
            backup = await asyncio.to_thread(llama_config.write, path, new)
            ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
            if not ok and problem != tr(llama_config.NOT_ANSWERING):
                await asyncio.to_thread(llama_config.restore, path, backup)
                raise InstallError(tr("llama-swap не принял правку ({problem}) — конфиг возвращён",
                                      problem=problem), 502)  # fmt: skip
        log.info("draft models gone from the disk: guessing ahead off for %s", changed)
        await self._bus.publish("llama.config", {"changes": {"spec_type": "none"}, "models": changed})
        return changed

    async def fit_to_build(self) -> dict[str, list[str]]:
        """The commands as the config's llama-server takes them (llama_config.match_build), at the hub's start too
        (0.1.12: the person's 0.1.11 kept «--no-mmap», which b11899 had taken away, and the model did not start): under
        the writers' lock, a copy kept; llama-swap does not take it - the copy back. {model: the flags it did not take};
        {} - nothing to change, or no config or build to read."""
        path = llama_config.config_path()
        try:
            text = await asyncio.to_thread(config_text)
            build = await asyncio.to_thread(llama_config.server_flags, server_path(text))
        except (InstallError, llama_config.ConfigError, OSError) as exc:
            log.info("commands not fitted to the build: %s", exc)
            return {}
        if build is None:
            return {}
        async with llama_config.writing():
            text = await asyncio.to_thread(config_text)
            found = llama_config.unknown_flags(text, build)
            new = llama_config.match_build(text, build)
            if new == text:
                return {}
            backup = await asyncio.to_thread(llama_config.write, path, new)
            ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
            if not ok and problem != tr(llama_config.NOT_ANSWERING):
                await asyncio.to_thread(llama_config.restore, path, backup)
                raise InstallError(tr("llama-swap не принял правку ({problem}) — конфиг возвращён",
                                      problem=problem), 502)  # fmt: skip
        log.info("commands fitted to the build (copy %s): %s", backup.name, found)
        await self._bus.publish("llama.config", {"fitted": found, "backup": backup.name})
        return found

    def space(self) -> dict[str, Any]:
        status = self._status() or self._kept_machine()
        return room(status, self.reserve, self._others(status))

    def _others(self, status: dict[str, Any] | None) -> float | None:
        """What the desktop and other programs hold of the video memory (the fix after 0.1.15), GiB: measured while
        llama-swap holds no model - its own would be counted with theirs, and it lets them go for a picture model -
        and kept for when it does; None - not measured yet."""
        used = ((status or {}).get("gpu") or {}).get("vram_used_mib")
        if used is not None and not ((status or {}).get("local") or {}).get("running"):
            self._others_gib = used / 1024
        return self._others_gib

    async def _staging(self, parts: list[dict[str, Any]]) -> int | None:
        """The largest tensor of a picture model's encoders, bytes (the fix after 0.1.15): sd.cpp stages it on the card
        beside the model - read from each file's header on Hugging Face once for the hub's life; None - none read
        (picture_fit takes PICTURE_STAGING_GIB)."""
        found = []
        for f in parts:
            if f["role"] in (*PICTURE_MAIN, "vae"):
                continue
            key = f"{f['repo']}/{f['path']}@{f.get('revision') or 'main'}"
            if key not in self._largest:
                try:
                    self._largest[key] = await asyncio.to_thread(hf.largest_tensor, f["repo"], f["path"], self.token(),
                                                                 f.get("revision") or "main") or None  # fmt: skip
                except hf.HubError, ValueError, KeyError, TypeError, IndexError:
                    self._largest[key] = None  # not a header AFO reads: the usual half a gigabyte
            if self._largest[key]:
                found.append(self._largest[key])
        return max(found) if found else None

    def _kept_machine(self) -> dict[str, Any] | None:
        """computer.json as a sample (35.2): a hub just started, before its first look at the machine - judged as
        by a sample, room() counts only what the computer has (38.1)."""
        kept = self._computer
        if not kept.get("ram_total_gib") and not kept.get("vram_total_gib"):
            return None
        gpu = {"name": kept.get("gpu") or "", "vram_total_mib": (kept.get("vram_total_gib") or 0) * 1024}
        return {"gpu": gpu, "memory": {"ram_total_gib": kept.get("ram_total_gib") or 0.0}}

    async def memory_now(self, provider: str, model_id: str) -> str:
        """Words for a local model starting to load when other programs hold the memory it needs (38.2), by the last
        sample; '' when enough is free - or when it cannot be told: no sample, the model or one of llama-swap's
        loaded ones not estimated (not llama.cpp's, its file not read), the model already among them."""
        status = self._status()
        if provider != "llama-swap" or not status:
            return ""
        running = [r.get("model", "") for r in ((status.get("local") or {}).get("running") or [])]
        if model_id in running:
            return ""
        total = (status.get("gpu") or {}).get("vram_total_mib")
        # read under the writers' lock: Windows refuses a write's os.replace while the file is open for reading -
        # and while the config is being changed, nothing is said rather than a read in its way
        lock = llama_config.writing()
        if lock.locked():
            return ""
        try:
            async with lock:
                text = await asyncio.to_thread(config_text)
        except InstallError:
            return ""

        def judged() -> tuple[dict[str, Any] | None, list[dict[str, Any] | None]]:
            return _estimated(text, model_id, total), [_estimated(text, other, total) for other in running]

        need, held = await asyncio.to_thread(judged)
        if need is None or any(h is None for h in held):
            return ""
        own = {"vram_gb": sum(h["vram_gb"] for h in held), "ram_gb": sum(h["ram_gb"] for h in held)}
        return short_now(need, status, own)

    def note_machine(self, machine: dict[str, Any]) -> None:
        """The card and the memory as sampled: into computer.json when they are others than it says (35.2, D58) -
        what changes by the minute is not kept."""
        space = room(machine)
        known = {key: space[key] for key in ("gpu", "vram_total_gib", "ram_total_gib")}
        if self._computer_file is None or not (known["vram_total_gib"] or known["ram_total_gib"]):
            return  # nothing sampled: what is known stays
        if {key: self._computer.get(key) for key in known} != known:
            self._computer = {**known, "at": utc_now()}
            with contextlib.suppress(OSError):
                kept_json.write(self._computer_file, self._computer)

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
        moe = bool((entry or {}).get("moe")) or bool(found.get("moe"))  # its architecture, tags or name (41.1)
        judged = {v["quant"]: verdict(v["size"], info, space, mmproj=lens["size"] if lens else 0, moe=moe)
                  for v in variants}  # fmt: skip
        best = recommend(variants, judged, moe=moe)
        ends = extremes(variants, judged)
        shown = [{**v, "verdict": judged[v["quant"]], "words": tr(VERDICTS[judged[v["quant"]]]),
                  "quality": quality_of(v.get("bits")),
                  "gb": round(v["size"] / 1e9, 1), "recommended": v["quant"] == best,
                  "pick": "fast" if v["quant"] == ends["fast"] and ends["fast"] != best else
                  "quality" if v["quant"] == ends["quality"] and ends["quality"] != best else ""}
                 for v in variants]  # fmt: skip
        await self._keep_answers()
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

    async def search(self, query: str, *, task: str = "", size: str = "", sort: str = "downloads",
                     plain: bool = False, **more: str) -> list[dict[str, Any]]:  # fmt: skip
        """Hugging Face at large (32.5): the words, what for (hf.TASKS), how big (SIZES, or "fits": what this computer
        holds), in what order (hf.SORTS), and `plain` - none whose refusals were taken out; `more` (41.2): language,
        author, build, licence (hf.search). Each found says whether it fits this computer (41.1); a model that draws
        or films - which picture set of AFO's it is (`picture`, its main repository), or none: AFO cannot put it
        together itself; and (47.2) whose family it is (`family`: the set by its repository or by the base model the
        files are made of - the other parts, encoders and VAE, are the set's)."""
        fewest, most = (0, fits_params(self.space())) if size == "fits" else SIZES.get(size, (0, 0))
        if size == "fits" and not most:
            return []  # nothing fits here
        async with httpx.AsyncClient(timeout=30.0) as http:
            found = await hf.search(http, query, token=self.token(), task=task, fewest=fewest, most=most, sort=sort,
                                    plain=plain, **more)  # fmt: skip
        space = self.space()
        listed = picture_sets()
        sets = {f["repo"]: entry["id"] for entry in listed for f in entry["files"] if f["role"] in PICTURE_MAIN}
        out = []
        for each in found:
            picture = sets.get(each["repo"], "")
            family = (picture_family(each["repo"], each["bases"], listed) or {}).get("id", "")
            drawn = bool(family) or task in hf.PICTURE_TASKS or each.get("pipeline") in hf.PICTURE_PIPELINES
            out.append({**each, "picture": picture, "family": family, "drawn": drawn,
                        "fit": None if drawn else search_fit(each, space)})  # fmt: skip
        await self._picture_fits(out, listed, space)
        return out

    async def _picture_fits(self, out: list[dict[str, Any]], listed: list[dict[str, Any]],
                            space: dict[str, Any]) -> None:  # fmt: skip
        """Beside each found model of a known family (47.7а): what it would take here - the model as it is usually taken
        (FIT_QUANT, by its parameter count) with the family's encoders and VAE (`parts`, bytes), placed as sd.cpp places
        them (picture_place: on the card, in the computer's memory), what of it the disk lacks (`download`: the parts
        already there for another model are not downloaded again, 47.3), and whether the family was run here for real
        (`checked`, the catalog's); a family whose parts Hugging Face did not list - none."""
        sets = {e["id"]: e for e in listed}
        wanted = list(dict.fromkeys(o["family"] for o in out if o["family"] and o.get("params")))
        named = await self._db.acall(_named) if wanted else []

        async def parts(entry: dict[str, Any]) -> tuple[int, int, int | None] | None:
            try:
                files = await self.picture_files(
                    {**entry, "files": [f for f in entry["files"] if f["role"] not in PICTURE_MAIN]})  # fmt: skip
            except hf.HubError, InstallError:
                return None
            await asyncio.to_thread(_share, files, named, models_home() / "pictures" / SHARED)
            return (sum(f["size"] for f in files), sum(f["size"] for f in files if not f["done"]),
                    await self._staging(files))  # fmt: skip

        sizes = dict(zip(wanted, await asyncio.gather(*(parts(sets[f]) for f in wanted)), strict=True))
        for o in out:
            found = sizes.get(o["family"])
            if found is None or not o.get("params"):
                continue
            (rest, coming, staging), model = found, int(o["params"] * FIT_BITS / 8)
            place = picture_place(model, rest, space, staging)
            o["fit"] = {"gb": round((model + rest) / 1e9, 1), "quant": FIT_QUANT, **place,
                        "words": tr(PICTURE_VERDICTS[place["verdict"]]), "parts": rest, "download": model + coming,
                        "room": int(space["ram_gib"] * GIB),
                        "checked": bool(sets[o["family"]].get("checked"))}  # fmt: skip
        if wanted:
            await self._keep_answers()

    # --- picture models (29.3) ---

    async def _listed(self, repo: str, *, sums: bool = False) -> dict[str, Any]:
        """A repository's files as Hugging Face listed them (hf.files), kept; `sums` - their sha256 count: an answer
        that hid them (given before the token came, the fix after 0.1.17) is asked again."""
        key = f"files:{repo}"
        kept = self._repos.get(key)
        if kept and not (sums and kept[1].get("hidden", True)):
            if time.monotonic() - kept[0] >= REPO_TTL_S:
                self._renew(key, self._ask_files(repo))
            return kept[1]
        return await self._ask_files(repo)

    async def _ask_files(self, repo: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as http:
            found = await hf.files(http, repo, self.token())
        self._repos[f"files:{repo}"] = (time.monotonic(), found)
        self._answers_new = True
        return found

    async def picture_files(self, entry: dict[str, Any], *, sums: bool = False) -> list[dict[str, Any]]:
        """A picture model's files as Hugging Face has them now: each with its repository, commit, size, sha256
        (`sums` - known where Hugging Face lets this token see them: _listed)."""
        repos = dict.fromkeys(f["repo"] for f in entry["files"])
        found = {repo: await self._listed(repo, sums=sums) for repo in repos}
        out = []
        for f in entry["files"]:
            listed = found[f["repo"]]["files"].get(f["path"])
            if listed is None:
                raise InstallError(tr("в {repo} нет файла {path}", repo=f["repo"], path=f["path"]), 404)
            out.append({"path": f["path"], "repo": f["repo"], "revision": found[f["repo"]]["revision"], "done": False,
                        "size": listed["size"], "sha256": listed["sha256"], "role": f["role"]})  # fmt: skip
        return out

    async def _closed(self, files: list[dict[str, Any]], *, fresh: bool = False) -> list[str]:
        """The repositories of these files Hugging Face keeps closed to this token - or to no token (the fix after
        0.1.16: LTX-2.5's encoder lies in a gated repository; the install stopped on it after the engine and the
        model). Only a gated one is asked (its info says so), by its first file's first byte. A «closed» is kept
        CLOSED_TTL_S, an «open» - REPO_TTL_S, each for its token; `fresh` - an install about to start: asked again."""
        token = self.token()
        mark = hashlib.sha256((token or "").encode()).hexdigest()[:16]  # not the token itself, kept in memory
        out = []
        for repo in dict.fromkeys(f["repo"] for f in files if f.get("repo")):
            if not (await self._listed(repo)).get("gated"):
                continue
            kept = self._readable.get((repo, mark))
            if kept and (kept[1] or not fresh) and time.monotonic() - kept[0] < (REPO_TTL_S if kept[1] else
                                                                                 CLOSED_TTL_S):  # fmt: skip
                readable = kept[1]
            else:
                first = next(f for f in files if f.get("repo") == repo)
                async with httpx.AsyncClient(timeout=30.0) as http:
                    readable = await hf.readable(http, repo, first["path"], token, first.get("revision") or "main")
                self._readable[(repo, mark)] = (time.monotonic(), readable)
            if not readable:
                out.append(repo)
        return out

    async def picture_catalog(self) -> list[dict[str, Any]]:
        """The curated picture models, each with its size and how it runs here."""
        machine = await asyncio.to_thread(self.machine)
        mine = {r["plan"].get("set"): r for r in await self._db.acall(installed) if not r["plan"].get("found")}
        listed = []
        for entry in (e for e in picture_sets() if e.get("card", True)):  # a family found by search only: no card
            verdict = picture_verdict(entry, machine)
            words = {"fast": tr("быстро"), "slow": tr("медленно — минуты на картинку"),
                     "no": tr("видео на этом компьютере — только в облаке")}  # fmt: skip
            if entry.get("kind") == "video" and verdict == "slow":
                words["slow"] = tr("медленно — минуты на секунду видео")
            shown = {**entry, "kind": entry.get("kind", "image"), "verdict": verdict, "words": words[verdict],
                     "purpose_words": [tr(PURPOSES[p]) for p in entry["purposes"]],
                     "recommended": entry["tier"] == ("main" if machine.get("pictures") == "main" else "light"),
                     "installed": [mine[entry["id"]]["model_id"]] if entry["id"] in mine else []}  # fmt: skip
            try:
                size = sum(f["size"] for f in await self.picture_files(entry))
                shown.update(size=size, gb=round(size / 1e9, 1))
            except (hf.HubError, InstallError) as exc:
                shown["error"] = str(exc)
            listed.append(shown)
        await self._keep_answers()
        return listed

    async def picture_details(self, repo: str) -> dict[str, Any]:
        """A found model that draws (47.5): its family's set (picture_family), its variants - each judged here with the
        set's encoders and VAE (picture_fit), with them (`with_parts`) and with what is not on the disk yet
        (`coming`, 47.3) - the one recommended, the parts and whether each is here, whether the engine comes too."""
        found = await self._listed(repo)
        families = picture_families(repo, found.get("bases") or [])
        if not families:
            raise InstallError(tr("AFO пока не умеет ставить {repo} сам: семейства этой модели нет в его списке",
                                  repo=repo), 404)  # fmt: skip
        named = await self._db.acall(_named)
        space, params = self.space(), found.get("params") or 0
        shown: list[dict[str, Any]] = []
        parts_of: dict[str, list[dict[str, Any]]] = {}
        for entry in families:  # 48.2: each family's model of the repository, with that family's parts
            variants = family_variants(entry, found["files"])
            if not variants:
                continue
            listed = await self.picture_files(entry)
            parts = parts_of[entry["id"]] = [f for f in listed if f["role"] not in PICTURE_MAIN]
            await asyncio.to_thread(_share, parts, named, models_home() / "pictures" / SHARED)
            with_parts, coming = sum(p["size"] for p in parts), sum(p["size"] for p in parts if not p["done"])
            staging = await self._staging(parts)  # the encoder's largest tensor, from its header (the fix after 0.1.15)
            for v in variants:  # a pair's halves - each the parameters Hugging Face counted in one
                bits = round(v["size"] / len(v["files"]) * 8 / params, 1) if params else None
                place = variant_place(v["files"], with_parts, space, staging)  # on the card, in RAM
                shown.append({"family": entry["id"], "name": entry["name"], "quant": v["quant"], "size": v["size"],
                              "gb": round(v["size"] / 1e9, 1), "bits": bits, "quality": quality_of(bits), **place,
                              "words": tr(PICTURE_VERDICTS[place["verdict"]]), "with_parts": v["size"] + with_parts,
                              "coming": v["size"] + coming, "modes": entry.get("modes") or ["text"],
                              "room": int(space["ram_gib"] * GIB), "checked": bool(entry.get("checked")),
                              "files": [{k: f[k] for k in ("path", "role", "size")} for f in v["files"]]})  # fmt: skip
        if not shown:
            raise InstallError(tr("в {repo} нет файлов модели GGUF", repo=repo), 404)
        # a part in a repository closed to this token (the fix after 0.1.16) - said before «Установить»
        mine = {"repo": repo, "path": shown[0]["files"][0]["path"], "revision": found["revision"]}
        closed = await self._closed([mine, *(p for parts in parts_of.values() for p in parts)])
        fast = [v for v in shown if v["verdict"] == "gpu" and (
            v["bits"] <= MAX_BITS if v["bits"] is not None else v["quant"] not in hf.SIXTEEN_BITS)]  # fmt: skip
        best = max(fast, key=lambda v: v["size"]) if fast else min(
            (v for v in shown if v["verdict"] != "no"), key=lambda v: v["size"], default=None)  # fmt: skip
        for v in shown:
            v["recommended"] = v is best  # as a chat model's variants say it
        try:
            engine = sd_server(await asyncio.to_thread(config_text)) is None
        except InstallError:
            engine = True
        await self._keep_answers()
        entry = next(e for e in families if e["id"] == (best or shown[0])["family"])  # the recommended one's family
        licence = found["license"] or entry["license"]  # none on its page (leejet's FLUX.3 klein): the family's (47.9)
        return {"repo": repo, "family": entry["id"], "name": entry["name"], "kind": entry.get("kind", "image"),
                "modes": entry.get("modes") or ["text"],  # how a video family films (48.5) - the window says it
                "families": [{"id": e["id"], "name": e["name"]} for e in families if e["id"] in parts_of],
                "size": entry["size"], "license": licence, "license_kind": hf.license_kind(licence),
                "gated": found["gated"], "page": found["page"], "variants": shown,
                "recommended": best["quant"] if best else "", "engine": engine,
                "closed": closed, "closed_words": closed_words(closed, bool(self.token())) if closed else "",
                "parts": [{"role": p["role"], "repo": p["repo"], "path": p["path"], "size": p["size"],
                           "have": p["done"]} for p in parts_of[entry["id"]]]}  # fmt: skip

    async def _engine_files(self, build: str) -> list[dict[str, Any]]:
        """The picture engine's archives the config does not name yet: its pinned build for this computer and, beside
        a card's, the processor's (29.4в: ~18 MB, the ladder's last rung)."""
        text = await asyncio.to_thread(config_text)
        builds = {SD_MACRO: build} | ({SD_CPU_MACRO: "cpu"} if build != "cpu" else {})
        out = []
        for macro, wanted in builds.items():
            if sd_server(text, macro) is not None:
                continue
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                    release = await engines.sd_cpp_release(http, wanted)
            except engines.EngineError as exc:
                raise InstallError(str(exc), 502) from exc
            missing = [a["name"] for a in release["assets"] if not a["sha256"]]
            if missing:  # as llama.cpp's (D46): nothing to run without it
                raise InstallError(tr("GitHub не дал контрольную сумму для {files}: без неё программа не ставится",
                                      files=", ".join(missing)), 502)  # fmt: skip
            out += [{"path": a["name"], "url": a["url"], "size": a["size"], "sha256": a["sha256"], "role": "engine",
                     "tag": release["tag"], "build": wanted, "done": False} for a in release["assets"]]  # fmt: skip
        return out

    # --- installs ---

    async def _publish(self, job_id: str) -> None:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is not None:
            await self._bus.publish("install.changed", {"job": (await self.mark_lost([found]))[0]})

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
        mine = {"repo": repo, "path": variant["files"][0]["path"], "revision": found["revision"]}
        if found.get("gated") and await self._closed([mine], fresh=True):  # said now, not after the download
            raise InstallError(tr("{name}: {words}", name=f"{repo_name(repo)} {quant}",
                                  words=closed_words([repo], bool(self.token()))), 403)  # fmt: skip
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
        try:
            text = config_text()
            llama_config.server_command(text)  # no llama.cpp in the config: nothing to install into
        except InstallError, llama_config.ConfigError:
            if not self.engine_coming():
                raise
            text = "models:\n"  # the engine on its way (39.3): the model downloads meanwhile, its block waits
        model_id = llama_config.free_id(text, id_base(repo, quant))  # _configure looks again
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

    async def _found_main(self, entry: dict[str, Any], repo: str, quant: str) -> tuple[list[dict[str, Any]], str]:
        """A found model's variant as a picture set's model (47.6): its files - one, or a pair's halves (48.2), each in
        its role - and the repository's licence; refused when the repository is not of the set's family or has no such
        variant of the family's model (family_variants)."""
        listed = await self._listed(repo)
        if entry["id"] not in {e["id"] for e in picture_families(repo, listed.get("bases") or [])}:
            raise InstallError(tr("{repo} — не модель семейства «{name}»", repo=repo, name=entry["name"]), 422)
        variant = next((v for v in family_variants(entry, listed["files"]) if v["quant"] == quant), None)
        if variant is None:
            raise InstallError(tr("у {repo} нет варианта {quant}", repo=repo, quant=quant), 404)
        # `tree`: by its path in the repository (stage 48's review, finding 1) - two halves of one name are two files
        files = [{"path": f["path"], "repo": repo, "revision": listed["revision"], "done": False, "size": f["size"],
                  "sha256": f["sha256"], "role": f["role"], "tree": True} for f in variant["files"]]  # fmt: skip
        return files, listed.get("license") or entry["license"]

    async def install_pictures(self, spec: dict[str, Any], client: Any) -> dict[str, Any]:
        """A picture model from the curated list: {id, drive or folder}; or (47.6) a found one of the set's family -
        `repo` and `quant`: that variant is the model, the set's encoders, VAE and flags are its; an install of its own,
        named by its repository. The engine comes with the first one."""
        entry = next((e for e in picture_sets() if e["id"] == spec["id"]), None)
        if entry is None:
            raise InstallError(tr("в списке нет модели {model}", model=spec["id"]), 404)
        found_repo, main = spec.get("repo") or "", next(f for f in entry["files"] if f["role"] in PICTURE_MAIN)
        if found_repo and entry.get("card", True) and (found_repo, spec.get("quant")) == (
                main["repo"], hf.quant_of(main["path"])):  # fmt: skip
            found_repo = ""  # a card's own model, chosen in the search (the fix after 0.1.15): the card's install
        repo, quant = (found_repo, spec.get("quant") or "") if found_repo else (main["repo"], hf.quant_of(main["path"]))
        rows = await self._db.acall(jobs)
        same = next((r for r in rows if r["plan"].get("set") == entry["id"] and (r["repo"], r["quant"]) == (repo, quant)
                     and r["status"] not in ("cancelled", "removed")), None)  # fmt: skip
        if same and same["status"] == "done":
            raise InstallError(tr("эта модель уже установлена — «{name}»", name=same["name"]))
        if same:
            return same
        machine = await asyncio.to_thread(self.machine)
        video = entry.get("kind") == "video"
        if video and not found_repo and picture_verdict(entry, machine) == "no":  # the set's: its measured thresholds
            raise InstallError(tr("видео на этом компьютере — только в облаке: {why}", why=machine.get("why", "")))
        parts = {**entry, "files": [f for f in entry["files"] if f["role"] not in PICTURE_MAIN]}
        own, licence = (await self._found_main(entry, repo, quant)) if found_repo else ([], entry["license"])
        # the parts' sums: an install shares a part with the others by them (_share)
        files = [*own, *await self.picture_files(parts if own else entry, sums=True)]
        if closed := await self._closed(files, fresh=True):  # nothing starts that would stop on it (after 0.1.16)
            raise InstallError(tr("{name}: {words}", name=f"{repo_name(repo)} {quant}" if own else entry["name"],
                                  words=closed_words(closed, bool(self.token()))), 403)  # fmt: skip
        if video and own:  # a found one (48.4): by its sizes, as sd.cpp places them
            verdict = variant_fit(own, sum(f["size"] for f in files if f not in own), await asyncio.to_thread(
                self.space))  # fmt: skip
            if verdict == "no":
                raise InstallError(tr("{name}: {words}", name=f"{repo_name(repo)} {quant}",
                                      words=tr(PICTURE_VERDICTS["no"])))  # fmt: skip
        engine = await self._engine_files(machine["build"])
        root = self._root(spec, await asyncio.to_thread(self.drives))
        folder = root / "pictures" / (repo.replace("/", os.sep) if own else entry["id"])
        flags = list(entry["flags"])
        if video and machine.get("tiles") and "--vae-tiling" not in flags:  # Vulkan falls without (29.1)
            flags.append("--vae-tiling")
        plan = {"engine": "sd", "set": entry["id"], "tier": entry["tier"], "build": machine["build"], "flags": flags,
                "size": entry["size"], **({"kind": "video"} if video else {}),
                **({"clip": entry["clip"]} if video and entry.get("clip") else {}),  # its own clip (48.3)
                **({"modes": entry["modes"]} if video and entry.get("modes") else {}),  # how it films (48.5)
                **({"found": repo} if own else {})}  # fmt: skip
        job_id = uuid.uuid4().hex[:12]
        everything = [*engine, *files]
        # the family's encoders and VAE: where another install keeps them, or one copy in `_shared` (47.3) - chosen
        # and written down under the lock a deletion counts who names a file under
        async with self._files:
            named = await self._db.acall(_named)
            await asyncio.to_thread(_share, files, named, root / "pictures" / SHARED)
            try:
                await asyncio.to_thread(_check_room, folder, sum(f["size"] for f in files if not f["done"]))
            except OSError as exc:
                raise InstallError(tr("папка {folder} недоступна: {error}", folder=folder, error=exc.strerror or exc),
                                   422) from exc  # fmt: skip
            text = await asyncio.to_thread(config_text)
            model_id = llama_config.free_id(text, id_base(repo, quant) if own else entry["id"])
            name = f"{repo_name(repo)} {quant}" if own else entry["name"]

            def add(conn) -> None:
                conn.execute(
                    "INSERT INTO model_installs (id, repo, revision, quant, files, folder, model_id, name, purposes, "
                    "license, status, total_bytes, plan) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (job_id, repo, files[0]["revision"], quant, json.dumps(everything), str(folder), model_id, name,
                     json.dumps(entry["purposes"]), licence, sum(f["size"] for f in everything), json.dumps(plan)),
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
        (a file, or ''), name (the list's, its id comes from it - 34.2)} - written into llama-swap's config where it
        lies, without a copy: «добавлена, не проверена» - its trial is the person's «Пробный запуск» (34.5, D57). Its
        files stay the person's: removing it never deletes them."""
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
        if same is not None and not (await self.mark_lost([same]))[0]["lost"]:
            raise InstallError(tr("эта модель уже добавлена — «{name}»", name=same["name"]))
        if same is not None:  # gone from the config (34.4): its old row makes way - the files are this one's now
            await self._db.acall(lambda conn: _update(conn, same["id"], status="removed", own_files=0))
            await self._publish(same["id"])
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
        # a blob added as a file, no name from the list: Ollama's own (36.1), else its header's
        ollama = ollama_names().get(os.path.normcase(os.path.normpath(path)), "") if BLOB.fullmatch(stem) else ""
        named = spec.get("name") or ollama or (info.get("name") if BLOB.fullmatch(stem) else "") or stem
        model_id = llama_config.free_id(text, local_id(named))
        name = spec.get("name") or ollama or info.get("name") or stem
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
        """«Продолжить», «Повторить» - and «Вернуть в llama-swap» (34.4): a model whose block is gone from the config
        goes through the config and the trial again; its files are there."""
        found = await self._get(job_id)
        if found["status"] not in ("paused", "failed") and not (await self.mark_lost([found]))[0]["lost"]:
            raise InstallError(tr("продолжить можно приостановленную или неудачную установку, а вернуть в llama-swap — "
                                  "модель, которой нет в config.yaml"))  # fmt: skip
        self._clients[job_id] = client
        await self._set(job_id, status="queued", error="")
        self._kick()
        return await self._get(job_id)

    def mark_sound(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each install says what of sound (52.4, `sound_of`): the window offers «Добавить звук» by it."""
        sets = picture_sets()
        for row in rows:
            row["sound"] = sound_of(row, sets)
        return rows

    async def add_sound(self, job_id: str, client: Any) -> dict[str, Any]:
        """«Добавить звук» (52.4): a video model installed before its family had an audio VAE in the catalog gets
        that one file - where another install keeps it, or in the family's shared folder - and goes through the
        download, the config (its block written again, with the file as sd-server's flag) and the trial once more;
        its other files are there."""
        found = await self._get(job_id)
        sets = picture_sets()
        state = sound_of(found, sets)
        if state == "on":
            raise InstallError(tr("звук у этой модели уже есть"))
        if state != "add":
            raise InstallError(tr("эта модель снимает без звука: добавить нечего"))
        family = next(e for e in sets if e["id"] == found["plan"]["set"])
        # its sum with the token (the fix after 0.1.17): the file is shared with the other installs by it (_share)
        sound = {**family, "files": [f for f in family["files"] if f["role"] == visual.SOUND_ROLE]}
        extra = await self.picture_files(sound, sums=True)
        if closed := await self._closed(extra, fresh=True):
            raise InstallError(tr("{name}: {words}", name=found["name"],
                                  words=closed_words(closed, bool(self.token()))), 403)  # fmt: skip
        folder = Path(found["folder"])
        async with self._files:  # as an install's own: chosen and written down under the lock a deletion counts under
            named = await self._db.acall(_named)
            await asyncio.to_thread(_share, extra, named, _pictures_root(folder) / SHARED)
            try:
                await asyncio.to_thread(_check_room, folder, sum(f["size"] for f in extra if not f["done"]))
            except OSError as exc:
                raise InstallError(tr("папка {folder} недоступна: {error}", folder=folder, error=exc.strerror or exc),
                                   422) from exc  # fmt: skip
            self._clients[job_id] = client
            await self._set(job_id, status="queued", error="", files=[*found["files"], *extra],
                            total_bytes=found["total_bytes"] + sum(f["size"] for f in extra))  # fmt: skip
        self._kick()
        return await self._get(job_id)

    async def try_added(self, client: Any, job_id: str = "") -> list[dict[str, Any]]:
        """«Пробный запуск» (34.5, D57): the models added and not tried - or one of them - wait for their trial, which
        goes one after another in the order they were added."""

        def queue(conn) -> list[str]:
            ids = [r["id"] for r in conn.execute("SELECT id FROM model_installs WHERE status = 'added' AND (? = '' OR "
                                                 "id = ?) ORDER BY created_at", (job_id, job_id))]  # fmt: skip
            for each in ids:
                _update(conn, each, status="trialwait", error="")
            return ids

        ids = await self._db.acall(queue)
        if job_id and not ids:
            raise InstallError(tr("пробный запуск — у добавленной и ещё не проверенной модели"))
        self.card.take_back()  # the person's button (48.9г): the card given away is theirs again
        for each in ids:
            self._clients[each] = client
            await self._publish(each)
        self._kick()
        return [await self._get(each) for each in ids]

    async def stop_trials(self) -> list[dict[str, Any]]:
        """«Остановить» (34.5): the models whose trial has not begun are «добавлена, не проверена» again; one being
        tried finishes."""

        def back(conn) -> list[str]:
            ids = [r["id"] for r in conn.execute("SELECT id FROM model_installs WHERE status = 'trialwait' AND "
                                                 "own_files = 0 ORDER BY created_at")]  # fmt: skip
            for each in ids:
                if each not in self._cancel:  # the one going waits for the card: it is stopped below
                    _update(conn, each, status="added", error="")
            return ids

        ids = await self._db.acall(back)
        for each in ids:
            if each in self._cancel:
                self._wanted[each] = "added"
                self._cancel[each].set()
            else:
                await self._publish(each)
        return [{**await self._get(each), "status": "added"} for each in ids]

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Stops an install and throws its files away; an installed model is removed with `remove`."""
        found = await self._get(job_id)
        if found["status"] in IN_CONFIG:
            raise InstallError(tr("модель уже установлена — её можно удалить"))
        if found["status"] in ("cancelled", "removed"):
            return found
        if job_id in self._cancel:
            self._wanted[job_id] = "cancelled"
            self._cancel[job_id].set()
            return found
        await self._throw_away(found)
        await self._set(job_id, status="cancelled", error="")
        return await self._get(job_id)

    async def _throw_away(self, found: dict[str, Any]) -> None:
        """An install's files deleted - but not one another install still names (47.3: a family's encoder and VAE),
        counted from the installs under the lock a picture install chooses its files under."""
        async with self._files:
            named = await self._db.acall(lambda conn: _named(conn, found["id"]))
            kept = {_key(_dest(Path(r["folder"]), f)) for r in named for f in r["files"] if f["role"] != "engine"}
            await asyncio.to_thread(_delete_files, found, kept)

    async def forget(self, job_id: str) -> None:
        """An install that did not finish off the list - with its files."""
        found = await self._get(job_id)
        if found["status"] in (*LIVE, *IN_CONFIG):
            raise InstallError(tr("эта установка ещё идёт или модель установлена"))
        await self._throw_away(found)
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
        ids = [model_id, *([found["plan"]["cpu"]] if found["plan"].get("cpu") else [])]  # its block on the processor
        for each in ids:
            self.card.let_go(each)  # 48.9г: the Studio's job, tuning working with it - stopped as by ✕
            await self._unload(each)
        path = llama_config.config_path()
        async with llama_config.writing():
            text = new = await asyncio.to_thread(config_text)
            for each in ids:
                if each in llama_config.model_blocks(new):
                    new = llama_config.remove_model(new, each)
            if new != text:
                backup = await asyncio.to_thread(llama_config.write, path, new)
                ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
                if not ok and problem != tr(llama_config.NOT_ANSWERING):
                    await asyncio.to_thread(llama_config.restore, path, backup)
                    raise InstallError(tr("llama-swap не принял правку ({problem}) — конфиг возвращён",
                                          problem=problem), 502)  # fmt: skip
        await self._throw_away(found)
        await self._set(found["id"], status="removed", error="")
        await self._sync()
        return await self._get(found["id"])

    async def _unload(self, model_id: str) -> None:
        """The model let go by llama-swap when it runs it - that one only (48.9г: the others are someone's)."""
        running = [r.get("model") for r in ((self._status() or {}).get("local") or {}).get("running", [])]
        if model_id not in running:
            return
        with contextlib.suppress(httpx.HTTPError):
            async with httpx.AsyncClient(timeout=30.0) as http:
                await http.post(f"{self._url.rstrip('/')}/api/models/unload/{model_id}")
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

            def oldest(conn):  # in the order they were added (34.5: by the last change, a retried one jumped)
                found = conn.execute("SELECT * FROM model_installs WHERE status IN ('queued', 'trialwait') ORDER BY "
                                     "created_at LIMIT 1").fetchone()  # fmt: skip
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
            if found["status"] == "trialwait":  # «Пробный запуск» of a model added (34.5), a trial a senior stopped
                pictures = found["plan"].get("engine") == "sd"  # (48.9в): in the config already
                await self._card(job_id, cancel, chats=pictures, background=job_id in self._quiet)
                found = await self._get(job_id)
                await (self._try_picture(found, True) if pictures else self._try(found, True))
                return
            await self._download(found, cancel)
            await self._engine_wait(job_id, cancel)
            if not found.get("own_files", 1):  # from the drives (34.5, D57): into the config only, nothing loaded -
                await self._configure(await self._get(job_id))  # the trial is the person's button
                await self._sync()
                await self._set(job_id, status="added", error="")
                return
            await self._card(job_id, cancel, chats=found["plan"].get("engine") == "sd")
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
                await self._throw_away(await self._get(job_id))
                await self._set(job_id, status="cancelled", error="")
            else:  # paused - or, a trial stopped before it began (34.5), added again
                await self._set(job_id, status=wanted, error="")
        except _Deferred as exc:  # 48.9в: the card is a senior's; 48.9б4: a game was on it - the trial waits for it
            # still, nothing failed
            log.info("install %s: its trial put off (%s); tried again once the card is still", job_id,
                     str(exc) or "a senior on the card")  # fmt: skip
            self._quiet.add(job_id)
            await self._set(job_id, status="trialwait", error="")
        except (hf.HubError, InstallError, llama_config.ConfigError, GGUFError, OSError) as exc:
            log.info("install %s failed: %s", job_id, exc)
            await self._set(job_id, status="failed", error=str(exc))
        finally:
            if self._seeing is not None:  # started in `_card`
                self._seeing.cancel()
            self._played = self._seeing = None
            self.card.give(TRIAL)  # taken in `_card`: the trial has ended, or never began
            self._cancel.pop(job_id, None)
            self.speed.pop(job_id, None)

    async def _engine_wait(self, job_id: str, cancel: threading.Event) -> None:
        """Until the engine on its way is in (39.3): a model downloaded before llama-swap waits «в очереди», with
        why; an engine that did not come leaves the config's own refusal to _configure."""
        while self.engine_coming():
            if cancel.is_set():
                raise hf.Cancelled()
            if (await self._get(job_id))["error"] != tr(ENGINE_WAIT):
                await self._set(job_id, status="queued", error=tr(ENGINE_WAIT))
            await asyncio.sleep(ENGINE_WAIT_S)

    async def _card(
        self, job_id: str, cancel: threading.Event, *, chats: bool = False, background: bool = False
    ) -> None:
        """Until the graphics card is the trial's (34.4): a local model is asked (48.9а: the card's count - a chat's
        turn, the editor's completion, an outside client; a cloud chat is not on the card) or the Studio holds the
        card (47.1) - its model would be swapped out in the middle of the answer or the picture. Tuning holding it is
        stopped (48.9б1: a trial before tuning) - llama-bench runs past llama-swap, and two models at once filled the
        memory (2026-11-08) - and the card is the trial's once it lets go. The card is taken in the same step these are
        looked at in - no `await` between them, a Studio's job came in between (stage 48's review, finding 2) - and held
        to the trial's end (`_run`). `chats` - a picture or video model's trial holds it from local chats too (2б): they
        wait at the gate. Never the two waiting for each other: a local request is waited for only while the card is
        not held, a chat waits only while it is. A chat model's trial does not hold it from chats - its own request
        goes through that gate. Meanwhile «ждёт пробного запуска». A senior stops the trial (48.9в, `_stop_trial`);
        stopped, it is background work (`background`): it waits for the card still for QUIET_S (48.9б2) - not as soon
        as the chat ends, or the models go to and fro on each message. A game on the card (48.9б5): nothing is loaded
        beside it - the trial, by the person's button too, waits for the card still, and says why."""
        quiet = Quiet(self.card) if background else None
        while True:
            if quiet is None and self.card.game():  # 48.9б5: its answer would not be taken (48.9б4) - not loaded
                quiet = Quiet(self.card)
            if (quiet.look() if quiet is not None else not self.card.asked) and self.card.try_take(
                    TRIAL, chats=chats, stop=self._stop_trial):  # fmt: skip
                break
            if cancel.is_set():
                raise hf.Cancelled()
            # waiting for the person's card (48.9д), for a game to end (48.9б5) - and why
            why = tr(GIVEN_WORDS) if self.card.given else tr(GAME_WORDS) if self.card.game() else ""
            found = await self._get(job_id)
            if found["status"] != "trialwait" or found["error"] != why:
                await self._set(job_id, status="trialwait", error=why)
            await asyncio.sleep(CARD_WAIT_S)
        self._deferring = False  # before any `await`: a stop said from now on is this trial's
        self._quiet.discard(job_id)
        self._played = Played(self.card)  # 48.9б4: a game on the card from now on - no verdict; looked at beside it
        self._seeing = asyncio.create_task(self._played.watch(CARD_WAIT_S))
        if (await self._get(job_id))["error"] in (tr(GIVEN_WORDS), tr(GAME_WORDS)):  # the card is the trial's: no
            await self._set(job_id, error="")  # longer waiting

    def _stop_trial(self) -> None:
        """A senior needs the card (48.9в: a chat, the Studio, any local request): the trial going on stops - its
        question let go (a video's job cancelled in the engine), a config being written first written whole."""
        if self._deferring:
            return
        self._deferring = True
        if self._asking is not None:
            self._asking.cancel()

    async def _attempt(self, asked: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
        """One question of the trial - let go when a senior stops it (48.9в): `_Deferred`. Its answer, or its failure,
        is not taken when a game was on the card meanwhile (48.9б4): `_Deferred` too - no try written, no smaller
        settings, no verdict; the trial waits for the card still."""
        if self._deferring:
            asked.close()
            raise _Deferred
        task = self._asking = asyncio.ensure_future(asked)
        try:
            answer = await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if self._deferring and task.cancelled() and not (current is not None and current.cancelling()):
                raise _Deferred from None
            raise
        except Exception:
            self._unless_played()
            raise
        finally:
            self._asking = None
        self._unless_played()
        return answer

    def _unless_played(self) -> None:
        if self._played is not None and self._played.look():
            raise _Deferred("a game was on the card")

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
        end = self._engine_end.get(job_id)  # the engine from GitHub on its way: how much of it is left (after 0.1.16)
        shown = {"id": job_id, "done_bytes": done, "total_bytes": total, "speed": round(self.speed.get(job_id, 0.0)),
                 "engine_left": max(end - done, 0) if end is not None else 0}  # fmt: skip
        await self._bus.publish("install.progress", shown, persist=False)

    async def _download(self, found: dict[str, Any], cancel: threading.Event) -> None:
        job_id, files = found["id"], found["files"]
        folder = Path(found["folder"])
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(lambda: _check_room(folder, _left(folder, files)))
        await self._set(job_id, status="downloading", error="")
        done = sum(f["size"] for f in files if f["done"])
        try:
            for at, f in enumerate(files):
                dest = _dest(folder, f)
                whole = await asyncio.to_thread(_whole, dest, f["size"])
                if f["done"] and whole:
                    continue
                if whole:  # got its name only once its sha256 matched
                    f["done"] = True
                else:
                    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)  # the engines' downloads
                    if f["role"] == "engine":  # GitHub's archives, before the model's files: their own time
                        self._engine_end[job_id] = done + sum(g["size"] for g in files[at:] if g["role"] == "engine"
                                                              and not g["done"])  # fmt: skip
                    else:
                        self._engine_end.pop(job_id, None)
                    await self._fetch(found, f, dest, done, cancel)
                done += f["size"]
                f["done"] = True
                await self._set(job_id, files=files, done_bytes=done)
        finally:
            self._engine_end.pop(job_id, None)

    async def _fetch(self, found: dict[str, Any], f: dict[str, Any], dest: Path, base: int,
                     cancel: threading.Event) -> None:  # fmt: skip
        # a picture model's file names its repository and commit; the engine's archive is GitHub's (no token there)
        repo, revision = f.get("repo") or found["repo"], f.get("revision") or found["revision"]
        url = f.get("url") or hf.file_url(repo, f["path"], revision)
        progress = hf.Progress()
        report = self._reporter(found["id"], base, found["total_bytes"])
        broken = 0
        for attempt in range(len(RETRIES_S) + 1):
            try:
                if not f.get("url") and hf.sha256_of(f.get("sha256")) is None:
                    # not known: Hugging Face hid it from a look without the token - 0.1.17 kept its asterisks (the fix
                    # after 0.1.17). Asked at the file's commit with the token; kept with the file once it is down
                    async with httpx.AsyncClient(timeout=30.0) as http:
                        f["sha256"] = (await hf.sums(http, repo, revision, self.token())).get(f["path"])
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
        async with llama_config.writing():  # 34.4: no other writer between this read and llama-swap's reread
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
                    with named(TRIAL):  # its request at the gate is the trial's (48.9а)
                        result = await self._attempt(self.trial(model, client))
                except _Deferred:
                    raise
                except Exception as exc:  # the check's own refusal (HTTPException) or a provider's trouble
                    result = {"ok": False, "error": str(getattr(exc, "detail", "") or exc)}
            tries.append({"context": plan["context"], "gpu_layers": plan["gpu_layers"], "ok": bool(result.get("ok")),
                          "seconds": result.get("seconds"), "error": result.get("error") or ""})  # fmt: skip
            if result.get("ok") or model is None or llama_config.hopeless(result.get("error") or ""):
                break  # 0.1.12: llama.cpp's own words say smaller settings would not start it either
            plan = smaller(plan)
            if plan is None:
                break
            if self._deferring:  # 48.9в: stopped - not smaller settings for it
                raise _Deferred
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
        async with llama_config.writing():  # 34.4: no other writer between this read and llama-swap's reread
            text = await asyncio.to_thread(config_text)
            engine = [f for f in found["files"] if f["role"] == "engine"]
            for build in dict.fromkeys(f["build"] for f in engine):  # the card's, the processor's (29.4в)
                parts = [f for f in engine if f["build"] == build]
                macro = SD_MACRO if build == found["plan"].get("build") else SD_CPU_MACRO
                if sd_server(text, macro) is not None:  # 47.9: one queued beside the first that brought it - its
                    continue  # files are another model's running sd-server's now; Windows refuses writing over them
                target = engines_home() / "stable-diffusion.cpp" / f"{parts[0]['tag']}-{build}"
                try:
                    for f in parts:
                        await asyncio.to_thread(engines.unpack, _dest(folder, f), target)
                    server = engines.sd_server_in(target)
                except engines.EngineError as exc:
                    raise InstallError(str(exc), 502) from exc
                text = llama_config.with_macro(text, macro, "'\"" + str(server).replace("\\", "/") + "\"'")
            if sd_server(text) is None:
                raise InstallError(tr("в config.yaml нет движка картинок (sd-server) — поставьте модель заново"))
            # (52.7 live, «Добавить звук») its own block written again under the same name: llama-swap names the
            # model before it has reread the file and after alike, and the trial came while it was rereading - «group
            # is shutting down». The block is taken out first and the model seen gone: llama-swap has reread; then it
            # is written back as a new one
            path, first = llama_config.config_path(), None
            mine = [
                b
                for b in (found["model_id"], f"{found['model_id']}-cpu")
                if b in llama_config.model_blocks(text) and _ours(text, b, job_id)
            ]
            if mine:
                for block_id in mine:
                    text = llama_config.remove_model(text, block_id)
                first = await asyncio.to_thread(llama_config.write, path, text)
                await llama_config.reloaded(self._url, list(llama_config.model_blocks(text)), gone=mine)
            blocks = [(found["model_id"], SD_MACRO, tr("{name}, установлена AFO: рисует картинки.", name=found["name"]),
                       found["name"])]  # fmt: skip
            video = found["plan"].get("kind") == "video"  # a video on the processor: hours - no block there (29.8)
            if not video and found["plan"].get("build") != "cpu" and sd_server(text, SD_CPU_MACRO) is not None:
                blocks.append((f"{found['model_id']}-cpu", SD_CPU_MACRO, tr(
                    "{name}, установлена AFO: рисует картинки процессором, когда видеокарта не может.",
                    name=found["name"]), tr("{name} — на процессоре", name=found["name"])))  # fmt: skip
            ids, flags = [], flags_now(found["plan"], await asyncio.to_thread(picture_sets))
            for wanted, macro, description, name in blocks:
                block_id = wanted
                if block_id in llama_config.model_blocks(text):
                    if _ours(text, block_id, job_id):
                        text = llama_config.remove_model(text, block_id)
                    else:
                        block_id = llama_config.free_id(text, block_id)
                block = llama_config.sd_block(
                    block_id,
                    name=name,
                    description=description,
                    server=f"${{{macro}}}",
                    files={f["role"]: str(_dest(folder, f)) for f in found["files"] if f["role"] != "engine"},
                    flags=flags,
                    metadata={
                        "source": f"huggingface.co/{found['repo']}",
                        "license": found["license"] or "?",
                        "localchat_install": job_id,
                    },
                )
                text = llama_config.add_model(text, block_id, block)
                ids.append(block_id)
            model_id, cpu_id = ids[0], (ids[1] if len(ids) > 1 else "")
            new = text
            backup = await asyncio.to_thread(llama_config.write, path, new)
            ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
            if not ok and problem != tr(llama_config.NOT_ANSWERING):
                await asyncio.to_thread(llama_config.restore, path, first or backup)  # as it was before all this
                raise InstallError(tr("llama-swap не принял новую модель ({problem}) — конфиг возвращён",
                                      problem=problem), 502)  # fmt: skip
        await self._set(job_id, model_id=model_id,
                        plan={**found["plan"], "flags": flags, **({"cpu": cpu_id} if cpu_id else {})})  # fmt: skip
        await self._bus.publish("llama.config", {"model_id": model_id, "installed": True, "backup": backup.name})
        return ok

    async def _try_picture(self, found: dict[str, Any], reachable: bool) -> None:
        """The trial picture: one small picture through llama-swap; a blank one does not count (hub/visual.py). Not on
        the card - on the processor (29.4в): then the model's pictures begin there on this computer."""
        job_id, model_id, cpu_id = found["id"], found["model_id"], found["plan"].get("cpu") or ""
        if not reachable:
            await self._set(job_id, status="done", trial={"skipped": tr("llama-swap не отвечает — модель проверится "
                                                                        "при первой картинке")})  # fmt: skip
            return
        await self._set(job_id, status="trial")
        if found["plan"].get("kind") == "video":  # a short video (29.8а)
            only_picture = "text" not in (found["plan"].get("modes") or ["text"])  # (48.5) its trial: AFO's own frame
            result = await self._attempt(visual.trial_video(self._url, model_id, clip=found["plan"].get("clip"),
                                                            picture=only_picture))  # fmt: skip
            if result["ok"]:
                await self._set(job_id, status="done", trial={"seconds": result["seconds"], "frames": result["frames"]})
                return
            await self._withdraw(model_id)
            await self._set(job_id, status="failed", error=tr(
                "модель скачана, но пробное видео не вышло: {error}. Файлы остались — «Повторить» попробует снова; или "
                "удалите", error=result["error"]))  # fmt: skip
            return
        result = await self._attempt(visual.trial(self._url, model_id))
        on_cpu = await self._attempt(visual.trial(self._url, cpu_id)) if not result["ok"] and cpu_id else None
        if result["ok"] or (on_cpu and on_cpu["ok"]):
            # a picture on the card starts the ladder afresh; one only on the processor - pictures begin there
            rules = {model_id: {}} if result["ok"] else {model_id: {"instead": cpu_id}}
            here = visual.computer(await asyncio.to_thread(self.machine))
            await self._db.acall(lambda conn: visual.remember(conn, here, rules))
            drawn = result if result["ok"] else on_cpu
            await self._set(job_id, status="done", trial={"seconds": drawn["seconds"], "size": drawn["size"],
                                                          **({} if result["ok"] else {"where": "cpu"})})  # fmt: skip
            return
        await self._withdraw(model_id, cpu_id)
        error = result["error"] if on_cpu is None else tr("{card}; на процессоре — тоже: {cpu}", card=result["error"],
                                                          cpu=on_cpu["error"])  # fmt: skip
        await self._set(job_id, status="failed", trial={"seconds": result.get("seconds")},
                        error=tr("модель скачана, но пробная картинка не вышла: {error}. Файлы остались — "
                                 "«Повторить» попробует снова; или удалите", error=error))  # fmt: skip

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
        async with llama_config.writing():
            text = await asyncio.to_thread(config_text)
            changes = {"context": plan["context"], "gpu_layers": plan["gpu_layers"], "ttl": plan["ttl"]}
            new = llama_config.edit(text, model_id, changes)
            await asyncio.to_thread(llama_config.write, path, new)
            await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)), (model_id, plan["context"]))
        await self._sync()

    async def _withdraw(self, *model_ids: str) -> None:
        """A model that would not start leaves the config (its files stay) - a picture model with its block on the
        processor."""
        path = llama_config.config_path()
        async with llama_config.writing():
            text = new = await asyncio.to_thread(config_text)
            for model_id in model_ids:
                if model_id and model_id in llama_config.model_blocks(new):
                    new = llama_config.remove_model(new, model_id)
            if new == text:
                return
            await asyncio.to_thread(llama_config.write, path, new)
            await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
        await self._sync()


def _setting(conn, key: str) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    value = json.loads(row["value"]) if row else {}
    return value if isinstance(value, dict) else {}


def _put_setting(conn, key: str, value: Any) -> None:
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                 "value = excluded.value, updated_at = excluded.updated_at",
                 (key, json.dumps(value, ensure_ascii=False), utc_now()))  # fmt: skip


def _views(text: str) -> list[dict[str, Any]]:
    """The config's llama.cpp models the window edits, as model_view reads them."""
    out = []
    for model_id in llama_config.model_blocks(text):
        with contextlib.suppress(llama_config.ConfigError):
            view = llama_config.model_view(text, model_id)
            if view["engine"] == "llama.cpp" and view["editable"]:
                out.append(view)
    return out


def _without_lost_drafts(text: str) -> tuple[str, list[str]]:
    """The config with guessing ahead off for each model whose draft model's file is not on the disk (blocking: in a
    thread); the models changed."""
    changed = [v["id"] for v in _views(text) if v["spec"]["draft"] and not Path(v["spec"]["draft"]).is_file()]
    for model_id in changed:
        text = llama_config.edit(text, model_id, {"spec_type": "none"})
    return text, changed


def _delete_draft(path: Path) -> None:
    """A draft's file and what is left of its download."""
    for each in (path.with_name(path.name + ".part"), path.with_name(path.name + ".parts")):
        each.unlink(missing_ok=True)
    path.unlink(missing_ok=True)


def config_text() -> str:
    path = llama_config.config_path()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallError(tr("нет конфига llama-swap ({path}): сначала нужен llama-swap (мастер первого запуска)",
                              path=path), 409) from exc  # fmt: skip


def _estimated(text: str, model_id: str, vram_total_mib: int | None) -> dict[str, Any] | None:
    """estimate() of a model as the config launches it, its file's header read (blocking: in a thread); None when it
    cannot be told - not llama.cpp's, not in the config, its file not read."""
    try:
        view = llama_config.model_view(text, model_id)
        if view["engine"] != "llama.cpp" or not view.get("file"):
            return None
        info = model_info(Path(view["file"]))
        mmproj = Path(view["mmproj"]).stat().st_size if view.get("mmproj") else 0
    except llama_config.ConfigError, OSError, GGUFError:
        return None
    if view["spec"]["draft"]:  # the draft model guessing ahead (42.2); its file away - the model's own estimate
        with contextlib.suppress(OSError, GGUFError):
            view["draft_info"] = model_info(Path(view["spec"]["draft"]))
    return estimate(info, context=view.get("context") or 4096, cache_type=view.get("cache_type") or "f16",
                    gpu_layers=view.get("gpu_layers") or "all", mmproj_bytes=mmproj,
                    mmproj_on_gpu=bool(view.get("mmproj_on_gpu")), vram_total_mib=vram_total_mib,
                    draft=draft_load(view), moe=moe_load(view))  # fmt: skip


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
    """Where a file of an install goes: the model's folder; the picture engine's archive - the engines' downloads; a
    picture family's encoder or VAE (47.3) - where it lies for all its models (`at`); a found model's file (`tree`,
    stage 48's review) - by its path in its repository, inside the folder: a pair's halves of one name, each in its
    folder (HighNoise, LowNoise), lay in one file by the name alone. One that would leave the folder - by the name."""
    if f["role"] == "engine":
        return engines_home() / "downloads" / f["path"]
    if f.get("at"):
        return Path(f["at"])
    path = PurePosixPath(f["path"])
    if f.get("tree") and not path.is_absolute() and all(p not in (".", "..") and ":" not in p and "\\" not in p
                                                        for p in path.parts):  # fmt: skip
        return folder.joinpath(*path.parts)
    return folder / path.name


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _named(conn, but: str = "") -> list[dict[str, Any]]:
    """The installs whose files may still be needed (47.3): all but the cancelled and the removed - a failed one keeps
    its files for «Повторить», a model added from the drives names the person's own - and but `but`."""
    rows = conn.execute("SELECT id, folder, files FROM model_installs WHERE status NOT IN ('cancelled', 'removed') "
                        "AND id != ?", (but,)).fetchall()  # fmt: skip
    return [{"id": r["id"], "folder": r["folder"], "files": json.loads(r["files"] or "[]")} for r in rows]


def _share(files: list[dict[str, Any]], named: list[dict[str, Any]], shared: Path) -> None:
    """A picture install's encoders and VAE (47.3), in place: one another install names - the same repository, path
    and sha256 - is taken where it lies (`at`; down already - `done`), whosever folder it is in; another goes to
    `shared`, one copy for the family - beside an older file of the same name, in a folder of its sha256."""
    have: dict[tuple[str, str, str], Path] = {}
    taken: dict[str, str] = {}  # where the other installs keep a file -> its sha256
    for row in named:
        for g in row["files"]:
            if g["role"] == "engine":
                continue
            where = _dest(Path(row["folder"]), g)
            taken[_key(where)] = g.get("sha256") or ""
            if g.get("repo") and g.get("sha256"):
                have.setdefault((g["repo"], g["path"], g["sha256"]), where)
    for f in files:
        if f["role"] in (*PICTURE_MAIN, "engine") or not f.get("sha256"):
            continue
        there = have.get((f["repo"], f["path"], f["sha256"]))
        if there is None:
            there = shared / f["repo"].replace("/", os.sep) / PurePosixPath(f["path"]).name
            if taken.get(_key(there), f["sha256"]) != f["sha256"]:
                there = there.parent / f["sha256"][:12] / there.name
        f["at"] = str(there)
        f["done"] = there.is_file() and there.stat().st_size == f["size"]  # one still coming: the queue's next


def _left(folder: Path, files: list[dict[str, Any]]) -> int:
    """The bytes an install's download still takes on the disk (stage 48's review, finding 7): a file not come yet, less
    what its `.part` holds there - a big one's is as long as the file from its first piece (hf._pieces)."""
    left = 0
    for f in files:
        if f["done"]:
            continue
        dest = _dest(folder, f)
        part = dest.with_name(dest.name + ".part")
        held = f["size"] if _whole(dest, f["size"]) else part.stat().st_size if part.is_file() else 0
        left += max(f["size"] - held, 0)
    return left


def _whole(path: Path, size: int) -> bool:
    return path.is_file() and path.stat().st_size == size


def _check_room(folder: Path, size: int) -> None:
    anchor = Path(folder.anchor or folder)
    free = shutil.disk_usage(anchor).free
    if free < size + DISK_MARGIN_GIB * GIB:
        raise InstallError(tr("на диске {disk} не хватает места: нужно {need:.1f} ГБ, свободно {free:.1f} ГБ",
                              disk=anchor, need=size / GIB + DISK_MARGIN_GIB, free=free / GIB), 507)  # fmt: skip


def _delete_files(found: dict[str, Any], kept: set[str] | frozenset[str] = frozenset()) -> None:
    """This install's files (their parts and lists of pieces too) gone, then its folder and the author's folder if
    they are empty - only files AFO downloaded: a model found on the drives keeps its files whatever happens (15.1);
    and not those another install names (`kept`, 47.3) - a shared one's empty folders go with its last model."""
    if not found.get("own_files", 1):
        return
    folder = Path(found["folder"])
    away: list[Path] = []
    for f in found["files"]:
        if f["role"] == "engine":
            continue  # the picture engine stays: the other picture models run on it
        dest = _dest(folder, f)
        if _key(dest) in kept:
            continue
        for path in (dest, dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".parts")):
            for _ in range(10):  # a server that just let go of the file: Windows may hold it a moment longer
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.5)
        if f.get("at"):
            away.append(dest.parent)
        for up in dest.parents if f.get("tree") else ():  # its repository's folders inside the install's, while empty
            if up == folder or folder not in up.parents:
                break
            try:
                up.rmdir()
            except OSError:
                break
    for empty in (folder, folder.parent):
        with contextlib.suppress(OSError):
            empty.rmdir()
    for each in away:  # up to `pictures`, while empty
        for up in (each, *each.parents):
            if up.name == "pictures" or up == up.parent:
                break
            try:
                up.rmdir()
            except OSError:
                break
