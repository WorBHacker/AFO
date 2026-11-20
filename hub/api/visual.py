"""/v1/visual - pictures (D55, stage 29.4; hub/visual.py). The desktop app's, like the installs of picture models.

GET /models - the picture models installed and the one a picture is drawn with by default; POST /images - a picture
asked for (a job, drawn in turn); GET /jobs - the latest; GET /jobs/<id>; POST /jobs/<id>/cancel - «Стоп»;
POST /videos - a video (29.8а, Wan2.2; a job like a picture's); POST /improve - «Улучшить ролик» (54.5): a video
of the Studio's or a film's improved, a job too; GET /jobs/<id>/file - the picture itself (PNG; a
video - WebP); GET /jobs/<id>/mp4 - a video's MP4 (29.8в, ffmpeg; 48.12: the window plays it); GET
/pictures/<sha256> - a picture a chat holds (data/blobs, 29.6: a model drew it), /pictures/<sha256>/mp4 - a chat's
video as MP4 (48.12). Events: visual.changed (a job), visual.progress (step and preview).
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from common.i18n import tr
from common.picture_sizes import GUIDANCE, SECONDS, STEPS

from .. import visual as v
from ..auth import Client
from ..gpu_card import STUDIO, ask_as
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
    ask_as(STUDIO)  # the person's, in the Studio: the card given away is theirs again (48.9г)
    try:
        return {"job": await _visual(request).draw(body.model_dump(), client.name)}
    except v.VisualError as exc:
        raise _refusal(exc) from exc


class Video(BaseModel):
    """A video (29.8а): its shape, how long, «Качественнее» - the family's own larger size; (48.5) by a picture - its
    first frame, as «По картинке» takes one (a job's picture, a chat's, a PNG sent; (50.3) a video's last frame) -
    and the last frame too; (50.4) its own steps and guidance - none: the ones the model was installed with; (52.3)
    with sound - when the model films it - and what is heard."""

    prompt: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=120)
    shape: Literal["wide", "tall", "square"] = "wide"
    seconds: float = Field(default=2.0, ge=SECONDS[0], le=SECONDS[1])
    hd: bool = False
    seed: int | None = None
    steps: int | None = Field(default=None, ge=STEPS[0], le=STEPS[1])
    cfg: float | None = Field(default=None, ge=GUIDANCE[0], le=GUIDANCE[1])
    conversation_id: str | None = Field(default=None, max_length=64)
    sound: bool = True
    sound_words: str = Field(default="", max_length=v.SOUND_WORDS)
    from_job: str = Field(default="", max_length=64)
    from_blob: str = Field(default="", max_length=64)
    from_png: str = Field(default="", max_length=28_000_000)
    last_job: str = Field(default="", max_length=64)
    last_blob: str = Field(default="", max_length=64)
    last_png: str = Field(default="", max_length=28_000_000)


@router.post("/videos")
async def video(request: Request, body: Video, client: Annotated[Client, Depends(require_app)]) -> dict:
    ask_as(STUDIO)  # the person's, in the Studio: the card given away is theirs again (48.9г)
    try:
        return {"job": await _visual(request).video(body.model_dump(), client.name)}
    except v.VisualError as exc:
        raise _refusal(exc) from exc


class Subject(BaseModel):
    """(56) «Только предмет»: where the object was clicked - `share` of the video's length, `x`, `y` of the frame."""

    share: float = Field(ge=0.0, le=1.0)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class Improve(BaseModel):
    """«Улучшить ролик» (54.5): a video of the Studio's or a film's joined one; twice as large (fast or with
    details), 60 frames a second or slowed, only the object clicked (56) - any of them, or several."""

    from_job: str = Field(default="", max_length=64)
    film: str = Field(default="", max_length=64)
    size: Literal["sharp", "detail"] | None = None
    motion: Literal["fps60", "slow2", "slow4"] | None = None
    select: Subject | None = None


@router.post("/improve")
async def improve(request: Request, body: Improve, client: Annotated[Client, Depends(require_app)]) -> dict:
    ask_as(STUDIO)  # the person's, in the Studio: the card given away is theirs again (48.9г)
    try:
        return {"job": await _visual(request).improve(body.model_dump(), client.name)}
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
    # a video: WebP - or, with sound (52.3), WebM
    return FileResponse(path, media_type=v.clip_type(path) or "application/octet-stream")


@router.get("/jobs/{job_id}/mp4")
async def mp4(request: Request, job_id: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    """A video's MP4 (29.8в): made the first time it is asked for - ffmpeg fetched first, ~86 MB, once."""
    try:
        path = await _visual(request).mp4(await _job(request, job_id))
    except v.VisualError as exc:
        raise _refusal(exc) from exc
    return FileResponse(path, media_type="video/mp4")


@router.get("/pictures/{name}")
async def picture(request: Request, name: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    """A picture a chat holds (data/blobs, by its sha256; 29.6) - only a picture: other files sent with messages are
    not given here."""
    path = request.app.state.chat.blobs / name if re.fullmatch(r"[0-9a-f]{64}", name) else None
    kind = await asyncio.to_thread(v.picture_type, path) if path is not None else None
    if kind is None:
        raise HTTPException(status_code=404, detail=tr("нет такой картинки"))
    return FileResponse(path, media_type=kind)


@router.get("/pictures/{name}/mp4")
async def picture_mp4(request: Request, name: str, client: Annotated[Client, Depends(require_app)]) -> FileResponse:
    """The MP4 of a video a chat holds (48.12): its card in the window plays it - made once, ffmpeg as for a job's."""
    if not re.fullmatch(r"[0-9a-f]{64}", name):
        raise HTTPException(status_code=404, detail=tr("нет такой картинки"))
    try:
        path = await _visual(request).blob_mp4(request.app.state.chat.blobs, name)
    except v.VisualError as exc:
        raise _refusal(exc) from exc
    return FileResponse(path, media_type="video/mp4")
