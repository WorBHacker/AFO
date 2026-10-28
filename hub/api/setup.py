"""/v1/setup - the first-run wizard (stage 10.2, D37; hub/setup.py). The desktop app only: it puts programs on the
computer.

GET - this computer (graphics cards, the build it wants, RAM and page file) and whether the engine is there; GET
/models - the first model to offer for each purpose; POST /engine - llama.cpp and llama-swap from GitHub (the build
named, or the one recommended), progress as `setup.progress` events; POST /engine/cancel.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import hf
from .. import model_install as mi
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/setup")


class Engine(BaseModel):
    build: str = ""  # '' - the one this computer's card wants


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.setup.state()


@router.get("/models")
async def first_models(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"models": await request.app.state.setup.first_models()}
    except (hf.HubError, mi.InstallError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/engine")
async def engine(request: Request, body: Engine, client: Annotated[Client, Depends(require_app)]) -> dict:
    setup = request.app.state.setup
    build = body.build or (await setup.state())["build"]["build"]
    try:
        return {"job": setup.start(build)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/engine/cancel")
async def cancel(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    request.app.state.setup.cancel()
    return {"job": dict(request.app.state.setup.job)}
