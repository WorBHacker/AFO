"""/v1/conversations/{id}/goal - a chat's goal (stage 11.1, D44): the chat works turn after turn until the hub's
check says it is met, a limit is reached, or it needs the human.

Reading is open to every client; setting, pausing, resuming, clearing and writing to a goal are the app's - setting it
approves the commands its criteria run (D17).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import APP_CLIENT, Client
from ..goals import GoalError
from .deps import own_conversation, require_client

router = APIRouter(prefix="/v1")


class Criterion(BaseModel):
    kind: Literal["command", "semantic"] | None = None  # left out: a command when there is one
    project: str = ""  # command: the attached project's name or id (the only one when left out)
    command: str = ""
    path: str = ""
    expected_exit_code: int = 0
    timeout_s: int = 300
    rule: str = ""  # semantic: what must be true, in words


class Limits(BaseModel):
    max_turns: int | None = None
    max_tokens: int | None = None
    max_minutes: int | None = None
    soft_ratio: float | None = None


class GoalBody(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    criteria: list[Criterion] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    model_id: str = Field(min_length=1)
    effort: str = "medium"
    limits: Limits = Field(default_factory=Limits)
    judge_model_id: str | None = None  # the model that checks it (11.2); left out - the role «Проверка цели»
    milestones: list[str] = Field(default_factory=list)  # its stages, in order (11.5)


class Stages(BaseModel):
    milestones: list[str]  # in place of the stages not done yet


class Resume(BaseModel):
    limits: Limits | None = None  # a parked goal goes on only with limits it has not used up


class Steer(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Wish(BaseModel):
    text: str = Field(min_length=1, max_length=4000)  # what the user wants, in their words
    model_id: str = ""  # the chat's model: it drafts when the role «Проверка цели» has none


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may set and steer a chat's goal")


async def _call(coro) -> Any:
    try:
        return await coro
    except GoalError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _given(limits: Limits | None) -> dict[str, Any]:
    return {k: v for k, v in limits.model_dump().items() if v is not None} if limits is not None else {}


@router.get("/conversations/{conversation_id}/goal")
async def get_goal(conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The chat's goal - the open one, else its last - with its recent checks and what the user wrote to it."""
    await own_conversation(request, client, conversation_id)
    return await _call(request.app.state.goals.get(conversation_id))


@router.put("/conversations/{conversation_id}/goal", status_code=201)
async def set_goal(conversation_id: str, body: GoalBody, request: Request,
                   client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """A new goal for the chat; its first turn starts at once."""
    _app_only(client)
    data = body.model_dump()
    data["criteria"] = [{k: v for k, v in c.items() if v not in (None, "")} for c in data["criteria"]]
    data["limits"] = _given(body.limits)
    goal = await _call(request.app.state.goals.set(conversation_id, data, by=client.name))
    return {"goal": goal}


@router.post("/conversations/{conversation_id}/goal/draft")
async def draft_goal(conversation_id: str, body: Wish, request: Request,
                     client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """A contract drafted by a model from the user's words, for the window to show and change; nothing is set."""
    _app_only(client)
    return await _call(request.app.state.goals.draft(conversation_id, body.text, body.model_id))


@router.post("/conversations/{conversation_id}/goal/pause", status_code=202)
async def pause_goal(conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]):
    """No next turn starts; the turn running now finishes."""
    _app_only(client)
    return {"goal": await _call(request.app.state.goals.pause(conversation_id, by=client.name))}


@router.post("/conversations/{conversation_id}/goal/resume", status_code=202)
async def resume_goal(conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)],
                      body: Resume | None = None) -> dict:  # fmt: skip
    """Back to work (a parked goal with higher limits): the next turn starts."""
    _app_only(client)
    limits = _given(body.limits) if body is not None else {}
    return {"goal": await _call(request.app.state.goals.resume(conversation_id, limits, by=client.name))}


@router.delete("/conversations/{conversation_id}/goal")
async def clear_goal(conversation_id: str, request: Request, client: Annotated[Client, Depends(require_client)]):
    """The goal is given up; the turn running now finishes, no next one starts."""
    _app_only(client)
    return {"goal": await _call(request.app.state.goals.clear(conversation_id, by=client.name))}


@router.put("/conversations/{conversation_id}/goal/milestones")
async def set_milestones(conversation_id: str, body: Stages, request: Request,
                         client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """The user's stages in place of those not done yet (11.5); the worker hears it at its next turn."""
    _app_only(client)
    return {"goal": await _call(request.app.state.goals.set_milestones(conversation_id, body.milestones,
                                                                       by=client.name))}  # fmt: skip


@router.post("/conversations/{conversation_id}/goal/steer")
async def steer_goal(conversation_id: str, body: Steer, request: Request,
                     client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """The user's word while the goal works: read at the start of the next turn, without stopping this one."""
    _app_only(client)
    return await _call(request.app.state.goals.steer(conversation_id, body.text, by=client.name))
