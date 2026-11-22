"""What local models need on this computer, looked at on the disk (stage 39): llama-swap's program, its config, the
llama-server the config names and llama-bench beside it.

Not «who answers on llama-swap's port»: another program there answers too - Tomcat in IntelliJ on 8080 says 404, and
that was taken for llama-swap (39.1, the plan's finding 1). A llama-swap that answers as llama-swap (its /running) -
the person's own, run in a way AFO does not know - counts as there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx

from common.i18n import tr
from common.paths import engines_home

from . import autostart, llama_config, strata
from .db import utc_now
from .tuning import BENCH, server_path

if TYPE_CHECKING:
    from .setup import Setup

log = logging.getLogger("hub.resources")

ANSWER_S = 2.0
FETCHING = ("downloading", "starting")
RETRY_S = 1800.0  # a fetch that failed (no network, GitHub's 60 questions an hour): tried again this much later
SEEN_SETTING = "resources.seen"  # AFO's own engine's programs once all were there (hub.db's settings)


def scan() -> dict[str, Any]:
    """{ready, missing: [{what, path}], start, config, server} - blocking (the disk): call it in a thread. `ready`:
    llama-swap's program, its config and llama-server are there; llama-bench missing is only said - tuning needs it,
    answers do not."""
    start = autostart.llama_swap_script()
    config = llama_config.config_path()
    missing: list[dict[str, str]] = []
    if not start.is_file():
        missing.append({"what": "llama-swap", "path": str(start)})
    server = None
    if not config.is_file():
        missing.append({"what": "config", "path": str(config)})
    else:
        try:
            server = server_path(config.read_text(encoding="utf-8"))
        except OSError, llama_config.ConfigError:
            server = None
        if server is None or not str(server) or not server.is_file():
            missing.append({"what": "llama-server", "path": str(server or "")})
        elif not server.with_name(BENCH).is_file():
            missing.append({"what": "llama-bench", "path": str(server.with_name(BENCH))})
    return {"ready": all(m["what"] == "llama-bench" for m in missing), "missing": missing, "start": str(start),
            "config": str(config), "server": str(server or "")}  # fmt: skip


def afo_own() -> bool:
    """llama-swap's program and config are AFO's own - in its engines folder, as the wizard puts them, or nothing
    chosen at all: only then does AFO fetch what is missing. The person's own llama-swap (chosen in «Настройки →
    Запуск», found running, the author's script) it never touches (blocking: the disk)."""
    home = engines_home()
    return autostart.llama_swap_script().is_relative_to(home) and llama_config.config_path().is_relative_to(home)


