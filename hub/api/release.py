"""/v1/release - AFO's own updates (step 10.4d): the newest release on GitHub, its installer's download and its run
over the installed program. The desktop app only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import Client
from ..release import ReleaseError
from .llama import require_app

router = APIRouter(prefix="/v1")


class Change(BaseModel):
    auto: bool


@router.get("/release")
async def get_release(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.releases.state()


@router.put("/release")
async def set_release(body: Change, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    from ..release import set_auto

    await request.app.state.db.acall(lambda conn: set_auto(conn, body.auto))
    return await request.app.state.releases.state()


@router.post("/release/check")
async def check(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await request.app.state.releases.check()


@router.post("/release/download")
async def download(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    releases = request.app.state.releases
    try:
        releases.download()
    except ReleaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await releases.state()


@router.post("/release/cancel")
async def cancel(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    request.app.state.releases.cancel()
    return await request.app.state.releases.state()


@router.post("/release/install")
async def install(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """The installer runs on its own: it stops this hub and the window, and opens the window again when done."""
    releases = request.app.state.releases
    try:
        releases.install()
    except ReleaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await releases.state()
