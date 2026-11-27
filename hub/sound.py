"""The sound added to a film (stage 53): the narrator's voice, music, a scene's sounds - made by audio.cpp
(hub/audio_cpp.py), a program the hub runs once for each piece. The fourth part is the listener (53.11): it says
what is heard in a sound - on the processor, the card not taken.

Its parts are fetched when the user asks for them (`install`): the engine's build for this computer the first time
- with any part - and the part's model, a GGUF file of some gigabytes into <models>/audio. What is on the disk is
the state: nothing is kept in the database; a download a stopped hub left goes on from where it was when asked
again. The window is told by `sound.changed`.

A piece is made on the graphics card (the processor's build - on the processor), one at a time. The card is taken
as any holder takes it (hub/gpu_card.py, SOUND), local chats waiting; the models llama-swap holds are unloaded
first - a video model fills the card, and audio.cpp runs past llama-swap. The one who asks holds the card for all
its pieces in a row (`working`) and gives it when they are made.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import threading
import wave
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from common.i18n import decimal, tr

from . import audio_cpp, hf
from .engines import EngineError
from .events import EventBus
from .gpu_card import SOUND, GpuCard

log = logging.getLogger(__name__)
ROOM = 1 << 30  # left free on the disk after a download
TICK_S = 0.5  # how often a download is told to the window


class SoundError(Exception):
    """A piece of sound that cannot be made, a part that cannot be fetched; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def seconds_of(path: Path) -> float:
    """How long a WAV the engine made is (blocking)."""
    with contextlib.closing(wave.open(str(path), "rb")) as sound:
        return sound.getnframes() / float(sound.getframerate() or 1)