class Resources:
    """The scanner (39.1): at the hub's start and when the window comes, what local models need is looked at on the
    disk; AFO's own engine missing - fetched through the wizard's Setup, one download at a time (Setup.start gives
    the one going on). Off in tests (HubSettings.fetch_engines): what is missing is only said."""

    def __init__(self, db, setup: Setup, *, url: str, enabled: bool):
        self._db = db
        self._setup = setup
        self._url = url
        self._enabled = enabled
        self._again: asyncio.Task | None = None  # a failed fetch's next try
        self.state: dict[str, Any] = {"status": "idle"}
        setup.on_finished = self._finished
        self._strata: asyncio.Task | None = None  # (45.2б) AFO's own Strata being put in
        self._strata_cancel = threading.Event()
        self._strata_failed: tuple[float, str] | None = None  # when and why it last failed
        self.strata_progress: tuple[int, int] = (0, 0)  # bytes fetched, of

    async def stop(self) -> None:
        self._strata_cancel.set()
        if self._strata is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._strata
        if self._again is not None:
            self._again.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._again

    async def check(self) -> dict[str, Any]:
        """{status: ready | fetching | failed | vanished | own | off, missing, words, ...}: what is there now, and a
        fetch started when AFO's own engine lacks something - not again and again: a failed one waits RETRY_S, a
        program gone after the install (an antivirus) is said, not fetched."""
        found = await asyncio.to_thread(scan)
        if found["ready"] or await llama_swap_answers(self._url):
            own = found["ready"] and await asyncio.to_thread(afo_own)
            if own:
                await self._db.acall(lambda conn: _keep_seen(conn, [found["start"], found["server"]]))
            self.state = {**found, "status": "ready", "words": "",
                          "strata": await self.strata_check() if own else {"status": "own", "words": ""}}  # fmt: skip
            return self.state
        lacking = ", ".join(m["path"] or m["what"] for m in found["missing"] if m["what"] != "llama-bench")
        if not await asyncio.to_thread(afo_own):
            self.state = {**found, "status": "own", "words": tr(
                "llama-swap — ваш собственный ({start}), и в нём нет: {missing}. AFO его не трогает: поправьте путь "
                "в «Настройки → Запуск» или верните выбор по умолчанию — тогда AFO поставит свой",
                start=found["start"], missing=lacking)}  # fmt: skip
            return self.state
        if not self._enabled:
            self.state = {**found, "status": "off", "words": tr("нет программы для локальных моделей: {missing}",
                                                                missing=lacking)}  # fmt: skip
            return self.state
        job = self._setup.job
        if job.get("status") not in FETCHING:
            seen = await self._db.acall(_seen)
            gone = [path for path in seen if path in {m["path"] for m in found["missing"]}]
            if gone:  # there once, gone since: fetched again, an antivirus would take it again
                self.state = {**found, "status": "vanished", "words": tr(
                    "{files} пропал после установки — похоже, его удалил антивирус. Добавьте папку {folder} в "
                    "исключения антивируса и нажмите «Установить движок» в мастере: сам AFO его больше не качает",
                    files=", ".join(gone), folder=engines_home())}  # fmt: skip
                return self.state
            wait = self._wait()
            if job.get("auto") and job.get("status") == "failed" and wait > 0:
                self._retry_in(wait)
                self.state = {**found, "status": "failed", "words": tr(
                    "Программа для локальных моделей не скачалась: {error}. Попробую снова через {minutes} мин — или "
                    "нажмите «Установить движок» в мастере", error=job.get("error") or "?",
                    minutes=max(1, round(wait / 60)))}  # fmt: skip
                return self.state
            build = await self._setup.recommended_build()
            log.info("missing for local models: %s - fetching the %s build", lacking, build)
            self._setup.start(build, auto=True)
        self.state = {**found, "status": "fetching", "words": tr("Скачиваю программу для локальных моделей…")}
        return self.state

    async def strata_check(self) -> dict[str, Any]:
        """(45.2б) {status: ready | fetching | failed | own | unfit | off, words}: AFO's own Strata, put in by itself
        where its smallest model fits (strata.fits: llama.cpp's CUDA build, 8 GB on the card, RAM for the experts)
        and the person has no Strata of their own; one fetch at a time, a failed one tried again RETRY_S later, at a
        look. Its models still come by a click (the user's word 2026-11-12)."""
        base = strata.home()
        if await asyncio.to_thread(strata.installed, base):
            return {"status": "ready", "words": ""}
        if self._strata is not None and not self._strata.done():
            done, total = self.strata_progress
            return {"status": "fetching", "words": tr("Скачиваю Strata… {done} из {total} МБ", done=done >> 20,
                                                      total=total >> 20)}  # fmt: skip
        if await asyncio.to_thread(strata.persons_own) is not None:
            return {"status": "own", "words": tr("Strata у вас своя — AFO свою не ставит и вашу не трогает")}
        build, vram, ram = await asyncio.to_thread(strata.computer)
        if not strata.fits(build, vram, ram):
            return {"status": "unfit", "words": ""}
        if not self._enabled:
            return {"status": "off", "words": tr("Strata не установлена")}
        if self._strata_failed is not None and time.monotonic() - self._strata_failed[0] < RETRY_S:
            return {"status": "failed", "words": tr("Strata не скачалась: {error}. Попробую снова позже",
                                                    error=self._strata_failed[1])}  # fmt: skip
        log.info("Strata's smallest model fits here (%s, %.1f GB on the card, %.1f GB of RAM): putting Strata in",
                 build, vram, ram)  # fmt: skip
        self._strata = asyncio.create_task(self._fetch_strata(base))
        return {"status": "fetching", "words": tr("Скачиваю Strata…")}

    async def _fetch_strata(self, base) -> None:
        def report(done: int, total: int) -> None:
            self.strata_progress = (done, total)

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                assets = await strata.release(http)
            await asyncio.to_thread(strata.install, assets, base, report=report, cancel=self._strata_cancel)
            self._strata_failed = None
        except Exception as exc:
            log.warning("Strata was not put in: %s", exc)
            self._strata_failed = (time.monotonic(), str(exc))

    def _wait(self) -> float:
        """How long a failed fetch still waits before the next try."""
        ended = self._setup.finished_at
        return RETRY_S - (time.monotonic() - ended) if ended is not None else 0.0

    def _finished(self, job: dict[str, Any]) -> None:
        """Setup's download ended: the scanner's own that failed is tried again later, by itself."""
        if job.get("auto") and job.get("status") == "failed":
            log.warning("the engine was not fetched (%s): again in %.0f s", job.get("error"), RETRY_S)
            self._retry_in(RETRY_S)

    def _retry_in(self, wait: float) -> None:
        if self._again is None or self._again.done():
            self._again = asyncio.create_task(self._later(wait))

    async def _later(self, wait: float) -> None:
        await asyncio.sleep(wait)
        try:
            await self.check()
        except Exception:
            log.exception("looking at the local models' programs again failed")


def _seen(conn) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SEEN_SETTING,)).fetchone()
    return json.loads(row["value"]) if row else []


def _keep_seen(conn, paths: list[str]) -> None:
    """AFO's own engine's programs, once all were there (an antivirus taking one later is told from never having it)."""
    if _seen(conn) != paths:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                     "value = excluded.value, updated_at = excluded.updated_at",
                     (SEEN_SETTING, json.dumps(paths), utc_now()))  # fmt: skip


async def llama_swap_answers(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """llama-swap answers at `url`: its /running, as the status monitor reads it - not just any program on its port."""
    try:
        async with httpx.AsyncClient(timeout=ANSWER_S, transport=transport) as http:
            response = await http.get(f"{url.rstrip('/')}/running")
        return response.status_code == 200 and isinstance(response.json().get("running"), list)
    except httpx.HTTPError, ValueError, AttributeError:
        return False
