"""/v1/films - «Режиссёр» (stage 50; hub/director.py): a film of scenes. The desktop app's, like the Studio.

GET / - the films, the newest first, each with its scenes; POST / - a film begun: the idea, the length, the
product's photo; GET /<id>; PATCH /<id> - its own changed; DELETE /<id>; POST /<id>/storyboard - «Собрать сценарий»:
the director's model is asked, in the background; PUT /<id>/scenes/<n> - a scene changed by hand; POST /<id>/shoot -
«Снять всё» and «Продолжить», {scene} - «Переснять» that one; POST /<id>/stop - «Стоп»; POST /<id>/join - «Собрать
ролик», {fade}; GET /<id>/file - the joined MP4; GET /<id>/photo - the product's photo. A scene's clip is a job of
the Studio's: /v1/visual/jobs/<job>/mp4. Events: film.changed (the film with its scenes), film.progress (a scene
begun: id, scene, job), film.deleted (id).

(Stage 53) The added sound: `voice` - the narrator's voice (GET /v1/sound names them; '' - none), a scene's `say` -
the line read while it goes; `music` - '' | made | file, `music_words` - what music is made, `music_path` (PATCH) -
the user's own file. They are made and mixed in when the film is joined; POST /<id>/stop stops their making.

(Stage 51) `checks` - the film's switch, at its beginning and in PATCH: each scene filmed is looked at by the
director's model, one that is not good is filmed anew. The film says the scene looked at now (`checking`), a scene -
what the look said of it (`verdict`, `reason`, `tries`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import director as d
from ..auth import Client
from ..gpu_card import STUDIO, ask_as
from .llama import require_app

router = APIRouter(prefix="/v1/films")
Shape = Literal["wide", "tall", "square"]


def _director(request: Request) -> d.Director:
    return request.app.state.director


def _refusal(exc: d.DirectorError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _film(request: Request, film_id: str) -> dict:
    found = await request.app.state.db.acall(lambda conn: d.film(conn, film_id))
    if found is None:
        raise HTTPException(status_code=404, detail=tr("нет такого фильма"))
    return found


class Photo(BaseModel):
    """The product's photo: a PNG sent (base64), a picture of the Studio's (a job), a chat's (its sha256)."""

    photo_png: str = Field(default="", max_length=28_000_000)
    from_job: str = Field(default="", max_length=64)
    from_blob: str = Field(default="", max_length=64)


class New(Photo):
    idea: str = Field(min_length=1, max_length=4000)
    seconds: int = Field(default=15, ge=d.FILM_S[0], le=d.FILM_S[1])
    shape: Shape = "wide"
    director: str = Field(default="", max_length=200)  # '' - the model of the role «Режиссёр»
    model: str = Field(default="", max_length=120)  # '' - the first video model that can film it
    checks: bool = True  # (51.1) each scene filmed is looked at by the director's model
    sound: bool = True  # (52.6) the scenes are filmed with sound, when the video model films it
    voice: str = Field(default="", max_length=40)  # (53.3) the narrator's voice; '' - no narrator
    music: Literal["", "made"] = ""  # (53.4) music made by its description
    music_words: str = Field(default="", max_length=2000)


class Change(Photo):
    """Only what is named changes; `photo: ""` - the photo taken away."""

    idea: str | None = Field(default=None, max_length=4000)
    seconds: int | None = Field(default=None, ge=d.FILM_S[0], le=d.FILM_S[1])
    shape: Shape | None = None
    director: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=120)
    fade: float | None = Field(default=None, ge=d.FADE_S[0], le=d.FADE_S[1])
    checks: bool | None = None
    sound: bool | None = None
    photo: Literal[""] | None = None
    voice: str | None = Field(default=None, max_length=40)  # (53.3)
    music: Literal["", "made", "file"] | None = None  # (53.4)
    music_words: str | None = Field(default=None, max_length=2000)
    music_path: str | None = Field(default=None, max_length=1000)  # the user's own file, on this computer


class Scene(BaseModel):
    """Only what is named changes; a seed of none or below 0 - a new one each time the scene is filmed."""

    words: str | None = Field(default=None, max_length=2000)
    prompt: str | None = Field(default=None, max_length=4000)
    mode: Literal["text", "picture", "continue", "frames"] | None = None
    seconds: float | None = Field(default=None, ge=d.SCENE_S[0], le=d.SCENE_S[1])
    seed: int | None = None
    sound_words: str | None = Field(default=None, max_length=2000)  # (52.6) what is heard in the scene
    say: str | None = Field(default=None, max_length=2000)  # (53.3) the narrator's line


class Shoot(BaseModel):
    scene: int | None = Field(default=None, ge=1)  # «Переснять» this one; none - all that are not filmed


class Join(BaseModel):
    fade: float | None = Field(default=None, ge=d.FADE_S[0], le=d.FADE_S[1])  # none - the film's own


@router.get("")
async def films(request: Request, client: Annotated[Client, Depends(require_app)], limit: int = 50) -> dict:
    return {"films": await request.app.state.db.acall(lambda conn: d.films(conn, max(1, min(limit, 200))))}


@router.post("")
async def create(request: Request, body: New, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"film": await _director(request).create(body.model_dump())}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.get("/{film_id}")
async def one(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    return {"film": await _film(request, film_id)}


@router.patch("/{film_id}")
async def change(request: Request, film_id: str, body: Change,
                 client: Annotated[Client, Depends(require_app)]) -> dict:  # fmt: skip
    try:
        return {"film": await _director(request).change(film_id, body.model_dump(exclude_unset=True))}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.delete("/{film_id}")
async def remove(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        await _director(request).remove(film_id)
    except d.DirectorError as exc:
        raise _refusal(exc) from exc
    return {"deleted": film_id}


@router.post("/{film_id}/storyboard")
async def storyboard(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    """«Собрать сценарий»: the film as it begins to be written; the scenes come with film.changed."""
    try:
        return {"film": await _director(request).write(film_id, background=True)}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.put("/{film_id}/scenes/{n}")
async def scene(request: Request, film_id: str, n: int, body: Scene,
                client: Annotated[Client, Depends(require_app)]) -> dict:  # fmt: skip
    try:
        return {"film": await _director(request).edit_scene(film_id, n, body.model_dump(exclude_unset=True))}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.post("/{film_id}/shoot")
async def shoot(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)],
                body: Shoot | None = None) -> dict:  # fmt: skip
    ask_as(STUDIO)  # the person's, in the Studio: the card given away is theirs again (48.9г)
    try:
        return {"film": await _director(request).shoot(film_id, only=body.scene if body else None)}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.post("/{film_id}/stop")
async def stop(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"film": await _director(request).stop(film_id)}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.post("/{film_id}/join")
async def join(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)],
               body: Join | None = None) -> dict:  # fmt: skip
    """«Собрать ролик»: the film as it begins to be joined; the file comes with film.changed."""
    try:
        return {"film": await _director(request).join(film_id, fade=body.fade if body else None)}
    except d.DirectorError as exc:
        raise _refusal(exc) from exc


@router.get("/{film_id}/file")
async def file(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    path = _director(request).own((await _film(request, film_id))["file"])
    if path is None:
        raise HTTPException(status_code=404, detail=tr("ролик ещё не собран"))
    return FileResponse(path, media_type="video/mp4")


@router.get("/{film_id}/photo")
async def photo(request: Request, film_id: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    path = _director(request).own((await _film(request, film_id))["photo"])
    if path is None:
        raise HTTPException(status_code=404, detail=tr("у фильма нет фото товара"))
    return FileResponse(path, media_type="image/png")
