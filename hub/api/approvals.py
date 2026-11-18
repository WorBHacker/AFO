"""/v1/approvals - the approval queue (stage 4, D17, D18): what agents wait to be allowed, and the user's answers.

The app's alone for now: a request shows code and commands, and answering one lets an agent act. Telegram (D21)
gets its own rights with stage 5. `approval.requested` / `approval.decided` tell clients to look again.
`/approvals/blocked` is what a trusted chat's rules refused unasked (0.1.17); `/approvals/{id}/pass` lets one through.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..approvals import ApprovalError
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class Answer(BaseModel):
    allow: bool
    scope: Literal["once", "chat", "project"] = "once"  # allowed: this time, for the chat, or always in the project
    comment: str = Field(default="", max_length=2000)  # a denial's reason: the agent reads it
    hunks: list[int] | None = None  # a write allowed in part (17.2, D51): the hunks of its preview, this once


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may see and answer approvals")


def _error(exc: ApprovalError) -> HTTPException:
    text = str(exc)
    return HTTPException(status_code=404 if text.startswith("no such") else 409, detail=text)


@router.get("/approvals")
async def pending(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    return {"approvals": await request.app.state.approvals.pending()}


@router.get("/approvals/blocked")
async def blocked(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """«Недавно заблокировано» (0.1.17): what a trusted chat's rules refused without asking, the newest first."""
    _app_only(client)
    return {"blocked": await request.app.state.approvals.blocked()}


@router.post("/approvals/{approval_id}/pass")
async def let_through(approval_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """«Разрешить» on a refusal of the rules: the chat's next same request goes through, once."""
    _app_only(client)
    try:
        return await request.app.state.approvals.let_through(approval_id, by=client.name)
    except ApprovalError as exc:
        raise _error(exc) from exc


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        return await request.app.state.approvals.get(approval_id)
    except ApprovalError as exc:
        raise _error(exc) from exc


@router.post("/approvals/{approval_id}")
async def answer(
    approval_id: str, body: Answer, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    queue = request.app.state.approvals
    try:
        await queue.get(approval_id)
        return await queue.decide(approval_id, allow=body.allow, scope=body.scope, comment=body.comment, by=client.name,
                                  hunks=body.hunks)  # fmt: skip
    except ApprovalError as exc:
        raise _error(exc) from exc
