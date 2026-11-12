"""The first run (stage 10.2, D37, Q19): a computer with no local models gets them without a console.

What the window's wizard shows and does:
- this computer: its graphics cards (common/hardware.py) and so the llama.cpp build it wants (CUDA 13 or 12 for
  NVIDIA, Vulkan for AMD and Intel, the processor's otherwise - the person may pick another), RAM, the page file;
- the engine: when llama-swap neither answers nor has a config with llama-server, the build and llama-swap come from
  their GitHub releases into LocalChat's engines folder (hub/engines.py, sha256 checked), llama-swap gets a config of
  its own, both become the window's choice (10.3) and the hub starts llama-swap;
- the first model: for each purpose («Разговоры», «Код», «Картинки») the curated model that runs best here - it is
  installed by the model installer (10.1).
A computer that already has its llama-swap (the first one LocalChat was built on) needs none of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from common import hardware
from common.i18n import N_, decimal, tr
from common.paths import engines_home

from . import autostart, engines, llama_config, resources
from . import model_install as mi
from .system_status import memory_sample

log = logging.getLogger("hub.setup")
BUILDS = {"cuda-13": "NVIDIA, CUDA 13", "cuda-12": "NVIDIA, CUDA 12", "vulkan": "Vulkan (AMD, Intel, NVIDIA)",
          "cpu": N_("только процессор")}  # fmt: skip
PAGEFILE_GIB = 16.0  # below this a model partly in RAM may meet «not enough memory» (the commit limit, Q11)
PROGRESS_S = 1.0


def page_file(memory: dict[str, Any] | None) -> dict[str, Any]:
    """The page file's size (the commit limit over the RAM) and a word on it."""
    if not memory:
        return {"gib": None, "advice": ""}
    size = max(round((memory.get("commit_limit_gib") or 0) - (memory.get("ram_total_gib") or 0), 1), 0.0)
    shown = decimal(f"{size:g}")
    advice = "" if size >= PAGEFILE_GIB else tr(
        "Файл подкачки — {size} ГБ. Большой модели, которая не помещается в видеопамять, может не хватить памяти: "
        "в «Параметры → Система → О системе → Дополнительные параметры → Быстродействие → Виртуальная память» "
        "поставьте «Размер по выбору системы» или не меньше 16 ГБ.", size=shown)  # fmt: skip
    return {"gib": size, "advice": advice}


