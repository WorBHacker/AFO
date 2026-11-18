"""«Режиссёр» (stage 50): a film made of scenes. The user gives an idea, a length and the product's photo; a model
writes the storyboard - the scenes, each with its words and its prompt; the Studio's video model films them one
after another, a scene going on from the last frame of the one before; ffmpeg joins them into one MP4.

A film and its scenes are rows (films, film_scenes); a scene's clip is a job of the Studio (visual_jobs). The files
lie in data/media: the photo (film-<id>-photo.png), the clips (their jobs'), the joined film (film-<id>.mp4) - the
cleanup of old pictures leaves them alone while the film is there (`kept`, hub/retention.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.i18n import tr
from common.picture_sizes import SECONDS, frames_for

from . import model_settings, visual
from .db import Database, utc_now
from .events import EventBus
from .judge import Judge, JudgeError

ROLE = "director"  # the role of the model that writes storyboards (model_settings.ROLES)
SCENES = (1, 16)  # a film's scenes, at least and at most
SCENE_S = (1.0, SECONDS[1])  # a scene's length, seconds
NEAR = 0.2  # a storyboard this far from the length asked is asked again, once
WRITE_TIMEOUT_S = 180.0  # one answer of the director's
TEXT_CHARS = {"prompt": 2000, "words": 400}
SYSTEM = """You are the director of a short product video. You get an idea, the length of the film and - when one is \
attached - a photo of the product. Write the storyboard: the scenes in order. A video model films each scene from \
your prompt, a few seconds each; the scenes are then joined into one film.

How a scene may begin (its "mode"):
{modes}

Rules:
- Each scene is {low:g} to {high:g} seconds long; together they are the length asked for.
- "prompt": English, one paragraph - the subject, what moves and how, the camera (static, slow push in, orbit, \
pull back...), the light, the style. One action a scene. Describe the product as the photo shows it; invent no \
text, logos or labels on it.
- "words": one short sentence saying what happens in the scene, in the language of the idea - the user reads it.
- Keep the product the same from scene to scene: the same thing, the same colours, the same place unless the idea \
moves it.

Answer with the JSON object only: {form}"""  # i18n: keep
MODE_WORDS = {
    "picture": '- "picture": the scene begins with the product\'s photo as its first frame - the product itself, as '
               "it is.",
    "continue": '- "continue": the scene goes on from the last frame of the scene before - one unbroken shot. Not for '
                "the first scene.",
    "frames": '- "frames": the scene goes from the last frame of the scene before to the product\'s photo as its last '
              "frame - a return to the product. Not for the first scene.",
    "text": '- "text": by your words alone, nothing shown to the video model.',
}  # fmt: skip
FORM = ('{{"scenes": [{{"mode": {modes}, "seconds": <number>, "words": "...", "prompt": "..."}}, ...]}} - '
        "{seconds} seconds in all")  # fmt: skip
SHAPES = ("wide", "tall", "square")
MODES = ("text", "picture", "continue", "frames")  # how a scene begins: film_scenes.mode
FILM_FIELDS = ("idea", "seconds", "shape", "photo", "director", "model_id", "status", "error", "file", "fade",
               "tokens", "cost_usd")  # fmt: skip
SCENE_FIELDS = ("mode", "words", "prompt", "frames", "seed", "job_id", "status", "error")


def _film(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    scenes = conn.execute("SELECT * FROM film_scenes WHERE film_id = ? ORDER BY n", (row["id"],)).fetchall()
    return {**dict(row), "scenes": [{k: s[k] for k in s.keys() if k != "film_id"} for s in scenes]}  # noqa: SIM118


def film(conn: sqlite3.Connection, film_id: str) -> dict[str, Any] | None:
    """A film with its scenes in order, or None."""
    row = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
    return _film(conn, row) if row else None


def films(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """The films, the newest first, each with its scenes."""
    rows = conn.execute("SELECT * FROM films ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return [_film(conn, row) for row in rows]


def new_film(conn: sqlite3.Connection, *, idea: str, seconds: int, shape: str = "wide", photo: str = "",
             director: str = "", model_id: str = "") -> dict[str, Any]:  # fmt: skip
    """A film begun: a draft, no scenes yet."""
    film_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO films (id, idea, seconds, shape, photo, director, model_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (film_id, idea, seconds, shape, photo, director, model_id),
    )
    return film(conn, film_id)


def update_film(conn: sqlite3.Connection, film_id: str, **fields: Any) -> None:
    unknown = set(fields) - set(FILM_FIELDS)
    if unknown:
        raise ValueError(f"not a film's field: {sorted(unknown)}")
    names = ", ".join(f"{k} = ?" for k in (*fields, "updated_at"))
    conn.execute(f"UPDATE films SET {names} WHERE id = ?", (*fields.values(), utc_now(), film_id))


def set_scenes(conn: sqlite3.Connection, film_id: str, scenes: list[dict[str, Any]]) -> None:
    """The storyboard written (or written anew): these scenes, numbered from 1, in place of the ones before - none
    of them filmed yet."""
    conn.execute("DELETE FROM film_scenes WHERE film_id = ?", (film_id,))
    conn.executemany(
        "INSERT INTO film_scenes (film_id, n, mode, words, prompt, frames, seed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(film_id, n, s["mode"], s.get("words", ""), s["prompt"], int(s["frames"]), s.get("seed"))
         for n, s in enumerate(scenes, start=1)],
    )  # fmt: skip
    conn.execute("UPDATE films SET updated_at = ? WHERE id = ?", (utc_now(), film_id))


def update_scene(conn: sqlite3.Connection, film_id: str, n: int, **fields: Any) -> None:
    unknown = set(fields) - set(SCENE_FIELDS)
    if unknown:
        raise ValueError(f"not a scene's field: {sorted(unknown)}")
    names = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE film_scenes SET {names} WHERE film_id = ? AND n = ?", (*fields.values(), film_id, n))
    conn.execute("UPDATE films SET updated_at = ? WHERE id = ?", (utc_now(), film_id))


def delete_film(conn: sqlite3.Connection, film_id: str) -> None:
    """The film and its scenes gone; its files stay until the cleanup of old pictures comes to them."""
    conn.execute("DELETE FROM films WHERE id = ?", (film_id,))


class DirectorError(Exception):
    """A film that cannot be made as asked; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def role_model(conn: sqlite3.Connection) -> str | None:
    """The model of the role «Режиссёр», if one is chosen."""
    roles = model_settings.current(conn, model_settings.ALL)[1].get("roles") or {}
    return roles.get(ROLE) or None


