"""«Режиссёр» (stage 50): a film made of scenes. The user gives an idea, a length and the product's photo; a model
writes the storyboard - the scenes, each with its words and its prompt; the Studio's video model films them one
after another, a scene going on from the last frame of the one before; ffmpeg joins them into one MP4.

A film and its scenes are rows (films, film_scenes); a scene's clip is a job of the Studio (visual_jobs). The files
lie in data/media: the photo (film-<id>-photo.png), the clips (their jobs'), the joined film (film-<id>-<x>.mp4, a
new name each time it is joined) - the cleanup of old pictures leaves them alone while the film is there (`kept`,
hub/retention.py).

The look (stage 51): each scene filmed is shown to the director's model - six frames of the take and the product's
photo; a take with a plain fault (the wrong product, no motion, parts out of number or place, distortions, drifted
colours, not the scene) is filmed anew by the prompt the look writes - twice a scene at most, within the shooting's
own limits. A look that does not come out stops no film.

Sound (stage 52): a film whose video model films with sound (hub/visual.py) has its own switch; the director is
asked what is heard in each scene, a scene is filmed with it, and the joined film keeps the scenes' sound.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.i18n import N_, tr
from common.picture_sizes import SECONDS, frames_for, scene_modes

from . import ffmpeg, model_settings, visual
from .db import Database, utc_now
from .events import EventBus
from .judge import Judge, JudgeError

log = logging.getLogger(__name__)

ROLE = "director"  # the role of the model that writes storyboards (model_settings.ROLES)
SCENES = (1, 16)  # a film's scenes, at least and at most
SCENE_S = (1.0, SECONDS[1])  # a scene's length, seconds
FILM_S = (5, int(SCENES[1] * SCENE_S[1]))  # a film's length, seconds
FADE_S = (0.0, 1.0)  # a cross-fade at each joint of the joined film
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
- "prompt": English, one paragraph. Begin with the motion: who or what moves and how, concretely, in the present \
tense. Then the camera (slow push in, orbit, pull back, tracking beside the subject...), the light, the style - \
briefly. Describe the product as the photo shows it; invent no text, logos or labels on it.
- One action a scene, exactly. When the idea has two things happening (a blink and a turn, a pour and a lift), \
give each a scene of its own: a video model asked for two in one take does neither well.
- Name the parts that must keep their number, shape and place ("the three marks, evenly spaced around the \
centre") and say that they keep them - a video model left to itself adds, drops and bends them.
- A scene that begins with a picture ("picture", "continue", "frames"): the video model sees that frame already - \
do not spend the prompt retelling what it shows, say what happens next. A crisp picture described by its looks \
alone stays a still picture.
- Something must visibly move in every scene. Do not write "static" or "still" in a scene whose subject moves: \
the camera follows, tracks or holds on the subject.
- "words": one short sentence saying what happens in the scene, in the language of the idea - the user reads it.
{sound}- Keep the product the same from scene to scene: the same thing, the same colours, the same place unless \
the idea moves it.

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
# (52.6) asked only of a film whose video model films with sound
SOUND_RULE = ('- "sound": English, one short sentence: what is heard in the scene - the place\'s own sounds, the '
              "product's, what moves in it. No music and no speech unless the idea asks for them. The video model "
              "films the scene with this sound.\n")  # fmt: skip
FORM = ('{{"scenes": [{{"mode": {modes}, "seconds": <number>, "words": "...", "prompt": "..."{sound}}}, ...]}} - '
        "{seconds} seconds in all")  # fmt: skip
SHAPES = ("wide", "tall", "square")
MODES = ("text", "picture", "continue", "frames")  # how a scene begins: film_scenes.mode
FILM_FIELDS = ("idea", "seconds", "shape", "photo", "director", "model_id", "status", "error", "file", "fade",
               "tokens", "cost_usd", "checks", "checking", "sound")  # fmt: skip
SCENE_FIELDS = ("mode", "words", "prompt", "frames", "seed", "job_id", "status", "error", "cfg", "tries", "verdict",
                "reason", "sound_words")  # fmt: skip
UNLOOKED = {"cfg": None, "tries": 0, "verdict": "", "reason": ""}  # a scene the look has said nothing of (51.1)


def _film(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """A film's row with its scenes in order, each with its clip (`clip`: the job's file, relative to data/media;
    '' - none yet) and how long that took to film (`took`, seconds): the window's cards and its «ещё ≈»."""
    scenes = conn.execute(
        "SELECT s.*, COALESCE(j.file, '') AS clip, j.seconds AS took FROM film_scenes s "
        "LEFT JOIN visual_jobs j ON j.id = s.job_id WHERE s.film_id = ? ORDER BY s.n", (row["id"],),
    ).fetchall()  # fmt: skip
    listed = [{k: s[k] for k in s.keys() if k != "film_id"} for s in scenes]  # noqa: SIM118
    return {**dict(row), "checks": bool(row["checks"]), "sound": bool(row["sound"]), "scenes": listed}


def film(conn: sqlite3.Connection, film_id: str) -> dict[str, Any] | None:
    """A film with its scenes in order, or None."""
    row = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
    return _film(conn, row) if row else None


