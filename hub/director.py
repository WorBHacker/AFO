"""«Режиссёр» (stage 50): a film made of scenes. The user gives an idea, a length and the product's photo; a model
writes the storyboard - the scenes, each with its words and its prompt; the Studio's video model films them one
after another, a scene going on from the last frame of the one before; ffmpeg joins them into one MP4.

A film and its scenes are rows (films, film_scenes); a scene's clip is a job of the Studio (visual_jobs). The files
lie in data/media: the photo (film-<id>-photo.png), the clips (their jobs'), the joined film (film-<id>.mp4) - the
cleanup of old pictures leaves them alone while the film is there (`kept`, hub/retention.py).
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .db import utc_now

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


def kept(conn: sqlite3.Connection) -> set[str]:
    """What the cleanup of old pictures leaves in data/media (hub/retention.py): the beginnings of the names of the
    films' own files (film-<id>) and of their scenes' clips with what lies beside them (the job's id)."""
    names = {f"film-{row[0]}" for row in conn.execute("SELECT id FROM films")}
    return names | {row[0] for row in conn.execute("SELECT job_id FROM film_scenes WHERE job_id IS NOT NULL")}