def suggestions(models: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """For each purpose, the curated model to offer first: the strongest that fits the video memory, else the
    smallest that runs at all."""
    out: dict[str, dict[str, Any] | None] = {}
    for purpose in ("chat", "code", "vision"):
        fitting = [m for m in models if purpose in m.get("purposes", []) and m.get("recommended")]
        fast = [m for m in fitting if m["recommended"]["verdict"] == "gpu"]
        if fast:
            out[purpose] = max(fast, key=lambda m: m["recommended"]["size"])
        else:
            out[purpose] = min(fitting, key=lambda m: m["recommended"]["size"]) if fitting else None
    return out


class Setup:
    """The first-run wizard's hub side: what this computer has, and the engine's download and start."""

    def __init__(self, db, bus, gateway, installer, *, llama_swap_url: str, logs: Path):
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self._installer = installer
        self._url = llama_swap_url
        self._logs = logs
        self._task: asyncio.Task | None = None
        self._cancel = threading.Event()
        self.job: dict[str, Any] = {"status": "idle"}  # idle | downloading | starting | done | failed | cancelled
        self.on_updated: Callable[[], Any] | None = None  # a newer llama.cpp in place: the models' tuning (15.2)
        self._shown = 0.0

    def _engine(self) -> dict[str, Any]:
        """Whether llama-swap and a config with llama-server are there."""
        config = llama_config.config_path()
        server = ""
        if config.is_file():
            with contextlib.suppress(OSError, llama_config.ConfigError):
                server = llama_config.server_command(config.read_text(encoding="utf-8"))
        start = autostart.llama_swap_script()
        return {"config": str(config), "config_found": config.is_file(), "server": server, "start": str(start),
                "start_found": start.is_file()}  # fmt: skip

    async def state(self) -> dict[str, Any]:
        cards = await asyncio.to_thread(hardware.adapters)
        nvidia = await asyncio.to_thread(hardware.nvidia_driver)
        build = hardware.build_for(cards, nvidia)
        memory = await asyncio.to_thread(memory_sample)
        pictures = await asyncio.to_thread(hardware.pictures_here, (memory or {}).get("ram_total_gib") or 0)
        engine = await asyncio.to_thread(self._engine)
        found = await asyncio.to_thread(resources.scan)  # the files, not who answers on the port (39.1)
        answering = await resources.llama_swap_answers(self._url)
        ready = found["ready"] or answering
        return {"needed": not ready, "answering": answering, "resources": found, "engine": engine, "cards": cards,
                "nvidia": nvidia, "build": build, "builds": {k: tr(v) for k, v in BUILDS.items()}, "memory": memory,
                "page_file": page_file(memory), "pictures": pictures, "job": dict(self.job),
                "folder": str(engines_home())}  # fmt: skip

    async def first_models(self) -> dict[str, Any]:
        found = suggestions(await self._installer.catalog())
        pictures = await self._installer.picture_catalog()  # «Рисовать» (29.3): the tier this computer draws on
        best = next((p for p in pictures if p["recommended"] and not p.get("error")), None)
        found["image"] = mi.picture_offer(best) if best else None
        return found

    # --- the engine ---

    def start(self, build: str, *, update: bool = False) -> dict[str, Any]:
        if build not in BUILDS:
            raise ValueError(tr("сборка — одна из {builds}", builds=", ".join(BUILDS)))
        if self._task is not None and not self._task.done():
            return dict(self.job)
        self._cancel = threading.Event()
        self.job = {"status": "downloading", "build": build, "done": 0, "total": 0, "error": "", "step": "",
                    "update": update}  # fmt: skip
        self._task = asyncio.create_task(self._update(build) if update else self._run(build))
        return dict(self.job)

    # --- a newer llama.cpp (10.4): beside the old build, the config's macro pointed at it ---

    def _server(self) -> Path | None:
        config = llama_config.config_path()
        try:
            text = config.read_text(encoding="utf-8")
            command = llama_config.server_command(text)
        except OSError, llama_config.ConfigError:
            return None
        if command.startswith("${"):
            command = llama_config.macros(text).get(command.strip("${}"), "")
        return Path(command.strip("'\"")) if command else None

    async def update_info(self) -> dict[str, Any]:
        """The build running now and the newest of its kind on GitHub."""
        server = await asyncio.to_thread(self._server)
        running = await asyncio.to_thread(engines.running_build, server) if server else None
        if running is None:
            return {"current": "", "build": "", "latest": "", "newer": False, "server": str(server or "")}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
            latest = (await engines.llama_cpp_release(http, running["build"]))["tag"]
        return {"current": running["tag"], "build": running["build"], "latest": latest,
                "newer": engines.newer(latest, running["tag"]), "server": str(server)}  # fmt: skip

    async def _update(self, build: str) -> None:
        loop = asyncio.get_running_loop()

        def report(done: int, total: int) -> None:
            self.job.update(done=done, total=total)
            now = time.monotonic()
            if now - self._shown >= PROGRESS_S:
                self._shown = now
                asyncio.run_coroutine_threadsafe(self._publish("setup.progress", False), loop)

        try:
            await self._publish()
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                cpp = await engines.llama_cpp_release(http, build)
            folder = engines_home() / "llama.cpp" / f"{cpp['tag']}-{build}"
            self.job.update(total=sum(a["size"] for a in cpp["assets"]), tag=cpp["tag"],
                            step=f"llama.cpp {cpp['tag']} ({tr(BUILDS[build])})")  # fmt: skip
            await self._publish()
            await asyncio.to_thread(self._fetch, cpp["assets"], engines_home() / "downloads", folder, 0, report)
            server = engines.server_in(folder)
            path = llama_config.config_path()
            async with llama_config.writing():  # 34.4: an install or the tuning does not write in between
                text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                command = llama_config.server_command(text)
                if not command.startswith("${"):
                    raise engines.EngineError(tr("в config.yaml llama-server указан в каждой модели, а не в макросе — "
                                                 "поменяйте путь на {server} сами", server=server))  # fmt: skip
                new = llama_config.set_macro(text, command.strip("${}"),
                                             "'\"" + str(server).replace("\\", "/") + "\"'")  # fmt: skip
                backup = await asyncio.to_thread(llama_config.write, path, new)
                ok, problem = await llama_config.reloaded(self._url, list(llama_config.model_blocks(new)))
                if not ok and problem != tr(llama_config.NOT_ANSWERING):
                    await asyncio.to_thread(llama_config.restore, path, backup)
                    raise engines.EngineError(tr("llama-swap не принял новый конфиг ({problem}) — возвращён прежний",
                                                 problem=problem))  # fmt: skip
            self.job.update(status="done", step="", backup=backup.name)
            if self.on_updated is not None:
                self.on_updated()
        except engines.EngineError as exc:
            self.job.update(status="failed", error=str(exc))
        except Exception as exc:
            if self._cancel.is_set():
                self.job.update(status="cancelled", error="")
            else:
                log.exception("the engine's update failed")
                self.job.update(status="failed", error=tr("не вышло: {error}", error=exc))
        await self._publish()

    def cancel(self) -> None:
        self._cancel.set()

    async def stop(self) -> None:
        self._cancel.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 10.0)

    async def _publish(self, kind: str = "setup.changed", persist: bool = True) -> None:
        await self._bus.publish(kind, {"job": dict(self.job)}, persist=persist)

    async def _run(self, build: str) -> None:
        home = engines_home()
        loop = asyncio.get_running_loop()

        def report(done: int, total: int) -> None:  # in the download's thread
            self.job.update(done=done, total=total)
            now = time.monotonic()
            if now - self._shown >= PROGRESS_S:
                self._shown = now
                asyncio.run_coroutine_threadsafe(self._publish("setup.progress", False), loop)

        try:
            await self._publish()
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                cpp = await engines.llama_cpp_release(http, build)
                swap = await engines.llama_swap_release(http)
            cpp_folder = home / "llama.cpp" / f"{cpp['tag']}-{build}"
            swap_folder = home / "llama-swap"
            assets = [*cpp["assets"], *swap["assets"]]
            self.job.update(total=sum(a["size"] for a in assets), tag=cpp["tag"], swap=swap["tag"],
                            step=tr("llama.cpp {tag} ({build}) и llama-swap {swap}", tag=cpp["tag"],
                                    build=tr(BUILDS[build]), swap=swap["tag"]))  # fmt: skip
            await self._publish()
            downloads = home / "downloads"
            await asyncio.to_thread(self._fetch, cpp["assets"], downloads, cpp_folder, 0, report)
            await asyncio.to_thread(self._fetch, swap["assets"], downloads, swap_folder,
                                    sum(a["size"] for a in cpp["assets"]), report)  # fmt: skip
            server = engines.server_in(cpp_folder)
            exe = swap_folder / "llama-swap.exe"
            if not exe.is_file():
                raise engines.EngineError(tr("в архиве llama-swap нет llama-swap.exe ({folder})", folder=swap_folder))
            config = swap_folder / "config.yaml"
            async with llama_config.writing():  # 39.1: as every write of the config - an install does not meet it
                if not config.exists():
                    await asyncio.to_thread(engines.write_config, config, server)
            self.job.update(status="starting", step=tr("запускаю llama-swap"))
            await self._publish()

            def choose(conn) -> None:
                autostart.set_choice(conn, llama_config.CONFIG_SETTING, str(config))
                autostart.set_choice(conn, autostart.START_SETTING, str(exe))
                autostart.set_llama_swap_wanted(conn, True)

            await self._db.acall(choose)
            started = await autostart.ensure_llama_swap(self._url, exe, True, self._logs / "llama-swap.out.log")
            with contextlib.suppress(Exception):
                await self._gateway.sync_catalog()
            if started in ("running", "started", "slow"):
                self.job.update(status="done", step="", started=started)
            elif (taken := await asyncio.to_thread(autostart.port_taken, self._url)) is not None:  # 23.4
                self.job.update(status="failed", error=tr(
                    "порт {port} занят: {name} (pid {pid}) — закройте эту программу, и хаб запустит llama-swap сам",
                    port=taken["port"], name=taken["name"], pid=taken["pid"]))  # fmt: skip
            else:
                self.job.update(status="failed", error=tr("llama-swap не запустился ({state})", state=started))
        except engines.EngineError as exc:
            self.job.update(status="failed", error=str(exc))
        except Exception as exc:
            if self._cancel.is_set():
                self.job.update(status="cancelled", error="")
            else:
                log.exception("the engine's install failed")
                self.job.update(status="failed", error=tr("не вышло: {error}", error=exc))
        await self._publish()

    def _fetch(self, assets, downloads: Path, folder: Path, base: int, report) -> None:
        engines.fetch(assets, downloads, folder, report=lambda n, _t: report(base + n, self.job["total"]),
                      cancel=self._cancel)  # fmt: skip