class Sound:
    """The engine's parts (`state`, `install`, `stop`, `remove`) and its pieces: `speak`, `music`, `effect` -
    inside `working`, which holds the card; `listen` - what is heard in a sound, without the card."""

    def __init__(self, bus: EventBus, *, card: GpuCard, llama_swap_url: str, tools: Path, models: Path,
                 build: Callable[[], Awaitable[str]]):  # fmt: skip
        self._bus = bus
        self._card = card
        self._url = llama_swap_url.rstrip("/")
        self.tools = tools
        self.models = models
        # the build this computer's card wants (the setup's): cuda-13 | cuda-12 | vulkan | cpu; tests give their own
        self.machine = build
        self._build: str | None = None
        self._one = asyncio.Lock()  # one holder of the engine at a time
        self._engine = asyncio.Lock()  # one fetch of the engine
        self._loads: dict[str, dict[str, Any]] = {}  # part -> its download: got, total, going, error...

    async def build(self) -> str:
        if self._build is None:
            self._build = audio_cpp.build_of(await self.machine())
        return self._build

    # --- the parts ---

    def _part(self, part: str, engine: bool) -> dict[str, Any]:
        spec, load = audio_cpp.PARTS[part], self._loads.get(part) or {}
        going = bool(load.get("going"))
        ready = engine and audio_cpp.has(self.models, part)
        return {"id": part, "name": tr(spec["name"]), "about": tr(spec["about"]), "size": spec["size"],
                "licence": spec["licence"], "state": "loading" if going else "ready" if ready else "missing",
                "got": load.get("got", 0) if going else 0, "total": load.get("total", spec["size"]),
                "speed": round(load.get("speed", 0)) if going else 0,
                "error": "" if going else load.get("error", "")}  # fmt: skip

    async def state(self) -> dict[str, Any]:
        """What the window shows: the engine's build and whether it is here, each part - ready, loading (how far),
        missing (its size) - and the voices."""
        build = await self.build()
        engine = await asyncio.to_thread(audio_cpp.find, self.tools, build) is not None
        parts = await asyncio.to_thread(lambda: [self._part(p, engine) for p in audio_cpp.PARTS])
        return {"engine": {"tag": audio_cpp.TAG, "build": build, "ready": engine}, "parts": parts,
                "voices": [{"id": k, "name": tr(v)} for k, v in audio_cpp.VOICES.items()]}  # fmt: skip

    async def ready(self, part: str) -> bool:
        build = await self.build()
        return await asyncio.to_thread(
            lambda: audio_cpp.find(self.tools, build) is not None and audio_cpp.has(self.models, part))  # fmt: skip

    async def _changed(self) -> dict[str, Any]:
        state = await self.state()
        await self._bus.publish("sound.changed", state, persist=False)
        return state

    def _known(self, part: str) -> None:
        if part not in audio_cpp.PARTS:
            raise SoundError(tr("нет такой части звука"), 404)

    async def install(self, part: str, *, agreed: bool = False) -> dict[str, Any]:
        """«Скачать»: the engine, if it is not here, and the part's model - in the background. The state as it
        begins; no room on the disk - refused at once. (62) A part whose licence forbids commercial use - not without
        the person's `agreed` (hf.NotForWork); a download stopped and asked again goes on without it."""
        self._known(part)
        load = self._loads.get(part)
        if load is not None and load["going"]:
            return await self.state()
        if await self.ready(part):
            return await self.state()
        spec = audio_cpp.PARTS[part]
        if load is None and not agreed and hf.not_for_work(spec["licence"], spec.get("commercial")):
            raise hf.NotForWork(tr(spec["name"]), spec["licence"])
        size = audio_cpp.PARTS[part]["size"]
        await asyncio.to_thread((self.models / "audio").mkdir, parents=True, exist_ok=True)
        free = (await asyncio.to_thread(shutil.disk_usage, self.models / "audio")).free
        if free < size + ROOM:
            raise SoundError(tr("на диске мало места: нужно {need} ГБ, свободно {free} ГБ",
                                need=decimal(f"{(size + ROOM) / 2**30:.1f}"),
                                free=decimal(f"{free / 2**30:.1f}")), 507)  # fmt: skip
        load = self._loads[part] = {"got": 0, "total": size, "stage": "engine", "error": "", "going": True,
                                    "cancel": threading.Event()}  # fmt: skip
        load["task"] = asyncio.create_task(self._install(part, load))
        return await self._changed()

    async def _told(self, work: Awaitable[Any], load: dict[str, Any]) -> Any:
        """A download in a thread, told to the window as it goes - with its speed."""
        task = asyncio.ensure_future(work)
        while not task.done():
            await asyncio.wait({task}, timeout=TICK_S)
            hf.measure(load)
            await self._changed()
        return task.result()

    async def _engine_here(self, load: dict[str, Any]) -> None:
        build = await self.build()
        async with self._engine:
            if await asyncio.to_thread(audio_cpp.find, self.tools, build) is not None:
                return
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                found = await audio_cpp.latest(http, build)

            def report(got: int, total: int) -> None:
                load["got"], load["total"] = got, total

            await self._told(asyncio.to_thread(audio_cpp.install, found, self.tools, load["cancel"], report), load)

    async def _install(self, part: str, load: dict[str, Any]) -> None:
        try:
            await self._engine_here(load)
            load |= {"stage": "model", "got": 0, "total": audio_cpp.PARTS[part]["size"]}
            if not await asyncio.to_thread(audio_cpp.has, self.models, part):
                await self._told(asyncio.to_thread(
                    audio_cpp.fetch_model, self.models, part, load["cancel"],
                    lambda got: load.__setitem__("got", got)), load)  # fmt: skip
        except hf.Cancelled:
            pass  # «Стоп»: what came stays on the disk for the next time
        except (EngineError, hf.HubError, httpx.HTTPError, OSError) as exc:
            load["error"] = str(exc) or type(exc).__name__
        except Exception:
            log.exception("sound part %s", part)
            load["error"] = tr("внутренняя ошибка хаба — подробности в журнале")
        finally:
            load["going"] = False  # before the window is told: the last word is not «loading»
            await self._changed()

    async def stop(self, part: str) -> dict[str, Any]:
        """A part's download stopped; what came stays for the next time."""
        self._known(part)
        if (load := self._loads.get(part)) is not None and load["going"]:
            load["cancel"].set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await load["task"]
        return await self.state()

    async def remove(self, part: str) -> dict[str, Any]:
        """A part's model gone from the disk (the engine stays: it is small and the other parts' too)."""
        await self.stop(part)
        self._loads.pop(part, None)
        await asyncio.to_thread(audio_cpp.remove, audio_cpp.model_file(self.models, part))
        return await self._changed()

    async def close(self) -> None:
        for part in list(self._loads):
            await self.stop(part)

    # --- the pieces ---

    async def _unload(self) -> None:
        """The models llama-swap holds leave the card: the engine runs past it and needs the room."""
        with contextlib.suppress(httpx.HTTPError):
            async with httpx.AsyncClient(timeout=60.0) as http:
                await http.post(f"{self._url}/api/models/unload")

    @contextlib.asynccontextmanager
    async def working(self, cancel: asyncio.Event) -> AsyncIterator[None]:
        """The card for the pieces made inside: taken once, the models unloaded, given at the end. `cancel` - the
        work's «Стоп»: set by whoever takes the card from it («Выгрузить»), and it ends the wait for the card too -
        a film waiting behind the Studio's video stops at once."""
        async with self._one:
            taking = asyncio.ensure_future(self._card.take(SOUND, chats=True, stop=cancel.set))
            waiting = asyncio.ensure_future(cancel.wait())
            try:
                await asyncio.wait({taking, waiting}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiting.cancel()
                if not taking.done():  # «Стоп» came first - or the hub closes
                    taking.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await taking
                    if not taking.cancelled():  # the card came in that very instant
                        self._card.give(SOUND)
            if taking.cancelled():
                raise SoundError(tr("остановлено"))
            try:
                if audio_cpp.backend_of(await self.build()) != "cpu":
                    await self._unload()
                yield
            finally:
                self._card.give(SOUND)

    async def _run[T](self, part: str, cancel: asyncio.Event,
                      work: Callable[[Path, str, threading.Event], T]) -> T:  # fmt: skip
        """The engine's program at a part's work, in a thread: `work(program, backend, halt)`; `cancel` kills it."""
        build = await self.build()
        program = await asyncio.to_thread(audio_cpp.find, self.tools, build)
        if program is None or not await asyncio.to_thread(audio_cpp.has, self.models, part):
            raise SoundError(tr("«{part}» не установлено: «Звук фильма» → «Скачать»",
                                part=tr(audio_cpp.PARTS[part]["name"])), 422)  # fmt: skip
        halt = threading.Event()
        going = asyncio.ensure_future(asyncio.to_thread(work, program, audio_cpp.backend_of(build), halt))
        waiting = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({going, waiting}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiting.cancel()
            if not going.done():
                halt.set()  # «Стоп», or the hub closes: the program is killed, its thread ends
        try:
            return await going
        except audio_cpp.Stopped as exc:
            raise SoundError(str(exc)) from None
        except EngineError as exc:
            raise SoundError(str(exc), 502) from None

    async def _make(self, part: str, target: Path, cancel: asyncio.Event, args: Callable[[str], list[str]]) -> Path:
        await self._run(
            part, cancel, lambda program, backend, halt: audio_cpp.run(program, args(backend), target, halt)
        )
        return target

    async def listen(self, path: Path, cancel: asyncio.Event) -> list[tuple[str, float]]:
        """What is heard in a sound file (a WAV): the listener's labels, the surest first - [(AudioSet's name, how
        sure: 0..1)]. On the processor: the card is not taken, and `working` is not needed."""
        args = audio_cpp.listen_args(self.models, audio=path)
        return await self._run("listener", cancel, lambda program, _backend, halt: audio_cpp.hear(program, args, halt))

    async def speak(self, target: Path, cancel: asyncio.Event, *, text: str, voice: str) -> Path:
        """A line read by a voice into `target` (WAV). Inside `working`. (53.8) A reading that came in another
        voice than the clip's, or did not end by itself, is not the line: it is read again by the next seed,
        `SAY_TAKES` times at most - the nearest to the voice is kept."""
        takes: list[tuple[float, Path]] = []
        try:
            for n in range(audio_cpp.SAY_TAKES):
                take = target.with_name(f"{target.stem}.take{n}{target.suffix}")
                args = lambda b, n=n: audio_cpp.speak_args(self.models, b, text=text, voice=voice, take=n)  # noqa: E731
                await self._make("voice", take, cancel, args)
                takes.append((await asyncio.to_thread(audio_cpp.amiss, take, text=text, voice=voice), take))
                if takes[-1][0] <= audio_cpp.PITCH_OFF:
                    break
            await asyncio.to_thread(os.replace, min(takes, key=lambda t: t[0])[1], target)
        finally:
            for _far, take in takes:
                with contextlib.suppress(OSError):
                    await asyncio.to_thread(take.unlink, True)
        return target

    async def music(self, target: Path, cancel: asyncio.Event, *, words: str, seconds: float) -> Path:
        """Music without words by its description into `target` (WAV). Inside `working`."""
        args = lambda b: audio_cpp.music_args(self.models, b, words=words, seconds=seconds)  # noqa: E731
        return await self._make("music", target, cancel, args)

    async def effect(self, target: Path, cancel: asyncio.Event, *, words: str, seconds: float,
                     take: int = 0) -> Path:  # fmt: skip
        """What is heard in a scene, by its description, into `target` (WAV). Inside `working`. `take` (53.13) -
        which making of it: each is another sound."""
        args = lambda b: audio_cpp.effect_args(self.models, b, words=words, seconds=seconds, take=take)  # noqa: E731
        return await self._make("effects", target, cancel, args)
