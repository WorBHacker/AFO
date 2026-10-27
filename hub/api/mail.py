"""/v1/mail - the mailbox the autonomous agent reads and writes from (stage 9.4b, Q21). The desktop app only.

GET says which mailbox is connected (its address and servers, whether the app password is set - never the password).
PUT connects one (the servers of well-known providers fill in by themselves), DELETE disconnects it, POST /check
tries both servers: all behind the connections' password (D13), as the letters are the user's.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import mail as ml
from ..auth import Client
from .connections import Ticket, _app_only, _open
from .deps import require_client

router = APIRouter(prefix="/v1/mail")


class Account(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    password: str = Field(default="", max_length=500)  # '' keeps the one set
    login: str = Field(default="", max_length=254)
    imap_host: str = Field(default="", max_length=253)
    imap_port: int | None = None
    imap_security: str = ""
    smtp_host: str = Field(default="", max_length=253)
    smtp_port: int | None = None
    smtp_security: str = ""


def _shown(state, found: dict[str, Any] | None) -> dict[str, Any]:
    return {"account": found, "password": bool(state.secrets.get(ml.PASSWORD)) if found else False}


@router.get("")
async def get_mail(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    state = request.app.state
    return _shown(state, await state.db.acall(ml.account))


@router.put("")
async def set_mail(body: Account, request: Request, client: Annotated[Client, Depends(require_client)],
                   ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    state = request.app.state
    try:
        checked = ml.check_account({k: v for k, v in body.model_dump().items() if k != "password"})
    except ml.MailError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not body.password and not state.secrets.get(ml.PASSWORD):
        raise HTTPException(status_code=422, detail="the mailbox's app password (the provider's settings make one)")
    if body.password:
        state.secrets.set(ml.PASSWORD, body.password)
    await state.db.acall(lambda conn: ml.set_account(conn, checked))
    await state.bus.publish("mail.connected", {"address": checked["address"]})
    return _shown(state, checked)


@router.delete("")
async def delete_mail(request: Request, client: Annotated[Client, Depends(require_client)],
                      ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    state = request.app.state
    await state.db.acall(lambda conn: ml.set_account(conn, None))
    state.secrets.delete(ml.PASSWORD)
    await state.bus.publish("mail.disconnected", {})
    return _shown(state, None)


@router.post("/check")
async def check_mail(request: Request, client: Annotated[Client, Depends(require_client)],
                     ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    try:
        box = await request.app.state.mail.mailbox()
        return {"ok": True, "detail": await asyncio.to_thread(box.check)}
    except ml.MailError as exc:
        return {"ok": False, "detail": str(exc)}
    except OSError as exc:
        return {"ok": False, "detail": f"the server did not answer: {exc}"}
