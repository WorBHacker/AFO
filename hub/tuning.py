"""Launch settings by measurement (step 15.2, D49): for a llama.cpp model in llama-swap's config, llama-bench of the
same llama.cpp build measures what a few settings give on this computer - flash attention on and off, the KV cache's
type, the ubatch - with a filled context (DEPTH tokens already in it, as a chat with some history). The fastest answer
wins (answers within CLOSE of each other: the faster prompt decides), and the config changes only when the winner is
faster than its own settings by more than CLOSE - less is the measurement's noise; the model's placement stays as the
config has it (all on the graphics card, a number of layers, or llama.cpp's own --fit), but for a Mixture-of-Experts
model that does not fit whole: where its experts go is measured by llama-server (44.2), then guessing ahead (42.4).
The winner goes into the config - a copy of the old one beside it - and the model is loaded with its whole context to
check it still fits; if not, the old config comes back.

Automatic tuning (after a model is installed or added, after llama.cpp is updated) waits until no chat has run for
IDLE_S and the graphics card itself has been quiet as long - the hub's own checks, the editor's completion, another
program are no chats, and measured beside them the numbers are theirs too (stage 28, live); a chat that starts while
it measures stops it, nothing is written, and the model waits its turn again. Measuring unloads the models and takes
the graphics card for minutes. The person's «Подобрать» waits only for the chats going on now."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import psutil

from common.gguf import GGUFError, model_info
from common.i18n import N_, tr
from common.llama_memory import COMPUTE_GIB, estimate, kv_bytes, moe_load
from common.paths import data_paths

from . import llama_config, model_settings
from .db import utc_now

log = logging.getLogger("hub.tuning")

BENCH = "llama-bench.exe" if sys.platform == "win32" else "llama-bench"
RESULTS_KEY = "tune.results"
AUTO_KEY = "tune.auto"
PROMPT, GEN, REPEAT = 512, 128, 2
DEPTH = 4096  # tokens already in the context while it measures
UBATCHES = (512, 1024, 2048)
BATCH = 2048
UNQUANTIZED = ("f16", "bf16", "f32")  # KV cache types that work with flash attention off
FIT_MARGIN_MIB = 1024  # what llama.cpp's fitting leaves free on the graphics card
CLOSE = 0.03
IDLE_S = 60.0
WAIT_S = 2.0  # as often as the status monitor samples the graphics card
BUSY_PCT = 15  # the graphics card at this much load or more works for someone: tuning waits
BENCH_TIMEOUT_S = 1800.0
CHECK_TIMEOUT_S = 300.0
WATCH_S = 3.0  # llama-swap --watch-config looks at the file every 2 s: a write with the same context is not seen
RELOADING = "shutting down"  # llama-swap's answer while it rereads the file («group is shutting down»)
RETRY_S = 1.0
GIB = 1024**3
# guessing ahead (42.4): measured by llama-server itself - llama-bench has no draft model
NGRAMS = ("ngram-mod", "ngram-simple")  # the kind of ngram guessing measured: the first the build has
# a draft made for one model guesses its own way (44.4): its header's general.architecture as llama.cpp b11899 names
# it -> llama-server's --spec-type; any other draft model - draft-simple
DRAFT_KINDS = {"dflash": "draft-dflash", "eagle3": "draft-eagle3"}
SPEC_TOKENS = 256  # each answer measured
SEED = 42
SERVE_TIMEOUT_S = 600.0  # a large model read from a slow disk
HEALTH_S = 0.5
SAMPLING = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty")  # the model's own: they decide how often a
# guess is right
SPEC_PROMPTS = (  # a code edit (ngram guesses its copied lines) and a plain answer (only a draft model guesses those)
    N_("Переименуй в этой функции переменную total в amount и верни весь код целиком, без пояснений:\n\n"
       "def summarize(orders):\n"
       "    total = 0\n"
       "    count = 0\n"
       "    for order in orders:\n"
       "        if order.get(\"status\") != \"paid\":\n"
       "            continue\n"
       "        total += order[\"price\"] * order.get(\"quantity\", 1)\n"
       "        count += 1\n"
       "    average = total / count if count else 0\n"
       "    return {\"total\": total, \"count\": count, \"average\": average}\n"),
    N_("Объясни простыми словами, как работает холодильник: откуда берётся холод и куда уходит тепло. "
       "Пять-шесть предложений."),
)  # fmt: skip
# where a Mixture-of-Experts model's experts go (44.2): measured by llama-server too - llama-bench has no --cpu-moe
MOE_MORE = 3  # the experts of this many layers more in RAM than the fewest with which the rest fits: room for buffers
MOE_CACHE_MIN_MIB = 1024  # a smaller cache of experts is not measured
MOE_CACHE_STEP_MIB = 256
READ_DROP = 0.10  # a placement that answers faster may read a long question this much slower, no more
READ_MIN_CONTEXT = 4096  # the long question's reading is measured with a context of at least this
# a long question (~3 thousand tokens): the experts in RAM slow its reading most (46.1: three times with --cpu-moe)
LONG_PROMPT = "\n".join(f"{i}. The value of item {i} is {i * 7919 % 10007}." for i in range(200)) + \
    "\nWhich item has the largest value? Answer with its number only."  # fmt: skip


class TuneError(Exception):
    """The model cannot be tuned; the message is for the window."""


class _Halted(Exception):
    """Automatic measuring stopped for a chat: the model is measured again later."""


def server_path(text: str) -> Path:
    """llama-server's program, as the config names it (directly or through a macro)."""
    command = llama_config.server_command(text)
    if command.startswith("${"):
        command = llama_config.macros(text).get(command.strip("${}"), "")
    return Path(command.strip("'\" "))


