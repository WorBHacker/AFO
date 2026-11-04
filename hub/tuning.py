"""Launch settings by measurement (step 15.2, D49): for a llama.cpp model in llama-swap's config, llama-bench of the
same llama.cpp build measures what a few settings give on this computer - flash attention on and off, the KV cache's
type, the ubatch - with a filled context (DEPTH tokens already in it, as a chat with some history). The fastest answer
wins (answers within CLOSE of each other: the faster prompt decides), and the config changes only when the winner is
faster than its own settings by more than CLOSE - less is the measurement's noise; the model's placement stays as the
config has it (all on the graphics card, a number of layers, or llama.cpp's own --fit). The winner goes into the
config - a copy of the old one beside it - and the model is loaded with its whole context to check it still fits; if
not, the old config comes back.

Automatic tuning (after a model is installed or added, after llama.cpp is updated) waits until no chat has run for
IDLE_S: measuring unloads the models and takes the graphics card for minutes. The person's «Подобрать» waits only for
the chats going on now."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
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
from common.i18n import tr
from common.llama_memory import kv_bytes

from . import llama_config
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
WAIT_S = 5.0
BENCH_TIMEOUT_S = 1800.0
CHECK_TIMEOUT_S = 300.0
WATCH_S = 3.0  # llama-swap --watch-config looks at the file every 2 s: a write with the same context is not seen
RELOADING = "shutting down"  # llama-swap's answer while it rereads the file («group is shutting down»)
RETRY_S = 1.0
GIB = 1024**3
COMPUTE_GIB = 1.5  # llama.cpp's buffers beside the weights and the cache (hub/model_install.py)


class TuneError(Exception):
    """The model cannot be tuned; the message is for the window."""


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
                 check: Callable[[str], Awaitable[dict[str, Any]]] | None = None):  # fmt: skip
        self._db = db
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
        if self._process is not None and self._process.poll() is None:
            with contextlib.suppress(OSError):
                self._process.kill()

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
            await self.tune(model_id)

    async def _wait(self, automatic: bool, model_id: str) -> None:
        """Until no chat runs - and, for automatic tuning, none has for IDLE_S."""
        while True:
            now = time.monotonic()
            if self._chat.busy:
                self._quiet_since = now
            elif not automatic or now - self._quiet_since >= IDLE_S:
                return
            if self.job.get("status") != "waiting":
                self.job = {"status": "waiting", "model_id": model_id}
                await self._publish()
            await asyncio.sleep(WAIT_S)

    async def _publish(self, persist: bool = True) -> None:
        await self._bus.publish("tune.changed", {"job": self.state_job()}, persist=persist)

    async def tune(self, model_id: str) -> dict[str, Any]:
        """Measures, chooses, writes and checks; the result is kept (the window shows it beside the model)."""
        self.job = {"status": "measuring", "model_id": model_id, "done": 0, "total": 0}
        await self._publish()
        try:
            result = await self._tune(model_id)
        except (TuneError, llama_config.ConfigError, GGUFError, OSError) as exc:
            result = {"status": "failed", "error": str(exc)}
            log.info("tuning %s: %s", model_id, exc)
        result["at"] = utc_now()
        self.results[model_id] = result
        await self._db.acall(lambda conn: _put(conn, RESULTS_KEY, self.results))
        self.job = {"status": "idle"}
        await self._publish()
        return result

    async def _tune(self, model_id: str) -> dict[str, Any]:
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
            measured += results(lines)
        if not measured:
            raise TuneError(tr("llama-bench не дал замеров — подробности в журнале хаба"))
        log.info("measured %s: %s", model_id, measured)
        choice, before = best(measured), current(view, measured)
        if same(view, choice) or not worth(choice, before):
            return {"status": "same", "before": before, "after": before or choice, "settings": {}}
        changes = changes_for(choice)
        self.job.update(status="checking")
        await self._publish()
        new = llama_config.edit(text, model_id, changes)
        backup = await asyncio.to_thread(llama_config.write, path, new)
        await asyncio.sleep(WATCH_S)  # llama-swap has seen the new file: the check is not answered by the old one
        ids = list(llama_config.model_blocks(new))
        ok, problem = await llama_config.reloaded(self._url, ids, (model_id, view["context"]))
        checked = await self._check(model_id) if ok or problem == tr(llama_config.NOT_ANSWERING) else {
            "ok": False, "error": problem}  # fmt: skip
        if not checked.get("ok"):
            await asyncio.to_thread(llama_config.restore, path, backup)
            await llama_config.reloaded(self._url, list(llama_config.model_blocks(text)))
            raise TuneError(tr("с новыми настройками модель не запустилась ({error}) — конфиг возвращён",
                               error=checked.get("error") or tr("нет ответа")))  # fmt: skip
        log.info("tuned %s: %s -> %s tok/s with %s", model_id, (before or {}).get("tg"), choice["tg"], changes)
        return {"status": "better", "before": before, "after": choice, "settings": changes, "backup": backup.name}

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
