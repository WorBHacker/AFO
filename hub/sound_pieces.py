"""«Аудио» in the Studio («Стабилизация», portion 5; the user's complaint 15 of 2026-11-27, «Нет в Студии
"Аудио"»): a voice reading a text, music by its description, a sound by its description - each a WAV of its own in
data/media, listed newest first; the window plays it and saves it where the person says.

The engine of sound makes them (hub/sound.py): its parts are the film's - fetched in the Studio too («Скачать») -,
one piece at a time, the card held as a film's sound holds it (`Sound.working`: the models llama-swap holds unloaded
first, local chats waiting). The same words made again give another piece: `take` - a seed of its own. A hub that
stopped with pieces going or waiting fails them when it starts again; ✕ stops the one going on or takes one waiting
out of the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.i18n import N_, tr

from . import audio_cpp
from .db import Database, utc_now
from .events import EventBus
from .sound import Sound, SoundError, seconds_of

log = logging.getLogger(__name__)
PARTS = {"voice": "voice", "music": "music", "effect": "effects"}  # a piece's kind -> the part of the engine it needs
WORDS = 2000  # the longest text read, the longest description
LENGTHS = {"music": audio_cpp.MUSIC_S, "effect": audio_cpp.EFFECT_S}  # seconds asked for
DEFAULT_S = {"music": 30.0, "effect": 5.0}
UNLINK_TRIES = 20  # a tenth of a second each: a file the player has not let go yet
EMPTY = {"voice": N_("нужен текст, который прочитать"), "music": N_("нужно описание музыки"),
         "effect": N_("нужно описание звука")}  # fmt: skip


class PieceError(Exception):
    """A piece that cannot be asked for, or a step on one; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _row(row) -> dict[str, Any]:
    return dict(row)


