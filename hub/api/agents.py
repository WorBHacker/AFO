"""/v1/agents and /v1/runs - the hub's own agents (stage 5, D16): profiles, and runs of tasks.

Reading is open to every client; making and changing profiles, starting and stopping runs are the app's (an agent
writes files and runs commands, within its rights - D17). A run is a conversation too: its messages come from
/v1/conversations/{id}.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import agents
from ..agents import AgentError
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class Profile(BaseModel):
    name: str
    instructions: str = ""
    model_id: str
    tools: list[str] = Field(default_factory=list)  # [] - all of the hub's tools
    mode: str = "ask"
    project_ids: list[str] = Field(default_factory=list)
    max_steps: int = 30
    max_minutes: int = 30
    budget_usd: float | None = None


class Task(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    project_ids: list[str] | None = None  # the profile's projects when left out


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may manage agents and their runs")


def _error(exc: AgentError) -> HTTPException:
    text = str(exc)
    return HTTPException(status_code=404 if text.startswith("no such") else 422, detail=text)


async def _db(request: Request, fn) -> Any:
    try:
        return await request.app.state.db.acall(fn)
    except AgentError as exc:
        raise _error(exc) from exc


@router.get("/agents")
async def list_profiles(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"agents": await _db(request, agents.list_profiles), "tools": agents.TOOL_NAMES}


@router.post("/agents", status_code=201)
async def create_profile(body: Profile, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    profile = await _db(request, lambda conn: agents.save_profile(conn, body.model_dump()))
    await request.app.state.bus.publish("agents.changed", {"agent_id": profile["id"]}, persist=False)
    return profile


@router.get("/agents/{agent_id}")
async def get_profile(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await _db(request, lambda conn: agents.get_profile(conn, agent_id))


@router.put("/agents/{agent_id}")
async def update_profile(
    agent_id: str, body: Profile, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    profile = await _db(request, lambda conn: agents.save_profile(conn, body.model_dump(), agent_id))
    await request.app.state.bus.publish("agents.changed", {"agent_id": agent_id}, persist=False)
    return profile


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_profile(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    if not await _db(request, lambda conn: agents.delete_profile(conn, agent_id)):
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    await request.app.state.bus.publish("agents.changed", {"agent_id": agent_id}, persist=False)


@router.get("/agents/{agent_id}/versions")
async def versions(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await _db(request, lambda conn: conn.execute(
        "SELECT version, data, created_at FROM agent_profile_versions WHERE profile_id = ? ORDER BY version DESC",
        (agent_id,)).fetchall())  # fmt: skip
    return {"versions": [{"version": r["version"], "created_at": r["created_at"], **json.loads(r["data"])}
                         for r in rows]}  # fmt: skip


@router.post("/agents/{agent_id}/runs", status_code=201)
async def start_run(
    agent_id: str, body: Task, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    try:
        return await request.app.state.agents.run(agent_id, body.task, body.project_ids)
    except AgentError as exc:
        raise _error(exc) from exc


@router.get("/runs")
async def list_runs(request: Request, client: Annotated[Client, Depends(require_client)], limit: int = 50) -> dict:
    count = max(1, min(limit, 500))
    return {"runs": await _db(request, lambda conn: agents.list_runs(conn, count))}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await _db(request, lambda conn: agents.get_run(conn, run_id))


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    await _db(request, lambda conn: agents.get_run(conn, run_id))
    return {"cancelled": await request.app.state.agents.cancel(run_id)}
