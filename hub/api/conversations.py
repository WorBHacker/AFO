"""/v1/conversations - chats and their messages; posting a message starts a streamed assistant reply.

The reply itself streams over the WebSocket /v1/events (chat.* events), not over this HTTP response:
POST .../messages returns at once with the stored user message, and the answer follows as events.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import store
from ..auth import Client
from ..chat import DEFAULT_EFFORT
from ..gateway import GatewayError
from .deps import require_client

router = APIRouter(prefix="/v1")

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class NewConversation(BaseModel):
    title: str = ""


class SendMessage(BaseModel):
    content: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    effort: str = DEFAULT_EFFORT


@router.get("/conversations")
async def list_conversations(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await request.app.state.db.acall(store.list_conversations)
    return {"conversations": rows, "busy": request.app.state.chat.busy}


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: NewConversation, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    cid = await request.app.state.db.acall(lambda conn: store.create_conversation(conn, title=body.title))
    return {"id": cid}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    def read(conn):
        if not store.conversation_exists(conn, conversation_id):
            return None
        return [m.to_json() for m in store.get_messages(conn, conversation_id)]

    messages = await request.app.state.db.acall(read)
    if messages is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    return {"id": conversation_id, "messages": messages, "busy": conversation_id in request.app.state.chat.busy}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> None:
    request.app.state.chat.cancel(conversation_id)
    deleted = await request.app.state.db.acall(lambda conn: store.delete_conversation(conn, conversation_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="no such conversation")


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(
    conversation_id: str, body: SendMessage, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    if body.effort not in EFFORTS:
        raise HTTPException(status_code=422, detail=f"effort must be one of {sorted(EFFORTS)}")
    exists = await request.app.state.db.acall(lambda conn: store.conversation_exists(conn, conversation_id))
    if not exists:
        raise HTTPException(status_code=404, detail="no such conversation")
    try:
        user_message = await request.app.state.chat.send(
            conversation_id, body.content, body.model_id, effort=body.effort, client_id=client.id
        )
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"user_message": user_message}


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_run(
    conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    return {"cancelled": request.app.state.chat.cancel(conversation_id)}
