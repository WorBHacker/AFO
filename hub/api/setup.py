"""/v1/setup - the first-run wizard (stage 10.2, D37; hub/setup.py). The desktop app only: it puts programs on the
computer.

GET - this computer (graphics cards, the build it wants, RAM and page file) and whether the engine is there; GET
/models - the first model to offer for each purpose; POST /engine - llama.cpp and llama-swap from GitHub (the build
named, or the one recommended), progress as `setup.progress` events; POST /engine/cancel; POST /check - the window
has come: what local models need looked at, AFO's own engine fetched when it lacks something (39.1).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from common.i18n import tr

from .. import engines, hf
from .. import model_install as mi
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/setup")


class Engine(BaseModel):
    build: str = ""  # '' - the one this computer's card wants


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.setup.state()


@router.post("/check")
async def check(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """The window has come (39.1): what local models need looked at again; AFO's own engine missing - fetched."""
    found = await request.app.state.resources.check()
    return {"resources": found, "job": dict(request.app.state.setup.job)}


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


@router.get("/update")
async def update_info(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """The llama.cpp build running and whether GitHub has a newer one of its kind (10.4)."""
    try:
        return await request.app.state.setup.update_info()
    except engines.EngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/update")
async def update(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    setup = request.app.state.setup
    try:
        found = await setup.update_info()
    except engines.EngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not found["build"]:
        raise HTTPException(status_code=409, detail=tr("не найден llama-server, который можно обновить"))
    return {"job": setup.start(found["build"], update=True)}


@router.post("/engine/cancel")
async def cancel(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    request.app.state.setup.cancel()
    return {"job": dict(request.app.state.setup.job)}
