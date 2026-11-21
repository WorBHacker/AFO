"""/v1/enhance - «Улучшить ролик» (stage 54; hub/enhance.py): its parts. The desktop app's.

GET / - the parts (smooth, sharp, detail: ready, loading - how far, missing - its size, unavailable - why); POST
/parts/<id> - «Скачать», in the background; POST /parts/<id>/stop - the download stopped, what came stays; DELETE
/parts/<id> - the part gone from the disk. Event: enhance.changed (the same as GET /).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import enhance as e
from .. import hf
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/enhance")


def _enhance(request: Request) -> e.Enhance:
    return request.app.state.enhance


def _refusal(exc: e.EnhanceError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


class Agreed(BaseModel):
    licence_ok: bool = False  # (62) the person's «Понимаю, скачать» for a licence that forbids commercial use


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _enhance(request).state()


@router.post("/parts/{part}")
async def install(request: Request, part: str, client: Annotated[Client, Depends(require_app)],
                  body: Agreed | None = None) -> dict:  # fmt: skip
    try:
        return await _enhance(request).install(part, agreed=bool(body and body.licence_ok))
    except hf.NotForWork as exc:  # (62) the window asks the person and sends it again with `licence_ok`
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except e.EnhanceError as exc:
        raise _refusal(exc) from exc


@router.post("/parts/{part}/stop")
async def stop(request: Request, part: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return await _enhance(request).stop(part)
    except e.EnhanceError as exc:
        raise _refusal(exc) from exc


@router.delete("/parts/{part}")
async def remove(request: Request, part: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return await _enhance(request).remove(part)
    except e.EnhanceError as exc:
        raise _refusal(exc) from exc
