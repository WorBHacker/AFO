"""What local models need on this computer, looked at on the disk (stage 39): llama-swap's program, its config, the
llama-server the config names and llama-bench beside it.

Not «who answers on llama-swap's port»: another program there answers too - Tomcat in IntelliJ on 8080 says 404, and
that was taken for llama-swap (39.1, the plan's finding 1). A llama-swap that answers as llama-swap (its /running) -
the person's own, run in a way AFO does not know - counts as there.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from common.i18n import tr
from common.paths import engines_home

from . import autostart, llama_config
from .tuning import BENCH, server_path

if TYPE_CHECKING:
    from .setup import Setup

log = logging.getLogger("hub.resources")

ANSWER_S = 2.0
FETCHING = ("downloading", "starting")


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

    def __init__(self, setup: Setup, *, url: str, enabled: bool):
        self._setup = setup
        self._url = url
        self._enabled = enabled
        self.state: dict[str, Any] = {"status": "idle"}

    async def check(self) -> dict[str, Any]:
        """{status: ready | fetching | own | off, missing, words, ...}: what is there now, and a fetch started when
        AFO's own engine lacks something."""
        found = await asyncio.to_thread(scan)
        if found["ready"] or await llama_swap_answers(self._url):
            self.state = {**found, "status": "ready", "words": ""}
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
        if self._setup.job.get("status") not in FETCHING:
            build = await self._setup.recommended_build()
            log.info("missing for local models: %s - fetching the %s build", lacking, build)
            self._setup.start(build, auto=True)
        self.state = {**found, "status": "fetching", "words": tr("Скачиваю программу для локальных моделей…")}
        return self.state


async def llama_swap_answers(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """llama-swap answers at `url`: its /running, as the status monitor reads it - not just any program on its port."""
    try:
        async with httpx.AsyncClient(timeout=ANSWER_S, transport=transport) as http:
            response = await http.get(f"{url.rstrip('/')}/running")
        return response.status_code == 200 and isinstance(response.json().get("running"), list)
    except httpx.HTTPError, ValueError, AttributeError:
        return False
