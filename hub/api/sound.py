"""/v1/sound - the sound added to a film (stage 53; hub/sound.py): the engine and its parts. The desktop app's.

GET / - the engine's build and whether it is here, the parts (voice, music, effects: ready, loading - how far,
missing - its size), the voices; POST /parts/<id> - «Скачать»: the engine the first time, and the part's model, in
the background; POST /parts/<id>/stop - the download stopped, what came stays; DELETE /parts/<id> - the part's model
gone from the disk. Event: sound.changed (the same as GET /).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import sound as s
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/sound")


def _sound(request: Request) -> s.Sound:
    return request.app.state.sound


def _refusal(exc: s.SoundError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _sound(request).state()


@router.post("/parts/{part}")
async def install(request: Request, part: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return await _sound(request).install(part)
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
