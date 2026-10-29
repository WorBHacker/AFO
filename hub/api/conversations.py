"""/v1/conversations - chats and their messages; posting a message starts a streamed assistant reply.

The reply itself streams over the WebSocket /v1/events (chat.* events), not over this HTTP response:
POST .../messages returns at once with the stored user message, and the answer follows as events.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import store
from ..auth import APP_CLIENT, Client
from ..chat import DEFAULT_EFFORT, BudgetReached
from ..gateway import GatewayError
from .deps import require_client

router = APIRouter(prefix="/v1")

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class NewConversation(BaseModel):
    title: str = ""
    home_project: str | None = None  # a session of this project: it lives in «Проекты» (2026-10-28)


class ConversationPatch(BaseModel):
    title: str = Field(max_length=200)


class SentFile(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    data: str  # the file's bytes, base64


class SendMessage(BaseModel):
    content: str = ""  # may be empty when files go with it
    model_id: str = Field(min_length=1)
    effort: str = DEFAULT_EFFORT
    confirm_over_budget: bool = False  # the user saw that a budget is used up and sends anyway
    files: list[SentFile] = Field(default_factory=list)  # files and pictures sent with it (2026-10-29)


@router.get("/conversations")
async def list_conversations(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await request.app.state.db.acall(store.list_conversations)
    return {"conversations": rows, "busy": request.app.state.chat.busy}


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: NewConversation, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    def make(conn):
        if body.home_project and conn.execute("SELECT 1 FROM projects WHERE id = ?", (body.home_project,)).fetchone() \
                is None:  # fmt: skip
            return None
        return store.create_conversation(conn, title=body.title, home_project=body.home_project)

    cid = await request.app.state.db.acall(make)
    if cid is None:
        raise HTTPException(status_code=404, detail="no such project")
    return {"id": cid}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    def read(conn):
        title = store.get_title(conn, conversation_id)
        if title is None:
            return None
        usage = store.usage_by_message(conn, conversation_id)
        messages = [{**m.to_json(), "usage": usage.get(m.id)} for m in store.get_messages(conn, conversation_id)]
        return (
            title,
            messages,
            store.conversation_projects(conn, conversation_id),
            store.context_settings(conn, conversation_id),
        )

    found = await request.app.state.db.acall(read)
    if found is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    title, messages, attached, settings = found
    return {
        "id": conversation_id,
        "title": title,
        "messages": messages,
        "projects": attached,
        "context": settings,
        "busy": conversation_id in request.app.state.chat.busy,
    }


class Pinned(BaseModel):
    project_id: str
    path: str


class ContextSettings(BaseModel):
    map: bool | None = None
    live: bool | None = None
    pinned: list[Pinned] | None = None
    mode: Literal["read_only", "ask", "sandbox", "trusted"] | None = None  # the chat's rights (D17, 9.4b)
    instructions: str | None = None  # the chat's own system prompt (D39)
    preset: Literal["", "precise", "balanced", "creative"] | None = None  # over the model's parameters
    refusal_vector: Literal["", "on", "off"] | None = None  # Strata's refusal vector for this chat; '' - as the model


@router.put("/conversations/{conversation_id}/context")
async def set_context(
    conversation_id: str, body: ContextSettings, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    """What the chat's context includes from its next message on (D22): the map, the live state, pinned files."""
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change a chat's context")
    pinned = [p.model_dump() for p in body.pinned] if body.pinned is not None else None

    def write(conn):
        if store.get_title(conn, conversation_id) is None:
            return None
        return store.set_context_settings(conn, conversation_id, map=body.map, live=body.live, pinned=pinned,
                                          mode=body.mode, instructions=body.instructions, preset=body.preset,
                                          refusal_vector=body.refusal_vector)  # fmt: skip

    settings = await request.app.state.db.acall(write)
    if settings is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    return settings


class ProjectList(BaseModel):
    project_ids: list[str]


@router.put("/conversations/{conversation_id}/projects")
async def attach_projects(
    conversation_id: str, body: ProjectList, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    """The projects this chat works with (D27): its tools reach only these, its prompt describes them."""
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change a chat's projects")

    def write(conn):
        if store.get_title(conn, conversation_id) is None:
            return None
        return store.set_conversation_projects(conn, conversation_id, body.project_ids)

    attached = await request.app.state.db.acall(write)
    if attached is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    await request.app.state.bus.publish(
        "chat.projects", {"conversation_id": conversation_id, "projects": attached}, persist=False
    )
    return {"projects": attached}


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: ConversationPatch, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    title = body.title.strip()
    renamed = await request.app.state.db.acall(lambda conn: store.set_title(conn, conversation_id, title))
    if not renamed:
        raise HTTPException(status_code=404, detail="no such conversation")
    await request.app.state.bus.publish("chat.titled", {"conversation_id": conversation_id, "title": title})
    return {"id": conversation_id, "title": title}


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
    if not body.content.strip() and not body.files:
        raise HTTPException(status_code=422, detail="an empty message: write something or send a file")
    exists = await request.app.state.db.acall(lambda conn: store.conversation_exists(conn, conversation_id))
    if not exists:
        raise HTTPException(status_code=404, detail="no such conversation")
    try:
        user_message = await request.app.state.chat.send(
            conversation_id,
            body.content,
            body.model_id,
            effort=body.effort,
            client_id=client.id,
            confirm_over_budget=body.confirm_over_budget,
            files=[f.model_dump() for f in body.files],
        )
    except BudgetReached as exc:
        detail = {
            "code": "budget_exceeded",
            "message": str(exc),
            "budgets": exc.budgets,
            "can_confirm": exc.can_confirm,  # send again with confirm_over_budget to go ahead
        }
        raise HTTPException(status_code=409, detail=detail) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"user_message": user_message}


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_run(
    conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    return {"cancelled": request.app.state.chat.cancel(conversation_id)}