def allowed(modes: list[str], photo: bool) -> list[str]:
    """How this film's scenes may begin: by what its video model films (`modes`: text, picture, frames) and whether
    there is a photo. To go on from a frame the model must film by a picture."""
    out = ["picture"] if photo and "picture" in modes else []
    out += ["continue"] if "picture" in modes else []
    out += ["frames"] if photo and "frames" in modes else []
    return out + (["text"] if "text" in modes else [])


def question(idea: str, seconds: int, shape: str, modes: list[str], photo: bool) -> tuple[str, str, str]:
    """What the director is asked: (system, prompt, form) - the form is said again when the answer is not it."""
    can = allowed(modes, photo)
    form = FORM.format(modes=" | ".join(f'"{m}"' for m in can), seconds=seconds)
    system = SYSTEM.format(modes="\n".join(MODE_WORDS[m] for m in can), low=SCENE_S[0], high=SCENE_S[1], form=form)
    frame = {"wide": "wide (landscape)", "tall": "tall (portrait, for a phone)", "square": "square"}[shape]
    prompt = (f"<idea>\n{idea}\n</idea>\nThe film: {seconds} seconds, {frame}. "
              + ("The product's photo is attached." if photo else "There is no photo of the product.")
              + " Write the storyboard. Answer with the JSON only.")  # fmt: skip
    return system, prompt, form