def films(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """The films, the newest first, each with its scenes."""
    rows = conn.execute("SELECT * FROM films ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return [_film(conn, row) for row in rows]


def new_film(conn: sqlite3.Connection, *, idea: str, seconds: int, shape: str = "wide", photo: str = "",
             director: str = "", model_id: str = "", checks: bool = True,
             sound: bool = True) -> dict[str, Any]:  # fmt: skip
    """A film begun: a draft, no scenes yet. `checks` (51.1) - its scenes are looked at once filmed; `sound` (52.6)
    - they are filmed with sound, when the video model films it."""
    film_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO films (id, idea, seconds, shape, photo, director, model_id, checks, sound) VALUES (?, ?, ?, ?, "
        "?, ?, ?, ?, ?)",
        (film_id, idea, seconds, shape, photo, director, model_id, int(checks), int(sound)),
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
        "INSERT INTO film_scenes (film_id, n, mode, words, prompt, frames, seed, sound_words) VALUES (?, ?, ?, ?, ?, "
        "?, ?, ?)",
        [(film_id, n, s["mode"], s.get("words", ""), s["prompt"], int(s["frames"]), s.get("seed"),
          s.get("sound_words", "")) for n, s in enumerate(scenes, start=1)],
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


def question(idea: str, seconds: int, shape: str, modes: list[str], photo: bool, *,
             sound: bool = False) -> tuple[str, str, str]:  # fmt: skip
    """What the director is asked: (system, prompt, form) - the form is said again when the answer is not it.
    `sound` (52.6) - the film is filmed with sound: what is heard in each scene is asked too."""
    can = scene_modes(modes, photo)
    form = FORM.format(modes=" | ".join(f'"{m}"' for m in can), seconds=seconds,
                       sound=', "sound": "..."' if sound else "")  # fmt: skip
    system = SYSTEM.format(modes="\n".join(MODE_WORDS[m] for m in can), low=SCENE_S[0], high=SCENE_S[1], form=form,
                           sound=SOUND_RULE if sound else "")  # fmt: skip
    frame = {"wide": "wide (landscape)", "tall": "tall (portrait, for a phone)", "square": "square"}[shape]
    prompt = (f"<idea>\n{idea}\n</idea>\nThe film: {seconds} seconds, {frame}. "
              + ("The product's photo is attached." if photo else "There is no photo of the product.")
              + " Write the storyboard. Answer with the JSON only.")  # fmt: skip
    return system, prompt, form


def reader(*, clip: dict[str, Any], modes: list[str], photo: bool, seconds: int,
           sound: bool = False) -> Callable[[str], dict | None]:  # fmt: skip
    """What reads the director's answer: {scenes: [{mode, words, prompt, frames, sound_words - what is heard, of a
    film with sound (52.6), else ''}], notes} - a scene's seconds into
    the frames the family's VAE takes (`clip`: fps, step), its length within a scene's, a mode this film cannot
    film put right; None - no storyboard, asked again: no JSON, no scenes or too many, a scene with no prompt, and -
    the first time only - a length far from the one asked (the second is taken as it is, and said)."""
    can, fps, step, tries = scene_modes(modes, photo), int(clip["fps"]), int(clip["step"]), [0]

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
            heard = " ".join(str(item.get("sound") or "").split())[: visual.SOUND_WORDS] if sound else ""
            scenes.append({"mode": mode_of(str(item.get("mode") or ""), not scenes),
                           "words": words[: TEXT_CHARS["words"]], "prompt": prompt[: TEXT_CHARS["prompt"]],
                           "frames": frames_for(length, fps, step), "sound_words": heard})  # fmt: skip
        got = sum(s["frames"] - 1 for s in scenes) / fps
        notes = []
        if abs(got - seconds) > seconds * NEAR:
            if tries[0] == 1:
                return None
            notes.append(tr("сценарий вышел на {got} с вместо {asked} с", got=round(got), asked=seconds))
        return {"scenes": scenes, "notes": notes}

    return read


LOOK_FRAMES = 6  # of a scene shown to the look, evenly from its first to its last: what changes between them
LOOK_SIDE = 768  # their longer side, points: a scene's own size or near it - small parts are told one from another
LOOK_TIMEOUT_S = 120.0  # one answer of the look's
RETAKES = 2  # times the look has one scene filmed anew
LOOK_USD = 0.5  # what the looks of one shooting may cost
CFG_ROOM = 2.0  # how far from the video model's own guidance the look may go
# why a take does not stay, and what the scene's card says when the model gave no words of its own
REASONS = {"product": N_("товар не тот, что на фото"), "motion": N_("движение не то, что в описании"),
           "geometry": N_("детали меняют число, форму или место"), "artifacts": N_("в кадре искажения"),
           "colours": N_("цвет ушёл от исходного"), "scene": N_("снято не то, что в описании")}  # fmt: skip
LOOK_SYSTEM = """You are the director of a short product video. A video model has just filmed one scene from your \
prompt; you check the take. You get the scene - what happens in it, how it begins, the prompt it was filmed from - \
and pictures: {frames} frames of the take in order, evenly spread from its first to its last{photo}.

Look at the frames one after another, as a viewer sees them. First find the parts of the subject that must stay \
what they are - name them and count them in the first frame. Then go frame by frame: are they all there, as many, \
of the same shape, in their places relative to each other? Does what moves go the way the scene asks? Does what \
the scene does not move stay as it began?

Reject the take for a fault a viewer sees at once:
- "product": {product}; text or a logo made up on it.
- "motion": nothing happens - the frames are one picture, though the scene is of something moving; or what moves \
is not what the scene asks for, or it goes the other way.
- "geometry": the subject does not keep its build - a part appears, vanishes, doubles or changes its number; parts \
drift from their places, lose their even spacing or their shape; the subject's outline or proportions change where \
the scene asks for no such change.
- "artifacts": broken shapes - melted or smeared things, extra limbs or fingers, a garbled face; a frame that is \
blank or noise.
- "colours": the colours or the light drift from where the take began - it greys, darkens or burns out.
- "scene": the take shows something other than the scene asked for - an action the prompt does not have, or one \
it has is missing.
Grain, a slight flicker, a soft frame stay: a take filmed anew costs minutes. But a fault is not accepted because \
the rest of the take is good.

When you reject, write the prompt anew so that the fault does not come back: English, one paragraph, the same \
scene. Begin with the motion - who or what moves and how. One action only: if the prompt asked for two, keep the \
one the scene is about. Name the parts that must keep their number, shape and place, and say that they keep them. \
Do not retell what the first frame shows; do not write "static" or "still" in a scene whose subject moves.{cfg}

Answer with the JSON object only: {form}"""  # i18n: keep
LOOK_PHOTO = ", then the product's photo - the last picture"
LOOK_PRODUCT = {True: "the product is not the one in the photo - another shape or colour, another thing",
                False: "the product turns into another thing during the take - another shape or colour"}  # fmt: skip
LOOK_CFG = (' You may also set "cfg", the video model\'s guidance, {low:g} to {high:g} (it was {now:g}): higher '
            "keeps closer to the prompt, lower moves more freely.")  # fmt: skip
LOOK_FORM = ('{{"seen": "<what changes from frame to frame: the parts, their number and places - two sentences at '
             'most>", "ok": true}} - or {{"seen": "...", "ok": false, "why": {whys}, "reason": "<one short sentence, '
             'in the language of what happens in the scene>", "prompt": "<the prompt written anew>"{cfg}}}'
             )  # fmt: skip
BEGINS = {"picture": "with the product's photo as its first frame",
          "continue": "with the last frame of the scene before - one unbroken shot",
          "frames": "with the last frame of the scene before, and ends on the product's photo",
          "text": "by the prompt alone"}  # fmt: skip


def cfg_room(model: dict[str, Any], scene: dict[str, Any]) -> tuple[float, float, float] | None:
    """The guidance the look may ask for: (the lowest, the highest, the scene's now) - or None: a model installed
    with none (cfg 1, the distilled ones) films worse with any, and twice as long."""
    own = float(model.get("cfg") or 1.0)
    if own <= 1.0:
        return None
    return max(1.5, own - CFG_ROOM), own + CFG_ROOM, float(scene.get("cfg") or own)


def look_question(scene: dict[str, Any], *, photo: bool, cfg: tuple[float, float, float] | None,
                  frames: int = LOOK_FRAMES) -> tuple[str, str, str]:  # fmt: skip
    """What the look at a filmed scene asks (51.2): (system, prompt, form). `cfg` - `cfg_room`'s."""
    form = LOOK_FORM.format(whys=" | ".join(f'"{why}"' for why in REASONS), cfg=', "cfg": <number>' if cfg else "")
    said = LOOK_CFG.format(low=cfg[0], high=cfg[1], now=cfg[2]) if cfg else ""
    system = LOOK_SYSTEM.format(frames=frames, photo=LOOK_PHOTO if photo else "", product=LOOK_PRODUCT[photo],
                                cfg=said, form=form)  # fmt: skip
    prompt = (f"<scene>\nWhat happens: {scene['words']}\nIt begins {BEGINS[scene['mode']]}.\n"
              f"The prompt it was filmed from: {scene['prompt']}\n</scene>\n"
              f"The pictures: the take's {frames} frames in order" + (", then the product's photo" if photo else "")
              + ". Does the take stay? Answer with the JSON only.")  # fmt: skip
    return system, prompt, form


def look_reader(cfg: tuple[float, float, float] | None) -> Callable[[str], dict[str, Any] | None]:
    """What reads the look's answer (51.2): {ok, why, reason, prompt, cfg} - `prompt` '' and `cfg` None: as they
    were; a guidance outside `cfg`'s room is brought into it, one for a model that takes none is dropped. None - not
    a verdict, asked again: no JSON, no `ok` that is true or false. What the model saw (`seen`: it is asked to say it
    before the verdict, so that it looks) is not kept."""

    def read(answer: str) -> dict[str, Any] | None:
        start, end = answer.find("{"), answer.rfind("}")
        try:
            data = json.loads(answer[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            return None
        if data["ok"]:
            return {"ok": True, "why": "", "reason": "", "prompt": "", "cfg": None}
        why = data.get("why") if data.get("why") in REASONS else "scene"
        reason = " ".join(str(data.get("reason") or "").split())[: TEXT_CHARS["words"]] or tr(REASONS[why])
        prompt = " ".join(str(data.get("prompt") or "").split())[: TEXT_CHARS["prompt"]]
        asked = None
        if cfg is not None:
            with contextlib.suppress(TypeError, ValueError):
                asked = round(min(cfg[1], max(cfg[0], float(data.get("cfg")))), 2)
        return {"ok": False, "why": why, "reason": reason, "prompt": prompt, "cfg": asked}

    return read


def _stale_after(conn: sqlite3.Connection, film_id: str, n: int) -> None:
    """Scene `n` has been filmed anew: the filmed scenes that go on from it - the chain of «продолжение» and
    «к фото» right after it - began with a frame that is no more: stale."""
    for scene in conn.execute("SELECT n, mode, status FROM film_scenes WHERE film_id = ? AND n > ? ORDER BY n",
                              (film_id, n)).fetchall():  # fmt: skip
        if scene["mode"] not in ("continue", "frames"):
            break
        if scene["status"] == "done":
            conn.execute("UPDATE film_scenes SET status = 'stale' WHERE film_id = ? AND n = ?", (film_id, scene["n"]))


def kept(conn: sqlite3.Connection) -> set[str]:
    """What the cleanup of old pictures leaves in data/media (hub/retention.py): the beginnings of the names of the
    films' own files (film-<id>) and of their scenes' clips with what lies beside them (the job's id)."""
    names = {f"film-{row[0]}" for row in conn.execute("SELECT id FROM films")}
    return names | {row[0] for row in conn.execute("SELECT job_id FROM film_scenes WHERE job_id IS NOT NULL")}


class Director:
    """The films' service: a film begun, changed and deleted (`create`, `change`, `remove`), its storyboard asked
    of the director's model (`write`) and changed by hand (`edit_scene`), its scenes filmed (`shoot`, `stop`) and
    joined into one MP4 (`join`). What takes long goes on in the background, the film's changes told by
    `film.changed`."""

    def __init__(self, db: Database, bus: EventBus, judge: Judge, studio: visual.Visual):
        self._db = db
        self._bus = bus
        self._judge = judge
        self._studio = studio
        self.media = studio.media
        self._stops: dict[str, asyncio.Event] = {}  # «Стоп» of a film's question or filming
        self._tasks: dict[str, asyncio.Task] = {}  # the films at work in the background: written, shot, joined

    async def start(self) -> int:
        """Films a stopped hub left being written, shot or joined are ready again - the scenes not filmed are
        «not filmed»: «Продолжить» goes on from them. How many."""

        def mend(conn: sqlite3.Connection) -> int:
            left = [
                r[0] for r in conn.execute("SELECT id FROM films WHERE status IN ('writing', 'filming', 'joining')")
            ]
            for film_id in left:
                has = conn.execute("SELECT 1 FROM film_scenes WHERE film_id = ?", (film_id,)).fetchone()
                update_film(conn, film_id, status="ready" if has else "draft", checking=0,
                            error=tr("хаб перезапускался — «Продолжить» снимет то, что не снято"))  # fmt: skip
            conn.execute("UPDATE film_scenes SET status = 'new', error = '' WHERE status = 'filming'")
            return len(left)

        return await self._db.acall(mend)

    async def close(self) -> None:
        """The hub stops: a film's question is let go, the film being shot is stopped, its scene's job with it; a
        film being joined is left - ffmpeg ends by itself, and `start` puts the film right."""
        for film_id, task in list(self._tasks.items()):
            if (stop := self._stops.get(film_id)) is not None:
                stop.set()
            else:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

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

    async def _picture(self, spec: dict[str, Any]) -> bytes | None:
        """The product's photo a request brings - a PNG sent (photo_png, base64), a picture of the Studio's (from_job;
        a video - its last frame) or a chat's (from_blob) - or None."""
        try:
            return await self._studio.source({"from_png": spec.get("photo_png"), "from_job": spec.get("from_job"),
                                              "from_blob": spec.get("from_blob")})  # fmt: skip
        except visual.VisualError as exc:
            raise DirectorError(str(exc), exc.status) from None

    async def _keep_photo(self, film_id: str, data: bytes) -> str:
        name = f"{datetime.now(UTC):%Y-%m}/film-{film_id}-photo.png"
        await asyncio.to_thread(visual.save, self.media / name, data)
        return name

    def own(self, name: str) -> Path | None:
        """A film's own file - its photo, the joined film - by the name the film keeps; None - it is not there."""
        path = self.media / name if name else None
        return path if path is not None and path.is_file() else None

    def _unlink(self, film_id: str, pattern: str, keep: Path | None = None) -> None:
        """The film's own files of this kind gone, but `keep` (blocking). One a player holds open stays - until the
        next time, or the cleanup of old pictures."""
        for path in self.media.glob(f"*/film-{film_id}-{pattern}"):
            if path != keep:
                with contextlib.suppress(OSError):
                    path.unlink()

    async def create(self, spec: dict[str, Any]) -> dict[str, Any]:
        """A film begun (50.8): {idea, seconds, shape, director, model, checks, sound} and the product's photo, if
        there is one (`_picture`). A draft, no scenes yet."""
        idea = str(spec.get("idea") or "").strip()
        if not idea:
            raise DirectorError(tr("нужна идея ролика: о чём он"), 422)
        photo = await self._picture(spec)
        made = await self._db.acall(lambda conn: new_film(
            conn, idea=idea, seconds=int(spec.get("seconds") or 15), shape=str(spec.get("shape") or "wide"),
            director=str(spec.get("director") or ""), model_id=str(spec.get("model") or ""),
            checks=spec.get("checks") is not False, sound=spec.get("sound") is not False))  # fmt: skip
        kept = {"photo": await self._keep_photo(made["id"], photo)} if photo is not None else {}
        return await self._changed(made["id"], **kept)

    async def change(self, film_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        """The film's own changed (50.8) - only what `spec` names: idea, seconds, shape, director, model, fade, checks
        (51.1: whether its scenes are looked at), the photo (as `create` takes one; `photo: ""` - none). Another
        shape, photo or video model - the scenes filmed are of the film as it was: stale, the joined file forgotten."""
        found = await self._film(film_id)
        fields = {k: spec[k] for k in ("idea", "seconds", "shape", "director", "fade") if spec.get(k) is not None}
        if "idea" in fields:
            fields["idea"] = str(fields["idea"]).strip()
            if not fields["idea"]:
                raise DirectorError(tr("нужна идея ролика: о чём он"), 422)
        if spec.get("model") is not None:
            fields["model_id"] = str(spec["model"])
        if spec.get("checks") is not None:
            fields["checks"] = int(bool(spec["checks"]))
        if spec.get("sound") is not None:  # (52.6) the scenes filmed stay as they are: the next ones are filmed so
            fields["sound"] = int(bool(spec["sound"]))
        photo = await self._picture(spec)
        if photo is not None:
            fields["photo"] = await self._keep_photo(film_id, photo)
        elif spec.get("photo") == "":
            fields["photo"] = ""
        look = "photo" in fields or any(k in fields and fields[k] != found[k] for k in ("shape", "model_id"))

        def write(conn: sqlite3.Connection) -> None:
            update_film(conn, film_id, **fields, **({"file": ""} if look else {}))
            if look:
                conn.execute("UPDATE film_scenes SET status = 'stale' WHERE film_id = ? AND status = 'done'",
                             (film_id,))  # fmt: skip

        await self._db.acall(write)
        if found["photo"] and fields.get("photo", found["photo"]) != found["photo"]:
            with contextlib.suppress(OSError):
                await asyncio.to_thread((self.media / found["photo"]).unlink, True)
        return await self._changed(film_id)

    async def remove(self, film_id: str) -> None:
        """The film gone, its photo and its joined file with it; its scenes' clips stay videos of the Studio's - the
        cleanup of old pictures comes to them as to any other."""
        await self._film(film_id)
        await self._db.acall(lambda conn: delete_film(conn, film_id))
        await asyncio.to_thread(self._unlink, film_id, "*")
        await self._bus.publish("film.deleted", {"id": film_id}, persist=False)

    async def video_model(self, found: dict[str, Any]) -> dict[str, Any]:
        """The video model that films this film: the one named, else the first that films as the film needs."""
        models = await self._db.acall(lambda conn: visual.installed(conn, "video"))
        need = "picture" if found["photo"] else "text"
        model = next((m for m in models if m["model_id"] == found["model_id"]), None) if found["model_id"] else next(
            (m for m in models if need in m["modes"]), models[0] if models else None)  # fmt: skip
        if model is None:
            raise DirectorError(tr("модели видео нет — поставьте её: «Установка моделей» → «Рисовать»"), 404)
        if not scene_modes(model["modes"], bool(found["photo"])):
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

    async def write(self, film_id: str, *, timeout_s: float = WRITE_TIMEOUT_S,
                    background: bool = False) -> dict[str, Any]:  # fmt: skip
        """«Собрать сценарий» (50.6): the director's model - the film's, else the role's - is asked for the scenes
        with the idea, the length and the photo; they replace the film's scenes. The film as it is then - or, in the
        `background` (50.8: the window's), as it begins: what could not be asked is refused at once, what the
        director did not answer is the film's `error`."""
        found = await self._film(film_id)
        model = await self.video_model(found)
        chosen = found["director"] or await self._db.acall(role_model)
        if not chosen:
            raise DirectorError(tr("выберите модель для роли «Режиссёр»: «Модели» → «Роли»"), 422)
        images = await self._photo(found)
        loud = bool(found["sound"]) and bool(model.get("sound"))  # (52.6) what is heard - asked of a film with sound
        system, prompt, form = question(found["idea"], found["seconds"], found["shape"], model["modes"], bool(images),
                                        sound=loud)  # fmt: skip
        read = reader(clip=model["clip"], modes=model["modes"], photo=bool(images), seconds=found["seconds"],
                      sound=loud)  # fmt: skip
        stop = self._stops[film_id] = asyncio.Event()
        began = await self._changed(film_id, status="writing", error="")

        async def ask() -> dict[str, Any]:
            try:
                got = await self._judge.complete(chosen, system, prompt, read, conversation_id=None,
                                                 meta={"purpose": "director", "film_id": film_id}, form=form,
                                                 images=images or None, timeout_s=timeout_s, cancel=stop)  # fmt: skip
            except JudgeError as exc:
                words = tr("сценарий не получился: {error}", error=exc)
                await self._changed(film_id, status="ready" if found["scenes"] else "draft",
                                    error="" if stop.is_set() else words)  # fmt: skip
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

        if not background:
            return await ask()
        self._tasks[film_id] = asyncio.create_task(self._background(film_id, ask()))
        return began

    async def _background(self, film_id: str, work: Awaitable[Any]) -> None:
        """A film's work that goes on after the answer: what it could not do is the film's `error` already."""
        try:
            await work
        except DirectorError:
            pass
        except Exception:
            log.exception("film %s", film_id)
            has = await self._db.acall(lambda conn: conn.execute(
                "SELECT 1 FROM film_scenes WHERE film_id = ?", (film_id,)).fetchone())  # fmt: skip
            await self._changed(film_id, status="ready" if has else "draft",
                                error=tr("внутренняя ошибка хаба — подробности в журнале"))  # fmt: skip
        finally:
            self._tasks.pop(film_id, None)

    async def edit_scene(self, film_id: str, n: int, spec: dict[str, Any]) -> dict[str, Any]:
        """A scene changed by hand (50.8) - only what `spec` names: words, prompt, mode, seconds, seed (none or below
        0 - a new one each time it is filmed), sound_words (52.6: what is heard). What the video model is given has
        changed - a clip filmed is of the
        scene as it was: the scene is stale, the filmed ones that go on from it too, the joined file forgotten, and
        what the look said of it (51.1) is of another scene. The words alone change nothing else."""
        found = await self._film(film_id)
        scene = next((s for s in found["scenes"] if s["n"] == n), None)
        if scene is None:
            raise DirectorError(tr("нет такой сцены"), 404)
        fields: dict[str, Any] = {}
        if spec.get("words") is not None:
            fields["words"] = " ".join(str(spec["words"]).split())[: TEXT_CHARS["words"]]
        if spec.get("prompt") is not None:
            fields["prompt"] = " ".join(str(spec["prompt"]).split())[: TEXT_CHARS["prompt"]]
            if not fields["prompt"]:
                raise DirectorError(tr("нужно описание сцены для модели видео"), 422)
        if spec.get("mode") is not None or spec.get("seconds") is not None:
            model = await self.video_model(found)
            if (mode := spec.get("mode")) is not None:
                if n == 1 and mode in ("continue", "frames"):
                    raise DirectorError(tr("первой сцене продолжать нечего — выберите другой режим"), 422)
                if mode not in scene_modes(model["modes"], bool(found["photo"])):
                    raise DirectorError(tr("так сцена начаться не может: у фильма нет фото товара или модель видео "
                                           "так не снимает"), 422)  # fmt: skip
                fields["mode"] = mode
            if spec.get("seconds") is not None:
                length = min(SCENE_S[1], max(SCENE_S[0], float(spec["seconds"])))
                fields["frames"] = frames_for(length, int(model["clip"]["fps"]), int(model["clip"]["step"]))
        if "seed" in spec:
            fields["seed"] = None if spec["seed"] is None or int(spec["seed"]) < 0 else int(spec["seed"])
        if spec.get("sound_words") is not None:
            fields["sound_words"] = " ".join(str(spec["sound_words"]).split())[: visual.SOUND_WORDS]
        other = any(fields[k] != scene[k] for k in ("prompt", "mode", "frames", "seed", "sound_words") if k in fields)
        if other:
            fields |= UNLOOKED
        if other and scene["status"] != "new":
            fields |= {"status": "stale" if scene["job_id"] and scene["status"] != "failed" else "new", "error": ""}

        def write(conn: sqlite3.Connection) -> None:
            if fields:
                update_scene(conn, film_id, n, **fields)
            if other:
                _stale_after(conn, film_id, n)
                update_film(conn, film_id, file="")

        await self._db.acall(write)
        return await self._changed(film_id)

    async def shoot(self, film_id: str, *, only: int | None = None) -> dict[str, Any]:
        """«Снять всё» and «Продолжить» (50.7): the scenes not filmed - new, failed, stale - go one after another, in
        order; `only` - «Переснять»: that scene alone. The film as it begins; it goes on in the background."""
        found = await self._film(film_id)
        model = await self.video_model(found)
        scenes = [s for s in found["scenes"] if (s["n"] == only if only else s["status"] != "done")]
        if not scenes:
            words = (tr("нет такой сцены") if only else tr("снимать нечего: все сцены сняты") if found["scenes"]
                     else tr("сначала соберите сценарий"))  # fmt: skip
            raise DirectorError(words, 422)
        self._stops[film_id] = asyncio.Event()
        began = await self._changed(film_id, status="filming", error="")
        self._tasks[film_id] = asyncio.create_task(self._shoot(found, model, [s["n"] for s in scenes]))
        return began

    async def stop(self, film_id: str) -> dict[str, Any]:
        """«Стоп»: the director's question is let go, or the scene being filmed is stopped in the engine and no next
        one begins; the film as it is once it has stopped. A film being joined is not stopped: ffmpeg is quick."""
        found = await self._db.acall(lambda conn: film(conn, film_id))
        if found is None:
            raise DirectorError(tr("нет такого фильма"), 404)
        if found["status"] == "joining":
            raise DirectorError(tr("ролик склеивается — это недолго, дождитесь конца"))
        if (stop := self._stops.get(film_id)) is not None:
            stop.set()
        if (task := self._tasks.get(film_id)) is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return await self._db.acall(lambda conn: film(conn, film_id))

    async def _spec(self, found: dict[str, Any], model: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
        """A scene as the Studio's video: its first frame by its mode - the product's photo, the last frame of the
        scene before (that scene's job: `Visual.source` takes its end) - and, «к фото», the photo as its last."""
        fps = int(model["clip"]["fps"])
        spec = {"prompt": scene["prompt"], "model": model["model_id"], "shape": found["shape"], "shape_fixed": True,
                "seconds": (scene["frames"] - 1) / fps, "seed": scene["seed"]}  # fmt: skip
        if scene.get("cfg"):  # (51.3) the guidance the look asked for
            spec["cfg"] = scene["cfg"]
        # (52.6) the film's switch and what is heard in the scene; a model that films no sound takes neither
        spec |= {"sound": bool(found["sound"]), "sound_words": scene.get("sound_words") or ""}
        if scene["mode"] in ("picture", "frames"):
            photo = (await self._photo(found) or [{}])[0].get("data")
            if not photo:
                raise DirectorError(tr("у фильма нет фото товара — добавьте его или смените режим сцены"), 422)
            spec["last_png" if scene["mode"] == "frames" else "from_png"] = photo
        if scene["mode"] in ("continue", "frames"):
            before = await self._db.acall(lambda conn: conn.execute(
                "SELECT job_id, status FROM film_scenes WHERE film_id = ? AND n = ?", (found["id"], scene["n"] - 1),
            ).fetchone())  # fmt: skip
            if before is None or before["status"] != "done" or not before["job_id"]:
                raise DirectorError(tr("сцена {n} продолжает сцену {before}, а та не снята — сначала снимите её",
                                       n=scene["n"], before=scene["n"] - 1), 422)  # fmt: skip
            spec["from_job"] = before["job_id"]
        return spec

    async def _scene(self, film_id: str, n: int, **fields: Any) -> None:
        await self._db.acall(lambda conn: update_scene(conn, film_id, n, **fields))
        await self._changed(film_id)

    async def _the_scene(self, film_id: str, n: int) -> dict[str, Any]:
        return next(s for s in (await self._db.acall(lambda conn: film(conn, film_id)))["scenes"] if s["n"] == n)

    def _keep_card(self, stop: asyncio.Event) -> None:
        """The card stays the Studio's between the film's scenes; taken from the film meanwhile - the film stops."""
        self._studio.keep(stop.set, tr("видеокарта снимает фильм — ответ начнётся после него; «Стоп» в Студии её "
                                       "освободит"))  # fmt: skip

    async def _take(self, found: dict[str, Any], model: dict[str, Any], n: int,
                    stop: asyncio.Event) -> tuple[dict[str, Any] | None, str]:  # fmt: skip
        """One take of scene `n`: (its job done, '') - or (None, why the film stops at this scene; '' - «Стоп»)."""
        film_id = found["id"]
        try:
            job = await self._studio.video(await self._spec(found, model, await self._the_scene(film_id, n)), "film")
        except (DirectorError, visual.VisualError) as exc:
            await self._scene(film_id, n, status="failed", error=str(exc))
            return None, tr("сцена {n} не снята: {error}", n=n, error=exc)
        await self._db.acall(lambda conn: update_film(conn, film_id, file=""))  # the joined film is of the old
        await self._scene(film_id, n, status="filming", error="", job_id=job["id"])
        await self._bus.publish("film.progress", {"id": film_id, "scene": n, "job": job["id"]}, persist=False)
        done = await self._studio.until_done(job["id"], stop)
        if done is not None and done["status"] == "done":
            await self._db.acall(lambda conn: _stale_after(conn, film_id, n))
            await self._scene(film_id, n, status="done")
            return done, ""
        if stop.is_set():
            await self._scene(film_id, n, status="new", error="")
            return None, ""
        said = (done or {}).get("error") or tr("видео не снято")
        await self._scene(film_id, n, status="failed", error=said)
        return None, tr("сцена {n} не снята: {error}", n=n, error=said)

    async def _look(self, found: dict[str, Any], model: dict[str, Any], n: int, job: dict[str, Any],
                    looks: dict[str, Any], stop: asyncio.Event) -> bool:  # fmt: skip
        """The take of scene `n` looked at by the director's model (51.3): three of its frames and the product's
        photo. Whether the scene is filmed anew - it is not good, and the scene's and the shooting's retakes are not
        used up: then its prompt (and guidance) are the look's. `looks` - the shooting's: `left` retakes, `spent`
        dollars, `off` - why no scene is looked at from now on (a look that does not come out stops none: the scene
        stays filmed)."""
        film_id = found["id"]
        if looks["off"] or stop.is_set():
            return False
        chosen = found["director"] or await self._db.acall(role_model)
        if not chosen:
            looks["off"] = tr("сцены не проверены: выберите модель для роли «Режиссёр»")
            return False
        if looks["spent"] >= LOOK_USD:
            looks["off"] = tr("проверка сцен остановлена: её расход дошёл до ${usd}", usd=f"{LOOK_USD:g}")
            return False
        scene = await self._the_scene(film_id, n)
        room, local = cfg_room(model, scene), self._judge.local(chosen)
        await self._changed(film_id, checking=n)
        try:
            frames = await self._studio.look(job, LOOK_FRAMES, LOOK_SIDE)
            photo = await self._photo(found)
            images = [{"media_type": "image/jpeg", "data": base64.b64encode(f).decode("ascii")} for f in frames]
            system, prompt, form = look_question(scene, photo=bool(photo), cfg=room, frames=len(frames))
            if local:  # its answer comes from the card: the film lets it go for the question
                self._studio.unkeep()
            try:
                said = await self._judge.complete(chosen, system, prompt, look_reader(room), conversation_id=None,
                                                  meta={"purpose": "director", "film_id": film_id, "scene": n},
                                                  form=form, images=images + photo, timeout_s=LOOK_TIMEOUT_S,
                                                  cancel=stop)  # fmt: skip
            finally:
                if local:
                    self._keep_card(stop)
        except (JudgeError, DirectorError, visual.VisualError) as exc:
            if not stop.is_set():
                looks["off"] = tr("сцены не проверены: {error}", error=exc)
            return False
        finally:
            await self._changed(film_id, checking=0)
        looks["spent"] += said["cost_usd"]
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE films SET tokens = tokens + ?, cost_usd = round(cost_usd + ?, 6) WHERE id = ?",
            (said["tokens"], said["cost_usd"], film_id)))  # fmt: skip
        if said["ok"]:
            await self._scene(film_id, n, verdict="ok", reason="")
            return False
        fields: dict[str, Any] = {"verdict": "reject", "reason": said["reason"]}
        again = scene["tries"] < RETAKES and looks["left"] > 0
        if again:
            looks["left"] -= 1
            fields["tries"] = scene["tries"] + 1
            if said["prompt"]:
                fields["prompt"] = said["prompt"]
            if said["cfg"] is not None:
                fields["cfg"] = said["cfg"]
            if not said["prompt"] and said["cfg"] is None:  # nothing else to film it by: another seed
                fields["seed"] = None
        elif scene["tries"] < RETAKES:
            looks["off"] = tr("проверка сцен остановлена: пересъёмок за эту съёмку уже {count}", count=looks["all"])
        await self._scene(film_id, n, **fields)
        return again

    async def _shoot(self, found: dict[str, Any], model: dict[str, Any], numbers: list[int]) -> None:
        film_id, error = found["id"], ""
        stop = self._stops[film_id]
        self._keep_card(stop)
        # (51.3) the looks of this shooting: scenes filmed anew - as many as there are scenes to film, at most (one
        # scene alone has its own RETAKES)
        retakes = max(RETAKES, len(numbers))
        looks = {"left": retakes, "all": retakes, "spent": 0.0, "off": "", "on": bool(found["checks"])}
        try:
            for n in numbers:
                if stop.is_set():
                    break
                await self._db.acall(lambda conn, n=n: update_scene(conn, film_id, n, tries=0, verdict="", reason=""))
                while True:
                    job, error = await self._take(found, model, n, stop)
                    if job is None or not looks["on"] or not await self._look(found, model, n, job, looks, stop):
                        break
                if job is None:  # not filmed, or «Стоп»: the next scene would go on from nothing
                    break
            error = error or looks["off"]
        except Exception:
            log.exception("film %s", film_id)
            error = tr("внутренняя ошибка хаба — подробности в журнале")
        finally:
            self._studio.unkeep()
            self._stops.pop(film_id, None)
            self._tasks.pop(film_id, None)
            await self._db.acall(lambda conn: conn.execute(
                "UPDATE film_scenes SET status = 'new' WHERE film_id = ? AND status = 'filming'", (film_id,),
            ))  # fmt: skip
            await self._changed(film_id, status="ready", error=error, checking=0)

    async def join(self, film_id: str, *, fade: float | None = None) -> dict[str, Any]:
        """«Собрать ролик» (50.8): the scenes' clips into one MP4 - cut to cut, or with a cross-fade of `fade` seconds
        at each joint (none said: the film's own). Every scene must be filmed, all in one size. The film as it
        begins: ffmpeg works in the background; the file's name is new each time - the window's player may hold the
        one before open."""
        found = await self._film(film_id)
        if not found["scenes"] or any(s["status"] != "done" for s in found["scenes"]):
            raise DirectorError(tr("сначала снимите все сцены: склеивается готовый фильм"), 422)
        fade = float(found["fade"] if fade is None else fade)
        jobs = await self._db.acall(lambda conn: [visual.job(conn, s["job_id"]) for s in found["scenes"]])
        clips = []
        for scene, job in zip(found["scenes"], jobs, strict=True):
            path = self._studio.file(job) if job is not None else None
            if path is None:
                raise DirectorError(tr("клипа сцены {n} уже нет — переснимите её", n=scene["n"]), 404)
            clips.append((path, job["frames"] / job["fps"]))
        if len({(job["width"], job["height"], job["fps"]) for job in jobs}) > 1:
            raise DirectorError(tr("сцены сняты в разном размере — переснимите их одной моделью"), 422)
        began = await self._changed(film_id, status="joining", error="")
        self._tasks[film_id] = asyncio.create_task(
            self._background(film_id, self._join(film_id, clips, int(jobs[0]["fps"]), fade))
        )
        return began

    async def _join(self, film_id: str, clips: list[tuple[Path, float]], fps: int, fade: float) -> None:
        name = f"{datetime.now(UTC):%Y-%m}/film-{film_id}-{uuid.uuid4().hex[:6]}.mp4"
        target = self.media / name
        try:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await self._studio.with_ffmpeg(
                target, lambda program: ffmpeg.join(program, clips, target, fps=fps, fade_s=fade)
            )
        except visual.VisualError as exc:
            await self._changed(film_id, status="ready", error=str(exc))
            raise DirectorError(str(exc), exc.status) from None
        await asyncio.to_thread(self._unlink, film_id, "*.mp4", target)  # the ones joined before
        await self._changed(film_id, status="ready", file=name, fade=fade, error="")