def bench_path(text: str) -> Path:
    bench = server_path(text).with_name(BENCH)
    if not bench.is_file():
        raise TuneError(tr("рядом с llama-server нет llama-bench ({path}) — обновите сборку llama.cpp в мастере "
                           "первого запуска", path=bench))  # fmt: skip
    return bench


def caches(view: dict[str, Any], info: dict[str, Any], size: int, space: dict[str, Any]) -> list[str]:
    """The KV cache types to measure: the config's and q8_0; f16 too when the model keeps its whole context with it on
    the graphics card (it takes twice q8_0's memory)."""
    out = list(dict.fromkeys([view.get("cache_type") or "f16", "q8_0"]))
    if "f16" not in out and view["gpu_layers"] == "all":
        need = size / GIB + kv_bytes(info, view["context"] or 4096, "f16") / GIB + COMPUTE_GIB
        if space.get("vram_gib") and need <= space["vram_gib"]:
            out.append("f16")
    return out


def flash(cache: str) -> list[str]:
    """Flash attention to measure with a KV cache type: a quantized V cache only with it on - without it llama.cpp
    makes no context, and llama-bench stops at the first setting it cannot make."""
    return ["on", "off"] if cache in UNQUANTIZED else ["on"]


def command(bench: Path, view: dict[str, Any], cache: str, *, threads: int, depth: int) -> list[str]:
    """llama-bench for one KV cache type: flash attention (on and off, where it can be off), each ubatch - one load
    of the model."""
    args = [str(bench), "-m", view["file"], "-p", str(PROMPT), "-n", str(GEN), "-d", str(depth), "-r", str(REPEAT),
            "-o", "jsonl", "-fa", ",".join(flash(cache)), "-ctk", cache, "-ctv", cache,
            "-ub", ",".join(map(str, UBATCHES)),
            "-b", str(BATCH), "-t", str(threads)]  # fmt: skip
    if view["gpu_layers"] == "auto":  # llama.cpp places it: as it will with --fit, for the model's context
        args += ["-fitt", str(FIT_MARGIN_MIB), "-fitc", str(view["context"] or 4096)]
    else:
        args += ["-ngl", "99" if view["gpu_layers"] == "all" else str(view["gpu_layers"])]
    if view.get("n_cpu_moe"):
        args += ["-ncmoe", str(view["n_cpu_moe"])]
    return args


def _fa(value: Any) -> str:
    if value in (True, 1, "1", "on", "true"):
        return "on"
    return "off" if value in (False, 0, "0", "off", "false") else "auto"


def results(lines: list[str]) -> list[dict[str, Any]]:
    """llama-bench's jsonl: each setting with its prompt speed (pp) and answer speed (tg), tokens a second."""
    found: dict[tuple, dict[str, Any]] = {}
    for text in lines:
        try:
            run = json.loads(text)
        except ValueError:
            continue
        if not isinstance(run, dict) or "avg_ts" not in run:
            continue
        key = (_fa(run.get("flash_attn")), run.get("type_k") or "f16", int(run.get("n_ubatch") or 0),
               int(run.get("n_threads") or 0))  # fmt: skip
        entry = found.setdefault(key, {"flash_attn": key[0], "cache_type": key[1], "ubatch": key[2], "threads": key[3]})
        entry["tg" if run.get("n_gen") else "pp"] = float(run["avg_ts"])
    return [e for e in found.values() if "tg" in e]


def best(measured: list[dict[str, Any]]) -> dict[str, Any]:
    top = max(e["tg"] for e in measured)
    return max((e for e in measured if e["tg"] >= top * (1 - CLOSE)), key=lambda e: (e.get("pp", 0.0), e["tg"]))


