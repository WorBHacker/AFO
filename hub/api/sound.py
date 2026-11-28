"""/v1/sound - the sound added to a film (stage 53; hub/sound.py): the engine and its parts. The desktop app's.

GET / - the engine's build and whether it is here, the parts (voice, music, effects: ready, loading - how far,
missing - its size), the voices; POST /parts/<id> - «Скачать»: the engine the first time, and the part's model, in
the background; POST /parts/<id>/stop - the download stopped, what came stays; DELETE /parts/<id> - the part's model
gone from the disk. Event: sound.changed (the same as GET /).

(«Стабилизация», portion 5) The Studio's «Аудио» - pieces of sound of their own (hub/sound_pieces.py): GET /pieces -
the latest, the newest first; POST /pieces {kind, words, voice, seconds} - one into the queue; POST
/pieces/<id>/cancel - ✕; DELETE /pieces/<id> - gone for good, its file too; GET /pieces/<id>/file - its WAV. Event:
sound.piece {piece} - one asked for, going on, done, failed, removed (status «removed»).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import hf
from .. import sound as s
from .. import sound_pieces as sp
from ..auth import Client
from ..gpu_card import STUDIO, ask_as
from .enhance import Agreed
from .llama import require_app

router = APIRouter(prefix="/v1/sound")


def _sound(request: Request) -> s.Sound:
    return request.app.state.sound


def _pieces(request: Request) -> sp.Pieces:
    return request.app.state.pieces


class Piece(BaseModel):
    kind: Literal["voice", "music", "effect"]
    words: str = Field(min_length=1, max_length=sp.WORDS)
    voice: str = Field(default="", max_length=40)
    seconds: float | None = None


@router.get("/pieces")
async def pieces(request: Request, client: Annotated[Client, Depends(require_app)], limit: int = 50) -> dict:
    found = await request.app.state.db.acall(lambda conn: sp.pieces(conn, max(1, min(limit, 200))))
    return {"pieces": found}


@router.post("/pieces")
async def make_piece(request: Request, body: Piece, client: Annotated[Client, Depends(require_app)]) -> dict:
    ask_as(STUDIO)  # the person's, in the Studio
    try:
        return {"piece": await _pieces(request).make(body.model_dump(), client.name)}
    except sp.PieceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/pieces/{piece_id}/cancel")
async def cancel_piece(request: Request, piece_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"piece": await _pieces(request).cancel(piece_id)}
    except sp.PieceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.delete("/pieces/{piece_id}")
async def remove_piece(request: Request, piece_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        await _pieces(request).remove(piece_id)
    except sp.PieceError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"removed": piece_id}


@router.get("/pieces/{piece_id}/file")
async def piece_file(request: Request, piece_id: str, client: Annotated[Client, Depends(require_app)]):
    found = await request.app.state.db.acall(lambda conn: sp.piece(conn, piece_id))
    path = _pieces(request).file(found) if found is not None else None
    if path is None:
        raise HTTPException(status_code=404, detail=tr("нет такого звука"))
    return FileResponse(path, media_type="audio/wav")


def _refusal(exc: s.SoundError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _sound(request).state()


@router.post("/parts/{part}")
async def install(request: Request, part: str, client: Annotated[Client, Depends(require_app)],
                  body: Agreed | None = None) -> dict:  # fmt: skip
    try:
        return await _sound(request).install(part, agreed=bool(body and body.licence_ok))
    except hf.NotForWork as exc:  # (62) the window asks the person and sends it again with `licence_ok`
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except s.SoundError as exc:
        raise _refusal(exc) from exc


@router.post("/parts/{part}/stop")
async def stop(request: Request, part: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return await _sound(request).stop(part)
    except s.SoundError as exc:
        raise _refusal(exc) from exc


@router.delete("/parts/{part}")
async def remove(request: Request, part: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return await _sound(request).remove(part)
    except s.SoundError as exc:
        raise _refusal(exc) from exc
