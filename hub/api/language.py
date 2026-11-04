"""/v1/language - the language of what the hub writes for people (step 10.5): the desktop app chooses it (Settings →
Language) and tells it again on every connect."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from common import i18n

from .. import language
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class Choice(BaseModel):
    language: str = Field(max_length=8)


def _state() -> dict:
    return {"language": i18n.language(), "languages": i18n.LANGUAGES}


@router.get("/language")
async def get_language(client: Annotated[Client, Depends(require_client)]) -> dict:
    return _state()


@router.put("/language")
async def set_language(body: Choice, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app chooses AFO's language")
    if body.language not in i18n.LANGUAGES:
        raise HTTPException(status_code=422, detail=f"AFO does not speak {body.language!r}")
    await request.app.state.db.acall(lambda conn: language.store(conn, body.language))
    return _state()