def current(view: dict[str, Any], measured: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the config's settings measured (flash attention «auto» counts as on: llama.cpp turns it on where it can)."""
    fa = "off" if view["flash_attn"] == "off" else "on"
    ub = view.get("ubatch") or 512
    return next((e for e in measured if (e["flash_attn"], e["cache_type"], e["ubatch"]) ==
                 (fa, view.get("cache_type") or "f16", ub)), None)  # fmt: skip


def same(view: dict[str, Any], choice: dict[str, Any]) -> bool:
    return current(view, [choice]) is not None


def worth(choice: dict[str, Any], before: dict[str, Any] | None) -> bool:
    """A change pays only past the measurement's noise: a faster answer, or as fast an answer with a faster prompt."""
    if before is None:
        return True
    if choice["tg"] > before["tg"] * (1 + CLOSE):
        return True
    return choice["tg"] >= before["tg"] * (1 - CLOSE) and choice.get("pp", 0.0) > before.get("pp", 0.0) * (1 + CLOSE)


def changes_for(choice: dict[str, Any]) -> dict[str, Any]:
    return {"flash_attn": choice["flash_attn"], "cache_type": choice["cache_type"], "ubatch": choice["ubatch"],
            "batch": max(BATCH, choice["ubatch"]), "threads": choice["threads"]}  # fmt: skip


def _tail(path: Path) -> str:
    """A log's last words: why a program stopped."""
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return ""
    return " ".join([line for line in lines if line][-3:])


def server_log() -> Path:
    """llama-server's log while guessing ahead is measured (42.4)."""
    return data_paths().logs / "tuning-server.log"


def _said(path: Path, word: str) -> str:
    """llama.cpp's first error about `word` in a log (its «E» lines), without the time and the function: why it went
    on without something (42.7)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.split(": ", 1)[-1].strip() for line in lines if " E " in line and word in line), "")


def free_port() -> int:
    """A port nobody listens on, for a llama-server the measuring runs itself (42.4)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def spec_command(text: str, model_id: str, changes: dict[str, Any], build: dict[str, Any], port: int) -> list[str]:
    """The model's launch as the config would have it with these settings (llama_config.edit, in `build`'s names) -
    to run past llama-swap on a port of the measuring's own: its macros in, ${PORT} too (42.4)."""
    view = llama_config.model_view(llama_config.edit(text, model_id, changes, build=build), model_id)
    argv = [word.replace("${PORT}", str(port)) for word in llama_config.arguments(view["command"])]
    if any("${" in word for word in argv):
        raise TuneError(tr("в команде «{model}» есть макрос llama-swap, который AFO не подставит", model=model_id))
    return argv


def spec_variants(view: dict[str, Any], info: dict[str, Any], space: dict[str, Any], build: dict[str, Any],
                  draft: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """What guessing ahead to measure (42.4): none; ngram (it takes no memory); the model's own MTP layers (44.3: its
    header has them, the build has draft-mtp - no file of their own); the draft model ({path, info}; 44.4: a DFlash or
    EAGLE-3 draft guesses its own way - its kind by its header, DRAFT_KINDS, when the build has it) beside
    the model as it is - or, when it does not fit so (the user's word, 2026-11-13), wholly on the graphics card and the
    model with as few layers fewer there as it needs; the context is never made smaller by itself. And what the draft
    lacks to fit beside the model as it is ({short_gb, context} - the largest context it would fit with; {} - it
    fits, or there is no draft)."""
    out: list[dict[str, Any]] = [{"name": "none", "changes": {"spec_type": "none"}}]
    ngram = next((kind for kind in NGRAMS if kind in build["spec_types"]), None)
    if ngram:
        out.append({"name": "ngram", "changes": {"spec_type": ngram}})
    if info.get("mtp_layers") and "draft-mtp" in build["spec_types"]:  # 44.3: the model's own layers guess ahead
        out.append({"name": "mtp", "changes": {"spec_type": "draft-mtp"}})
    kind = DRAFT_KINDS.get(draft["info"].get("architecture") or "", "draft-simple") if draft else ""
    if draft is None or kind not in build["spec_types"]:
        return out, {}
    cache = view.get("cache_type") or "f16"  # the draft's KV cache as the model's: q8_0 takes half of f16
    load = {"info": draft["info"], "gpu_layers": "all", "cache_type": cache}
    changes = {"spec_type": kind, "draft_model": str(draft["path"]), "draft_gpu_layers": "all",
               "draft_cache_type": cache}  # fmt: skip
    context = view.get("context") or 4096

    def need(layers: str | int, tokens: int = context) -> float:
        return estimate(info, context=tokens, cache_type=cache, gpu_layers=layers, draft=load,
                        moe=moe_load(view))["vram_gb"]  # fmt: skip

    room = space.get("vram_gib") or 0.0
    if view["gpu_layers"] == "auto" or need(view["gpu_layers"]) <= room:  # --fit places them itself
        return [*out, {"name": "draft", "changes": changes}], {}
    layers = info["layers"] if view["gpu_layers"] == "all" else int(view["gpu_layers"])
    fewer = next((n for n in range(layers - 1, 0, -1) if need(n) <= room), None)
    if fewer is not None:
        out.append({"name": "draft_fewer", "changes": {**changes, "gpu_layers": fewer}})
    smaller = [context // 2**i for i in range(1, 5) if context // 2**i >= 4096]
    return out, {"short_gb": round(need(view["gpu_layers"]) - room, 1),
                 "context": next((c for c in smaller if need(view["gpu_layers"], c) <= room), 0)}  # fmt: skip


def spec_choice(measured: list[dict[str, Any]]) -> dict[str, Any]:
    """The guessing ahead to write (42.4): the variants come cheapest first (none, ngram, the draft, the draft with
    the model's layers fewer on the card); a costlier one only when faster than each cheaper one by more than CLOSE -
    less is the measurement's noise, and a draft takes memory."""
    pick = measured[0]
    for at, variant in enumerate(measured[1:], 1):
        if variant["tg"] > max(m["tg"] for m in measured[:at]) * (1 + CLOSE):
            pick = variant
    return pick


def _placed(view: dict[str, Any], changes: dict[str, Any]) -> bool:
    """Whether the command keeps the experts as these changes would."""
    return (view.get("gpu_layers"), bool(view.get("cpu_moe")), view.get("n_cpu_moe") or 0,
            view.get("moe_cache_mib") or 0) == (changes["gpu_layers"], changes["cpu_moe"], changes["n_cpu_moe"],
                                                changes["moe_cache_mib"])  # fmt: skip


def moe_variants(view: dict[str, Any], info: dict[str, Any], space: dict[str, Any], build: dict[str, Any] | None,
                 *, mmproj: int = 0) -> list[dict[str, Any]]:  # fmt: skip
    """Where to keep a Mixture-of-Experts model's experts (44.2), measured when the model does not fit on the card
    whole: the command's own placement first (after an install - llama.cpp's --fit); the experts of the fewest first
    layers in RAM with which the rest fits (--n-cpu-moe), and of MOE_MORE layers more; every expert in RAM and as large
    a cache of them on the card as the room leaves (--cpu-moe --moe-cache-mib, a build that has it). Each with all the
    rest on the card, its experts within the RAM there is. A model that fits whole while its command keeps experts in
    RAM: whole on the card. [] - nothing to measure: no experts, no graphics card, or it fits as it is."""
    experts = info.get("expert_bytes") or []
    room, ram = space.get("vram_gib") or 0.0, space.get("ram_gib") or 0.0
    if not experts or not room:
        return []

    def judged(moe: dict[str, Any] | None) -> dict[str, Any]:
        return estimate(info, context=view.get("context") or 4096, cache_type=view.get("cache_type") or "f16",
                        gpu_layers="all", mmproj_bytes=mmproj, mmproj_on_gpu=bool(view.get("mmproj_on_gpu")),
                        moe=moe)  # fmt: skip

    def fits(moe: dict[str, Any] | None) -> bool:
        need = judged(moe)
        return need["vram_gb"] <= room and (not ram or need["ram_gb"] <= ram)

    whole = {"gpu_layers": "all", "cpu_moe": False, "n_cpu_moe": 0, "moe_cache_mib": 0}
    out = [{"name": "card", "changes": whole}] if fits(None) and moe_load(view) else []
    if not fits(None):
        fewest = next((n for n in range(1, len(experts) + 1) if fits({"all": False, "layers": n})), None)
        if fewest is not None:
            out.append({"name": "layers", "changes": {**whole, "n_cpu_moe": fewest}})
            if fewest + MOE_MORE <= len(experts):
                out.append({"name": "layers_more", "changes": {**whole, "n_cpu_moe": fewest + MOE_MORE}})
        if build is not None and llama_config.spelled("moe_cache_mib", build)[1] is not None:
            alone = judged({"all": True})
            mib = min(int((room - alone["vram_gb"]) * 1024), sum(experts) // 1024**2)
            mib -= mib % MOE_CACHE_STEP_MIB
            if mib >= MOE_CACHE_MIN_MIB and (not ram or alone["ram_gb"] <= ram):
                out.append({"name": "cache", "changes": {**whole, "cpu_moe": True, "moe_cache_mib": mib}})
    out = [v for v in out if not _placed(view, v["changes"])]
    return [{"name": "now", "changes": {}}, *out] if out else []


def moe_choice(measured: list[dict[str, Any]]) -> dict[str, Any]:
    """The placement of the experts to write (44.2): the command's own, unless another answers faster by more than
    CLOSE - the fastest such - and reads the long question at most READ_DROP slower. The command's own that did not
    start: the fastest that did."""
    now = measured[0]
    ran = [m for m in measured[1:] if "tg" in m]
    if "tg" not in now:
        return max(ran, key=lambda m: m["tg"]) if ran else now
    better = [m for m in ran if m["tg"] > now["tg"] * (1 + CLOSE)
              and (not now.get("pp") or not m.get("pp") or m["pp"] >= now["pp"] * (1 - READ_DROP))]  # fmt: skip
    return max(better, key=lambda m: m["tg"]) if better else now


def sampling(conn, model_id: str) -> dict[str, Any]:
    """The model's own sampling, as the hub's model settings have it (llama-swap's id): a guess is right more often
    when the answer is surer - measured as it will be used."""
    params = model_settings.current(conn, f"llama-swap/{model_id}")[1].get("params") or {}
    return {key: params[key] for key in SAMPLING if key in params}


def load_results(conn) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RESULTS_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def _put(conn, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), utc_now()),
    )


def auto(conn) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (AUTO_KEY,)).fetchone()
    return bool(json.loads(row[0])) if row else True


def set_auto(conn, on: bool) -> None:
    _put(conn, AUTO_KEY, bool(on))


def tunable(text: str) -> list[str]:
    """The config's models llama.cpp runs with a command AFO can change."""
    out = []
    for model_id in llama_config.model_blocks(text):
        with contextlib.suppress(llama_config.ConfigError):
            view = llama_config.model_view(text, model_id)
            if view["engine"] == "llama.cpp" and view["editable"]:
                out.append(model_id)
    return out


class Tuner:
    """One model measured at a time, in the background; the queue goes on until it is empty."""

    def __init__(self, db, bus, chat, *, url: str, space: Callable[[], dict[str, Any]],
                 run: Callable[[list[str], Callable[[], None]], list[str]] | None = None,
                 check: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
                 gpu: Callable[[], dict[str, Any] | None] | None = None,
                 installing: Callable[[], bool] | None = None,
                 draft: Callable[[str, Path], Awaitable[Path | None]] | None = None):  # fmt: skip
        self._db = db
        self._draft = draft  # a draft model's file for a model (42.5: found and downloaded); None - none
        self.on_tuned: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None  # a result kept (42.5: drafts)
        self._bus = bus
        self._chat = chat
        self._url = url.rstrip("/")
        self._space = space
        self._run = run or self._bench
        self._check = check or self._load
        self.results: dict[str, Any] = {}
        self.job: dict[str, Any] = {"status": "idle"}  # idle | waiting | measuring | checking
        self._queue: list[tuple[str, bool]] = []
        self._task: asyncio.Task | None = None
        self._process: subprocess.Popen | None = None
        self._quiet_since = time.monotonic()
        self._gpu = gpu or (lambda: None)  # the status monitor's latest look at the graphics card
        # the installs have work (34.4): llama-bench runs past llama-swap - beside an install's trial, two models at
        # once filled the memory
        self._installing = installing or (lambda: False)
        self._halt = threading.Event()  # measuring is to stop: a chat or an install began

    async def start(self) -> None:
        self.results = await self._db.acall(load_results)

    async def stop(self) -> None:
        self._queue.clear()
        self._kill()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def _kill(self) -> None:
        """The measuring program let go - with what it started (a launcher's child would keep the port and the
        graphics card)."""
        process = self._process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(process.pid).children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    child.kill()
        with contextlib.suppress(OSError):
            process.kill()

    def request(self, model_id: str, auto: bool = False) -> dict[str, Any]:
        if model_id not in {m for m, _ in self._queue} and self.job.get("model_id") != model_id:
            self._queue.append((model_id, auto))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
        return self.state_job()

    def request_all(self, auto: bool = False) -> dict[str, Any]:
        """Every llama.cpp model of the config (after llama.cpp's update; «Подобрать для всех»)."""
        try:
            text = llama_config.config_path().read_text(encoding="utf-8")
        except OSError:
            return self.state_job()
        for model_id in tunable(text):
            self.request(model_id, auto)
        return self.state_job()

    async def rename(self, model_id: str, new_id: str) -> None:
        """A model under a new id (36.1): what was measured stays its own."""
        if model_id in self.results:
            self.results[new_id] = self.results.pop(model_id)
            await self._db.acall(lambda conn: _put(conn, RESULTS_KEY, self.results))

    def cancel(self) -> dict[str, Any]:
        self._queue.clear()
        self._kill()
        return self.state_job()

    def state_job(self) -> dict[str, Any]:
        return {**self.job, "queue": [m for m, _ in self._queue]}

    async def _work(self) -> None:
        while self._queue:
            model_id, automatic = self._queue[0]
            if automatic and not await self._db.acall(auto):
                self._queue.pop(0)
                continue
            await self._wait(automatic, model_id)
            self._queue.pop(0)
            if (await self.tune(model_id, automatic=automatic)).get("status") == "halted":
                self._queue.append((model_id, automatic))  # measured again once all is quiet

    async def _wait(self, automatic: bool, model_id: str) -> None:
        """Until no chat runs, the installs have no work (34.4) and the graphics card is quiet (by hand too, 42.4: a
        game or another program's work is in its numbers) - and, for automatic tuning, all this for IDLE_S."""
        while True:
            now = time.monotonic()
            if self._chat.busy or self._installing() or self._card_busy():
                self._quiet_since = now
            elif not automatic or now - self._quiet_since >= IDLE_S:
                return
            if self.job.get("status") != "waiting":
                self.job = {"status": "waiting", "model_id": model_id}
                await self._publish()
            await asyncio.sleep(WAIT_S)

    def _card_busy(self) -> bool:
        util = (self._gpu() or {}).get("util_pct")
        return util is not None and util >= BUSY_PCT

    async def _watch(self) -> None:
        """While measuring goes on (by hand too, 42.4): a chat or an install that starts stops it - llama-bench or
        llama-server let go; it is measured again once all is quiet."""
        while self.job.get("status") == "measuring":
            if self._chat.busy or self._installing():
                self._halt.set()
                self._kill()
                return
            await asyncio.sleep(WAIT_S)

    async def _publish(self, persist: bool = True) -> None:
        await self._bus.publish("tune.changed", {"job": self.state_job()}, persist=persist)

    async def tune(self, model_id: str, *, automatic: bool = False) -> dict[str, Any]:
        """Measures, chooses, writes and checks; the result is kept (the window shows it beside the model). Measuring a
        chat or an install stops is not a result: {"status": "halted"}, nothing kept."""
        self.job = {"status": "measuring", "model_id": model_id, "done": 0, "total": 0}
        self._halt.clear()
        watch = asyncio.create_task(self._watch())
        await self._publish()
        try:
            result = await self._tune(model_id)
        except (_Halted, TuneError, llama_config.ConfigError, GGUFError, OSError) as exc:
            if self._halt.is_set():  # stopped for a chat or an install (llama-bench killed): not a result
                log.info("tuning %s stopped: a chat or an install began; measured again later", model_id)
                self.job = {"status": "idle"}
                await self._publish()
                return {"status": "halted"}
            result = {"status": "failed", "error": str(exc)}
            log.info("tuning %s: %s", model_id, exc)
        finally:
            watch.cancel()
        result["at"] = utc_now()
        self.results[model_id] = result
        await self._db.acall(lambda conn: _put(conn, RESULTS_KEY, self.results))
        self.job = {"status": "idle"}
        await self._publish()
        if self.on_tuned is not None:
            try:  # the drafts that gave nothing go (42.5): its failure is not the tuning's
                await self.on_tuned(model_id, result)
            except Exception:
                log.exception("after tuning %s", model_id)
        return result

    async def _tune(self, model_id: str) -> dict[str, Any]:
        """llama-bench's settings, then with them where the experts go (44.2: `moe`, only a Mixture-of-Experts model),
        then guessing ahead (42.4: `spec`): each step's failure is its own, what the steps before gave stays."""
        result = await self._tune_bench(model_id)
        for key, step in (("moe", self._tune_moe), ("spec", self._tune_spec)):
            try:
                found = await step(model_id)
            except (TuneError, llama_config.ConfigError, GGUFError, OSError) as exc:
                if self._halt.is_set():
                    raise
                log.info("%s for %s: %s", key, model_id, exc)
                found = {"status": "failed", "error": str(exc)}
            if found is not None:
                result[key] = found
        return result

    async def _tune_moe(self, model_id: str) -> dict[str, Any] | None:
        """Where a Mixture-of-Experts model's experts go (44.2), with the settings llama-bench left: each placement
        (moe_variants) measured by llama-server itself - its answers and its reading of a long question - the pick
        (moe_choice) written and checked as the rest. None - not such a model, or nothing to measure. {status: better
        (written) | same, kind, settings, placed (where the command keeps the experts after it), before and after
        ({tg, pp}), measured}."""
        text = await asyncio.to_thread(llama_config.config_path().read_text, encoding="utf-8")
        view = llama_config.model_view(text, model_id)
        info = await asyncio.to_thread(model_info, Path(view["file"]))
        if not info.get("expert_bytes"):
            return None
        build = await asyncio.to_thread(llama_config.server_flags, server_path(text))
        mmproj = 0
        if view.get("mmproj"):
            with contextlib.suppress(OSError):
                mmproj = Path(view["mmproj"]).stat().st_size
        variants = moe_variants(view, info, self._space(), build, mmproj=mmproj)
        if not variants:
            return None
        params = await self._db.acall(lambda conn: sampling(conn, model_id))
        reading = not view.get("context") or view["context"] >= READ_MIN_CONTEXT
        self.job.update(status="measuring", step="moe", done=0, total=len(variants))
        await self._publish()
        await self._unload()  # the check of llama-bench's settings loaded the model in llama-swap
        measured: list[dict[str, Any]] = []
        for variant in variants:
            port = free_port()
            try:
                argv = spec_command(text, model_id, variant["changes"], build, port)
                found = await asyncio.to_thread(self._serve, argv, port, params, reading=reading)
            except (TuneError, llama_config.ConfigError) as exc:
                log.info("the experts of %s, %s: %s", model_id, variant["name"], exc)
                measured.append({**variant, "error": str(exc)})
                continue
            finally:
                self.job["done"] = self.job.get("done", 0) + 1
                await self._publish(False)
            measured.append({**variant, **found})
        log.info("the experts of %s measured: %s", model_id, measured)
        pick = moe_choice(measured)
        if "tg" not in pick:
            raise TuneError(pick.get("error") or tr("llama-server не ответил"))
        placed = {"gpu_layers": view["gpu_layers"], "cpu_moe": view["cpu_moe"], "n_cpu_moe": view["n_cpu_moe"],
                  "moe_cache_mib": view["moe_cache_mib"], **pick["changes"]}  # fmt: skip
        out = {"kind": pick["name"], "settings": pick["changes"], "placed": placed,  # 44.5: the window's «as it left»
               "before": {key: measured[0][key] for key in ("tg", "pp") if key in measured[0]},
               "after": {key: pick[key] for key in ("tg", "pp") if key in pick},
               "measured": [{key: m[key] for key in ("name", "changes", "tg", "pp", "error") if key in m}
                            for m in measured]}  # fmt: skip
        backup = await self._write(model_id, lambda now: llama_config.edit(now, model_id, pick["changes"], build=build))
        return {"status": "better", **out, "backup": backup} if backup else {"status": "same", **out}

    async def _tune_spec(self, model_id: str) -> dict[str, Any]:
        """Guessing ahead (42.4), with the settings llama-bench left: each variant (spec_variants) measured by
        llama-server itself, the pick (spec_choice) written and checked as the rest. {status: better (written) |
        same | unsupported (the build has no --spec-type), kind, spec_type, before (tokens a second without it),
        after, accepted, measured, short (what a draft lacks), draft (its file's name)}."""
        text = await asyncio.to_thread(llama_config.config_path().read_text, encoding="utf-8")
        build = await asyncio.to_thread(llama_config.server_flags, server_path(text))
        if build is None or llama_config.spelled("spec_type", build)[1] is None:
            return {"status": "unsupported"}
        view = llama_config.model_view(text, model_id)
        info = await asyncio.to_thread(model_info, Path(view["file"]))
        draft = await self._find_draft(model_id, view)
        variants, short = spec_variants(view, info, self._space(), build, draft)
        params = await self._db.acall(lambda conn: sampling(conn, model_id))
        self.job.update(status="measuring", step="spec", done=0, total=len(variants))
        await self._publish()
        await self._unload()  # the check of llama-bench's settings loaded the model in llama-swap
        measured: list[dict[str, Any]] = []
        for variant in variants:
            port = free_port()
            try:
                argv = spec_command(text, model_id, variant["changes"], build, port)
                found = await asyncio.to_thread(self._serve, argv, port, params)
                if "draft_model" in variant["changes"] and found["accepted"] is None:
                    # 42.7: llama.cpp did not take the draft (another vocabulary) and went on without guessing - its
                    # speed is the model's own and the noise, the draft held in the memory all the same
                    raise TuneError(tr("llama.cpp не взял черновую модель: {error}", error=_said(server_log(), "draft")
                                       or tr("за замер она не угадала ни одного токена")))  # fmt: skip
                if variant["changes"]["spec_type"] == "draft-mtp" and found["accepted"] is None:  # 44.3, as 42.7
                    raise TuneError(tr("llama.cpp не стал угадывать слоем MTP модели: {error}",
                                       error=_said(server_log(), "mtp")
                                       or tr("за замер он не угадал ни одного токена")))  # fmt: skip
            except (TuneError, llama_config.ConfigError) as exc:
                if variant["name"] == "none":  # nothing to compare with
                    raise
                log.info("guessing ahead for %s, %s: %s", model_id, variant["name"], exc)
                measured.append({"name": variant["name"], "error": str(exc)})
                continue
            finally:
                self.job["done"] = self.job.get("done", 0) + 1
                await self._publish(False)
            measured.append({**variant, **found})
        log.info("guessing ahead for %s measured: %s", model_id, measured)
        pick = spec_choice([m for m in measured if "tg" in m])
        out = {"kind": pick["name"], "spec_type": pick["changes"]["spec_type"], "before": measured[0]["tg"],
               "after": pick["tg"], "accepted": pick.get("accepted"), "short": short,
               "draft": draft["path"].name if draft else "",
               "measured": [{key: m[key] for key in ("name", "tg", "accepted", "error") if key in m}
                            for m in measured]}  # fmt: skip
        backup = await self._write(model_id, lambda now: llama_config.edit(now, model_id, pick["changes"], build=build))
        return {"status": "better", **out, "backup": backup} if backup else {"status": "same", **out}

    async def _find_draft(self, model_id: str, view: dict[str, Any]) -> dict[str, Any] | None:
        """The draft model to measure: the one found for this model (42.5: paired, downloaded), else the one its
        command has; with its file's header (the memory estimate)."""
        path = await self._draft(model_id, Path(view["file"])) if self._draft is not None else None
        if path is None and view["spec"]["draft"] and Path(view["spec"]["draft"]).is_file():
            path = Path(view["spec"]["draft"])
        if path is None:
            return None
        try:
            return {"path": path, "info": await asyncio.to_thread(model_info, path)}
        except (OSError, GGUFError) as exc:
            log.info("the draft %s for %s: %s", path, model_id, exc)
            return None

    async def _write(self, model_id: str, edit: Callable[[str], str | None]) -> str | None:
        """The config changed under the writers' lock - `edit` of the file as it is now, not as it was before the
        measuring (34.4: an install may have written it since - a change over the old text took away three models added
        meanwhile, 2026-11-08); then the model loaded with it (the check). It does not start - its own lines back
        (what others wrote meanwhile stays) and TuneError. The copy's name; None - nothing to change."""
        path = llama_config.config_path()
        async with llama_config.writing():
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            if model_id not in llama_config.model_blocks(text):
                raise TuneError(tr("«{model}» убрали из config.yaml, пока шёл замер", model=model_id))
            new = edit(text)
            if new is None or new == text:
                return None
            self.job.update(status="checking")
            await self._publish()
            backup = await asyncio.to_thread(llama_config.write, path, new)
            await asyncio.sleep(WATCH_S)  # llama-swap has seen the new file: the check is not answered by the old one
            ids, context = list(llama_config.model_blocks(new)), llama_config.model_view(new, model_id)["context"]
            ok, problem = await llama_config.reloaded(self._url, ids, (model_id, context))
        checked = await self._check(model_id) if ok or problem == tr(llama_config.NOT_ANSWERING) else {
            "ok": False, "error": problem}  # fmt: skip
        if not checked.get("ok"):
            async with llama_config.writing():  # its own model's lines back; what others wrote meanwhile stays
                now = await asyncio.to_thread(path.read_text, encoding="utf-8")
                back = text if now == new else llama_config.with_block(now, model_id, text)
                if now == new:
                    await asyncio.to_thread(llama_config.restore, path, backup)
                else:
                    await asyncio.to_thread(llama_config.write, path, back)
                await llama_config.reloaded(self._url, list(llama_config.model_blocks(back)))
            raise TuneError(tr("с новыми настройками модель не запустилась ({error}) — конфиг возвращён",
                               error=checked.get("error") or tr("нет ответа")))  # fmt: skip
        return backup.name

    async def _tune_bench(self, model_id: str) -> dict[str, Any]:
        path = llama_config.config_path()
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        view = llama_config.model_view(text, model_id)
        if view["engine"] != "llama.cpp" or not view["editable"]:
            raise TuneError(tr("«{model}» запускается не llama.cpp или её команда записана в одну строку — подбирать "
                               "нечего", model=model_id))  # fmt: skip
        file = Path(view["file"])
        if not file.is_file():
            raise TuneError(tr("нет файла модели: {path}", path=file))
        bench = bench_path(text)
        info = await asyncio.to_thread(model_info, file)
        threads = psutil.cpu_count(logical=False) or os.cpu_count() or 4
        depth = DEPTH if (info.get("context_trained") or 0) >= 2 * DEPTH else 0
        kinds = caches(view, info, info.get("size_bytes") or file.stat().st_size, self._space())
        self.job.update(total=sum(len(flash(k)) for k in kinds) * len(UBATCHES) * 2)  # each: a prompt and an answer
        await self._unload()
        loop = asyncio.get_running_loop()

        def progress() -> None:  # in the measuring thread
            self.job["done"] = self.job.get("done", 0) + 1
            asyncio.run_coroutine_threadsafe(self._publish(False), loop)

        measured: list[dict[str, Any]] = []
        for kind in kinds:
            lines = await asyncio.to_thread(self._run, command(bench, view, kind, threads=threads, depth=depth),
                                            progress)  # fmt: skip
            if self._halt.is_set():
                raise _Halted
            measured += results(lines)
        if not measured:
            raise TuneError(tr("llama-bench не дал замеров — подробности в журнале хаба"))
        log.info("measured %s: %s", model_id, measured)
        judged: dict[str, Any] = {}

        def edit(now: str) -> str | None:  # by the config as it is when the measuring ends
            view = llama_config.model_view(now, model_id)
            choice, before = best(measured), current(view, measured)
            judged.update(choice=choice, before=before)
            if same(view, choice) or not worth(choice, before):
                return None
            return llama_config.edit(now, model_id, changes_for(choice))

        backup = await self._write(model_id, edit)
        choice, before = judged["choice"], judged["before"]
        if backup is None:
            return {"status": "same", "before": before, "after": before or choice, "settings": {}}
        changes = changes_for(choice)
        log.info("tuned %s: %s -> %s tok/s with %s", model_id, (before or {}).get("tg"), choice["tg"], changes)
        return {"status": "better", "before": before, "after": choice, "settings": changes, "backup": backup}

    async def _unload(self) -> None:
        """The graphics card free for measuring: llama-swap lets its models go."""
        with contextlib.suppress(httpx.HTTPError):
            async with httpx.AsyncClient(timeout=30.0) as http:
                await http.post(f"{self._url}/api/models/unload")
        await asyncio.sleep(1.0)

    def _bench(self, args: list[str], progress: Callable[[], None]) -> list[str]:
        """llama-bench, its jsonl lines as they come (blocking: in a thread)."""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                         encoding="utf-8", errors="replace", creationflags=flags)  # fmt: skip
        lines: list[str] = []
        timer = threading.Timer(BENCH_TIMEOUT_S, self._kill)
        timer.start()
        try:
            assert self._process.stdout is not None
            for text in self._process.stdout:
                if text.lstrip().startswith("{"):
                    lines.append(text)
                    progress()
            error = self._process.stderr.read() if self._process.stderr else ""
            code = self._process.wait()
        finally:
            timer.cancel()
        if code:
            tail = " ".join(error.strip().splitlines()[-3:])
            if not lines:
                raise TuneError(tr("llama-bench не смог: {error}", error=tail or code))
            log.warning("llama-bench stopped (exit %s) after %d measurements: %s", code, len(lines), tail)
        return lines

    def _serve(self, argv: list[str], port: int, params: dict[str, Any], *, reading: bool = False) -> dict[str, Any]:
        """llama-server run with this command past llama-swap and its answers measured (42.4; blocking: in a thread):
        a short warm-up, then each of SPEC_PROMPTS at the model's sampling (`params`) - tokens a second by llama.cpp's
        own timings, and how many of the guessed tokens were right; `reading` - and how fast it reads LONG_PROMPT
        (44.2: `pp`); then the server is let go. A server that does not start says why in its log; stopped for a
        chat - _Halted."""
        log_path = server_log()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "wb") as out:  # not a pipe: llama-server writes a lot, and an unread pipe stops it
            self._process = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
        url = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(timeout=httpx.Timeout(CHECK_TIMEOUT_S, connect=2.0)) as http:
                self._healthy(http, url, log_path)
                body = {**params, "seed": SEED, "stream": False}
                self._ask(http, url, {**body, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 4})
                speeds, drafted, right = [], 0, 0
                for prompt in SPEC_PROMPTS:
                    timings = self._ask(http, url, {**body, "messages": [{"role": "user", "content": tr(prompt)}],
                                                    "max_tokens": SPEC_TOKENS})  # fmt: skip
                    speeds.append(float(timings["predicted_per_second"]))
                    drafted += int(timings.get("draft_n") or 0)
                    right += int(timings.get("draft_n_accepted") or 0)
                if reading:
                    timings = self._ask(http, url, {**body, "messages": [{"role": "user", "content": LONG_PROMPT}],
                                                    "max_tokens": 1})  # fmt: skip
                    read = float(timings["prompt_per_second"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if self._halt.is_set():
                raise _Halted from exc
            raise TuneError(tr("llama-server не ответил: {error}", error=_tail(log_path) or exc)) from exc
        finally:
            self._kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(10)
        out = {"tg": round(sum(speeds) / len(speeds), 1), "accepted": round(right / drafted, 2) if drafted else None}
        if reading:
            out["pp"] = round(read, 1)
        return out

    def _healthy(self, http: httpx.Client, url: str, log_path: Path) -> None:
        """Until llama-server has loaded the model (its /health answers 200); it quit - its log's last words."""
        deadline = time.monotonic() + SERVE_TIMEOUT_S
        while True:
            if self._halt.is_set():
                raise _Halted
            if self._process is None or self._process.poll() is not None:
                raise TuneError(tr("llama-server не запустился: {error}", error=_tail(log_path) or tr("нет ответа")))
            with contextlib.suppress(httpx.HTTPError):
                if http.get(f"{url}/health", timeout=2.0).status_code == 200:
                    return
            if time.monotonic() > deadline:
                raise TuneError(tr("llama-server не загрузил модель за {seconds} с", seconds=int(SERVE_TIMEOUT_S)))
            time.sleep(HEALTH_S)

    @staticmethod
    def _ask(http: httpx.Client, url: str, body: dict[str, Any]) -> dict[str, Any]:
        answer = http.post(f"{url}/v1/chat/completions", json=body)
        answer.raise_for_status()
        return answer.json()["timings"]

    async def _load(self, model_id: str) -> dict[str, Any]:
        """The model loaded with its whole context and one short answer: it fits with the new settings. llama-swap
        still rereading the file is asked again (for llama_config.RELOAD_S)."""
        started = time.monotonic()
        body = {"model": model_id, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 4}
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as http:
                while True:
                    answer = await http.post(f"{self._url}/v1/chat/completions", json=body)
                    if answer.status_code == 200 or RELOADING not in answer.text or \
                            time.monotonic() - started > llama_config.RELOAD_S:  # fmt: skip
                        break
                    await asyncio.sleep(RETRY_S)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": exc.__class__.__name__}
        ok = answer.status_code == 200 and bool((answer.json() or {}).get("choices"))
        return {"ok": ok, "error": "" if ok else f"llama-swap {answer.status_code}: {answer.text[:200]}",
                "seconds": round(time.monotonic() - started, 1)}  # fmt: skip