def pieces(conn, limit: int = 50) -> list[dict[str, Any]]:
    """The latest pieces, the newest first."""
    rows = conn.execute("SELECT * FROM sound_pieces ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def piece(conn, piece_id: str) -> dict[str, Any] | None:
    found = conn.execute("SELECT * FROM sound_pieces WHERE id = ?", (piece_id,)).fetchone()
    return _row(found) if found else None


def _update(conn, piece_id: str, **fields: Any) -> None:
    fields["updated_at"] = utc_now()
    names = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE sound_pieces SET {names} WHERE id = ?", (*fields.values(), piece_id))


class Pieces:
    """The Studio's pieces of sound: `make` - one asked for, into the queue; `cancel`, `remove`, `file`."""

    def __init__(self, db: Database, bus: EventBus, sound: Sound, media: Path):
        self._db = db
        self._bus = bus
        self.sound = sound
        self.media = media
        self._task: asyncio.Task | None = None
        self._running: str | None = None  # the piece going on
        self._stop: asyncio.Event | None = None  # its «Стоп»
        self._closing = False

    async def start(self) -> int:
        """Pieces a stopped hub left going or waiting are failed: the engine went with it."""

        def fail(conn) -> int:
            return conn.execute("UPDATE sound_pieces SET status = 'failed', error = ?, updated_at = ? WHERE status IN "
                                "('queued', 'running')",
                                (tr("хаб перезапускался — звук не сделан"), utc_now())).rowcount  # fmt: skip

        return await self._db.acall(fail)

    async def stop(self) -> None:
        """The hub stops: no next piece; the one going on stops; those waiting are failed when it starts again."""
        self._closing = True
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 10.0)

    async def make(self, spec: dict[str, Any], client: str) -> dict[str, Any]:
        """A piece asked for: {kind - voice, music or effect; words - the text read, or the description; voice - a
        voice's (audio_cpp.VOICES); seconds - music's or a sound's length}. Its part not here - refused with words."""
        kind = str(spec.get("kind") or "")
        if kind not in PARTS:
            raise PieceError(tr("нет такого звука: {kind}", kind=kind), 422)
        words = " ".join(str(spec.get("words") or "").split())
        if not words:
            raise PieceError(tr(EMPTY[kind]), 422)
        if len(words) > WORDS:
            raise PieceError(tr("слишком длинно: не больше {count} знаков", count=WORDS), 422)
        voice, seconds = "", None
        if kind == "voice":
            voice = str(spec.get("voice") or next(iter(audio_cpp.VOICES)))
            if voice not in audio_cpp.VOICES:
                raise PieceError(tr("нет такого голоса: {voice}", voice=voice), 422)
        else:
            low, high = LENGTHS[kind]
            seconds = float(spec.get("seconds") or DEFAULT_S[kind])
            if not low <= seconds <= high:
                raise PieceError(tr("длина — от {low:g} до {high:g} с", low=low, high=high), 422)
        part = PARTS[kind]
        if not await self.sound.ready(part):
            raise PieceError(tr("«{part}» ещё не скачано — «Скачать» рядом", part=tr(audio_cpp.PARTS[part]["name"])),
                             422)  # fmt: skip
        piece_id = uuid.uuid4().hex[:12]

        def add(conn) -> None:
            take = conn.execute("SELECT COUNT(*) FROM sound_pieces WHERE kind = ? AND words = ? AND voice = ?",
                                (kind, words, voice)).fetchone()[0]  # fmt: skip
            conn.execute(
                "INSERT INTO sound_pieces (id, kind, words, voice, seconds, take, status, client) VALUES "
                "(?, ?, ?, ?, ?, ?, 'queued', ?)",
                (piece_id, kind, words, voice, seconds, take, client),
            )

        await self._db.acall(add)
        await self._publish(piece_id)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
        return await self._db.acall(lambda conn: piece(conn, piece_id))

    async def cancel(self, piece_id: str) -> dict[str, Any]:
        """✕: one waiting - out of the queue; the one going on - stopped (nothing of it stays)."""
        found = await self._db.acall(lambda conn: piece(conn, piece_id))
        if found is None:
            raise PieceError(tr("нет такого звука"), 404)
        if self._running == piece_id and self._stop is not None:  # going on - or the worker's, waiting for the card
            self._stop.set()
        if found["status"] == "queued":
            await self._set(piece_id, status="cancelled")
        return await self._db.acall(lambda conn: piece(conn, piece_id))

    async def remove(self, piece_id: str) -> None:
        """A piece gone for good, its file too; one going on is stopped first."""
        found = await self._db.acall(lambda conn: piece(conn, piece_id))
        if found is None:
            raise PieceError(tr("нет такого звука"), 404)
        if found["status"] in ("queued", "running"):
            await self.cancel(piece_id)
            if self._running == piece_id and self._task is not None:  # its program killed, its file not yet written
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._ended(piece_id), 15.0)
        if found["file"]:
            await self._unlink(self.media / found["file"])
        await self._db.acall(lambda conn: conn.execute("DELETE FROM sound_pieces WHERE id = ?", (piece_id,)))
        await self._bus.publish("sound.piece", {"piece": {**found, "status": "removed"}})

    @staticmethod
    async def _unlink(path: Path) -> None:
        """A piece's file off the disk. Windows keeps a file being played: the window's player lets it go a moment
        after it is told - tried for two seconds, then said."""
        for _ in range(UNLINK_TRIES):
            try:
                await asyncio.to_thread(path.unlink, True)
                return
            except PermissionError:
                await asyncio.sleep(0.1)
        raise PieceError(tr("файл звука занят другой программой — закройте её и удалите снова"), 409)

    async def _ended(self, piece_id: str) -> None:
        while self._running == piece_id:
            await asyncio.sleep(0.05)

    def file(self, found: dict[str, Any]) -> Path | None:
        if found.get("status") != "done" or not found.get("file"):
            return None
        path = self.media / found["file"]
        return path if path.is_file() else None

    async def _publish(self, piece_id: str) -> None:
        found = await self._db.acall(lambda conn: piece(conn, piece_id))
        if found is not None:
            await self._bus.publish("sound.piece", {"piece": found})

    async def _set(self, piece_id: str, **fields: Any) -> None:
        await self._db.acall(lambda conn: _update(conn, piece_id, **fields))
        await self._publish(piece_id)

    async def _work(self) -> None:
        while not self._closing:

            def oldest(conn):
                found = conn.execute("SELECT * FROM sound_pieces WHERE status = 'queued' ORDER BY created_at "
                                     "LIMIT 1").fetchone()  # fmt: skip
                return _row(found) if found else None

            found = await self._db.acall(oldest)
            if found is None:
                return
            try:
                self._running, self._stop = found["id"], asyncio.Event()
                await self._run(found, self._stop)
            except Exception:  # one piece's trouble must not stop the rest
                log.exception("sound piece %s", found["id"])
                await self._set(found["id"], status="failed",
                                error=tr("внутренняя ошибка хаба — подробности в журнале"))  # fmt: skip
            finally:
                self._running = self._stop = None

    async def _run(self, found: dict[str, Any], stop: asyncio.Event) -> None:
        piece_id, kind = found["id"], found["kind"]
        name = f"{datetime.now(UTC):%Y-%m}/{piece_id}.wav"
        target = self.media / name
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        start = asyncio.get_running_loop().time()
        try:
            async with self.sound.working(stop):
                if stop.is_set():  # ✕ while it waited for the card
                    raise SoundError(tr("остановлено"))
                await self._set(piece_id, status="running", error="")
                if kind == "voice":
                    await self.sound.speak(target, stop, text=found["words"], voice=found["voice"])
                elif kind == "music":
                    await self.sound.music(target, stop, words=found["words"], seconds=found["seconds"],
                                           take=found["take"])  # fmt: skip
                else:
                    await self.sound.effect(target, stop, words=found["words"], seconds=found["seconds"],
                                            take=found["take"])  # fmt: skip
        except SoundError as exc:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(target.unlink, True)
            stopped = stop.is_set()
            await self._set(piece_id, status="cancelled" if stopped else "failed", error="" if stopped else str(exc))
            return
        length = await asyncio.to_thread(seconds_of, target)
        await self._set(piece_id, status="done", file=name, seconds=round(length, 2),
                        took=round(asyncio.get_running_loop().time() - start, 1))  # fmt: skip