def reader(*, clip: dict[str, Any], modes: list[str], photo: bool, seconds: int) -> Callable[[str], dict | None]:
    """What reads the director's answer: {scenes: [{mode, words, prompt, frames}], notes} - a scene's seconds into
    the frames the family's VAE takes (`clip`: fps, step), its length within a scene's, a mode this film cannot
    film put right; None - no storyboard, asked again: no JSON, no scenes or too many, a scene with no prompt, and -
    the first time only - a length far from the one asked (the second is taken as it is, and said)."""
    can, fps, step, tries = allowed(modes, photo), int(clip["fps"]), int(clip["step"]), [0]

    def mode_of(asked: str, first: bool) -> str:
        if asked in can and not (first and asked in ("continue", "frames")):
            return asked
        order = ("picture", "text") if first else ("continue", "picture", "text")
        return next(m for m in order if m in can)

    def read(answer: str) -> dict[str, Any] | None:
        tries[0] += 1
        start, end = answer.find("{"), answer.rfind("}")
        try:
            data = json.loads(answer[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
        listed = data.get("scenes") if isinstance(data, dict) else None
        if not isinstance(listed, list) or not SCENES[0] <= len(listed) <= SCENES[1]:
            return None
        scenes = []
        for item in listed:
            prompt = " ".join(str(item.get("prompt") or "").split()) if isinstance(item, dict) else ""
            if not prompt:
                return None
            try:
                length = float(item.get("seconds") or 0) or float(item.get("frames") or 0) / fps
            except TypeError, ValueError:
                return None
            length = min(SCENE_S[1], max(SCENE_S[0], length or SCENE_S[1]))
            words = " ".join(str(item.get("words") or "").split()) or prompt
            scenes.append({"mode": mode_of(str(item.get("mode") or ""), not scenes),
                           "words": words[: TEXT_CHARS["words"]], "prompt": prompt[: TEXT_CHARS["prompt"]],
                           "frames": frames_for(length, fps, step)})  # fmt: skip
        got = sum(s["frames"] - 1 for s in scenes) / fps
        notes = []
        if abs(got - seconds) > seconds * NEAR:
            if tries[0] == 1:
                return None
            notes.append(tr("сценарий вышел на {got} с вместо {asked} с", got=round(got), asked=seconds))
        return {"scenes": scenes, "notes": notes}

    return read


def kept(conn: sqlite3.Connection) -> set[str]:
    """What the cleanup of old pictures leaves in data/media (hub/retention.py): the beginnings of the names of the
    films' own files (film-<id>) and of their scenes' clips with what lies beside them (the job's id)."""
    names = {f"film-{row[0]}" for row in conn.execute("SELECT id FROM films")}
    return names | {row[0] for row in conn.execute("SELECT job_id FROM film_scenes WHERE job_id IS NOT NULL")}


class Director:
    """The films' service: the storyboard asked of the director's model (`write`)."""

    def __init__(self, db: Database, bus: EventBus, judge: Judge, media: Path):
        self._db = db
        self._bus = bus
        self._judge = judge
        self.media = media
        self._stops: dict[str, asyncio.Event] = {}  # «Стоп» of a film's question or filming

    async def _changed(self, film_id: str, **fields: Any) -> dict[str, Any]:
        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            if fields:
                update_film(conn, film_id, **fields)
            return film(conn, film_id)

        found = await self._db.acall(write)
        if found is not None:
            await self._bus.publish("film.changed", {"film": found}, persist=False)
        return found

    async def _film(self, film_id: str) -> dict[str, Any]:
        found = await self._db.acall(lambda conn: film(conn, film_id))
        if found is None:
            raise DirectorError(tr("нет такого фильма"), 404)
        if found["status"] in ("writing", "filming", "joining"):
            raise DirectorError(tr("фильм занят: дождитесь конца или нажмите «Стоп»"))
        return found

    async def video_model(self, found: dict[str, Any]) -> dict[str, Any]:
        """The video model that films this film: the one named, else the first that films as the film needs."""
        models = await self._db.acall(lambda conn: visual.installed(conn, "video"))
        need = "picture" if found["photo"] else "text"
        model = next((m for m in models if m["model_id"] == found["model_id"]), None) if found["model_id"] else next(
            (m for m in models if need in m["modes"]), models[0] if models else None)  # fmt: skip
        if model is None:
            raise DirectorError(tr("модели видео нет — поставьте её: «Установка моделей» → «Рисовать»"), 404)
        if not allowed(model["modes"], bool(found["photo"])):
            raise DirectorError(tr("{name} снимает только по картинке — добавьте фото товара", name=model["name"]), 422)
        return model

    async def _photo(self, found: dict[str, Any]) -> list[dict[str, str]]:
        """The product's photo as a question's picture; none - an empty list."""
        if not found["photo"]:
            return []
        path = self.media / found["photo"]
        kind = await asyncio.to_thread(visual.picture_type, path)
        if kind is None:
            raise DirectorError(tr("фото товара уже удалено — добавьте его снова"), 404)
        data = await asyncio.to_thread(path.read_bytes)
        return [{"media_type": kind, "data": base64.b64encode(data).decode("ascii")}]

    async def write(self, film_id: str, *, timeout_s: float = WRITE_TIMEOUT_S) -> dict[str, Any]:
        """«Собрать сценарий» (50.6): the director's model - the film's, else the role's - is asked for the scenes
        with the idea, the length and the photo; they replace the film's scenes. The film as it is then."""
        found = await self._film(film_id)
        model = await self.video_model(found)
        chosen = found["director"] or await self._db.acall(role_model)
        if not chosen:
            raise DirectorError(tr("выберите модель для роли «Режиссёр»: «Модели» → «Роли»"), 422)
        images = await self._photo(found)
        system, prompt, form = question(found["idea"], found["seconds"], found["shape"], model["modes"], bool(images))
        read = reader(clip=model["clip"], modes=model["modes"], photo=bool(images), seconds=found["seconds"])
        stop = self._stops[film_id] = asyncio.Event()
        await self._changed(film_id, status="writing", error="")
        back = "ready" if found["scenes"] else "draft"
        try:
            got = await self._judge.complete(chosen, system, prompt, read, conversation_id=None,
                                             meta={"purpose": "director", "film_id": film_id}, form=form,
                                             images=images or None, timeout_s=timeout_s, cancel=stop)  # fmt: skip
        except JudgeError as exc:
            words = tr("сценарий не получился: {error}", error=exc)
            await self._changed(film_id, status=back, error="" if stop.is_set() else words)
            raise DirectorError(words, 502) from None
        finally:
            self._stops.pop(film_id, None)

        def keep(conn: sqlite3.Connection) -> None:
            set_scenes(conn, film_id, got["scenes"])
            update_film(
                conn,
                film_id,
                status="ready",
                error="; ".join(got["notes"]),
                director=chosen,
                file="",
                tokens=found["tokens"] + got["tokens"],
                cost_usd=round(found["cost_usd"] + got["cost_usd"], 6),
            )

        await self._db.acall(keep)
        return await self._changed(film_id)
