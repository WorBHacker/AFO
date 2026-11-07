"""/v1/visual - pictures (D55, stage 29.4; hub/visual.py). The desktop app's, like the installs of picture models.

GET /models - the picture models installed and the one a picture is drawn with by default; POST /images - a picture
asked for (a job, drawn in turn); GET /jobs - the latest; GET /jobs/<id>; POST /jobs/<id>/cancel - «Стоп»;
POST /videos - a video (29.8а, Wan2.2; a job like a picture's); GET /jobs/<id>/file - the picture itself (PNG; a
video - WebP); GET /pictures/<sha256> - a picture a chat holds (data/blobs, 29.6:
a model drew it). Events: visual.changed (a job), visual.progress (step and preview).
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import visual as v
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/visual")


def _visual(request: Request) -> v.Visual:
    return request.app.state.visual


def _refusal(exc: v.VisualError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/models")
async def models(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _visual(request).models()


class Image(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=120)
    width: int = Field(default=0, ge=0, le=4096)  # 0: the model's own size
    height: int = Field(default=0, ge=0, le=4096)
    seed: int | None = None  # none or below 0: a new one
    conversation_id: str | None = Field(default=None, max_length=64)
    # «По картинке» (29.6б): one source - a job's picture, a chat's (sha256), a PNG sent (base64) - and how much to
    # change it (0.1-0.95; 0.6 unless said); the size then follows its shape
    from_job: str = Field(default="", max_length=64)
    from_blob: str = Field(default="", max_length=64)
    from_png: str = Field(default="", max_length=28_000_000)
    strength: float | None = None


@router.post("/images")
async def image(request: Request, body: Image, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"job": await _visual(request).draw(body.model_dump(), client.name)}
    except v.VisualError as exc:
        raise _refusal(exc) from exc


class Video(BaseModel):
    """A video (29.8а): its shape, how long, «Качественнее» - Wan's own 1280×704."""

    prompt: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=120)
    shape: Literal["wide", "tall", "square"] = "wide"
    seconds: float = Field(default=2.0, ge=0.5, le=4.0)
    hd: bool = False
    seed: int | None = None
    conversation_id: str | None = Field(default=None, max_length=64)


@router.post("/videos")
async def video(request: Request, body: Video, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"job": await _visual(request).video(body.model_dump(), client.name)}
    except v.VisualError as exc:
        raise _refusal(exc) from exc


@router.get("/jobs")
async def jobs(request: Request, client: Annotated[Client, Depends(require_app)], limit: int = 50) -> dict:
    return {"jobs": await request.app.state.db.acall(lambda conn: v.jobs(conn, max(1, min(limit, 500))))}


async def _job(request: Request, job_id: str) -> dict:
    found = await request.app.state.db.acall(lambda conn: v.job(conn, job_id))
    if found is None:
        raise HTTPException(status_code=404, detail=tr("нет такой картинки"))
    return found


@router.get("/jobs/{job_id}")
async def one(request: Request, job_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    return {"job": await _job(request, job_id)}


@router.post("/jobs/{job_id}/cancel")
async def cancel(request: Request, job_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"job": await _visual(request).cancel(job_id)}
    except v.VisualError as exc:
        raise _refusal(exc) from exc


@router.get("/jobs/{job_id}/file")
async def file(request: Request, job_id: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    path = _visual(request).file(await _job(request, job_id))
    if path is None:
        raise HTTPException(status_code=404, detail=tr("картинки ещё нет"))
    return FileResponse(path, media_type=v.picture_type(path) or "application/octet-stream")  # a video: WebP


@router.get("/pictures/{name}")
async def picture(request: Request, name: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    """A picture a chat holds (data/blobs, by its sha256; 29.6) - only a picture: other files sent with messages are
    not given here."""
    path = request.app.state.chat.blobs / name if re.fullmatch(r"[0-9a-f]{64}", name) else None
    kind = await asyncio.to_thread(v.picture_type, path) if path is not None else None
    if kind is None:
        raise HTTPException(status_code=404, detail=tr("нет такой картинки"))
    return FileResponse(path, media_type=kind)
