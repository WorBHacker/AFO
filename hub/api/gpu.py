"""/v1/gpu - the graphics card for models (stage 43.6): programs moved to the integrated graphics through Windows' own
«Графика» setting, the switch, the person's choice for a program, «Вернуть как было». The desktop app only."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1")


class Switch(BaseModel):
    on: bool


class Choice(BaseModel):
    exe: str
    where: Literal["card", "integrated", ""]  # "" - AFO decides again


@router.get("/gpu")
async def get_gpu(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.gpu.check()


@router.put("/gpu")
async def set_gpu(body: Switch, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.gpu.set_on(body.on)


@router.put("/gpu/programs")
async def choose(body: Choice, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.gpu.choose(body.exe, body.where)


@router.post("/gpu/continue")
async def go_on(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """«Продолжить» (48.9д): the card given by «Выгрузить» is everyone's again - the Studio's queue goes on, the
    person's work without them, the trials and tuning put off, at once, in the order of seniority."""
    state = request.app.state
    state.visual.release()
    state.card.go_on()
    return {"card": state.status.card() if state.status.card is not None else state.card.state()}


@router.post("/gpu/give-back")
async def give_back(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.gpu.give_back()
