"""/v1/tune - launch settings by measurement (step 15.2, D49): what was measured for each model, a model's tuning or
every llama.cpp model's, and whether AFO tunes by itself. The desktop app only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from common.i18n import tr

from .. import llama_config, tuning
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1")


class Change(BaseModel):
    auto: bool


async def _state(request: Request) -> dict:
    tuner = request.app.state.tuner
    return {"job": tuner.state_job(), "results": tuner.results, "auto": await request.app.state.db.acall(tuning.auto)}


def _config() -> str:
    try:
        return llama_config.config_path().read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=409, detail=tr("нет конфига llama-swap")) from exc


@router.get("/tune")
async def get_tune(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _state(request)


@router.put("/tune")
async def set_tune(body: Change, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    await request.app.state.db.acall(lambda conn: tuning.set_auto(conn, body.auto))
    return await _state(request)


@router.post("/tune/all")
async def tune_all(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    _config()
    request.app.state.tuner.request_all()
    return await _state(request)


@router.post("/tune/cancel")
async def cancel(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    request.app.state.tuner.cancel()
    return await _state(request)


@router.post("/tune/{model_id}")
async def tune_one(model_id: str, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    if model_id not in llama_config.model_blocks(_config()):
        raise HTTPException(status_code=404, detail=tr("в config.yaml нет модели {model}", model=model_id))
    request.app.state.tuner.request(model_id)
    return await _state(request)
