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


@router.post("/gpu/give-back")
async def give_back(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.gpu.give_back()
