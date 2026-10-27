"""/v1/schedules - autonomous goals started on a schedule or an event (stage 9.7, Q23), and /v1/agent/quiet - the quiet
hours. Only the desktop app sets them up: a schedule starts agents with nobody at the computer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import schedule as sc
from ..agents import AgentError, check_goal
from ..auth import Client
from .connections import _app_only
from .deps import require_client

router = APIRouter(prefix="/v1")


class Schedule(BaseModel):
    title: str = Field(default="", max_length=200)
    goal: dict[str, Any]
    trigger: dict[str, Any]
    computer: bool = False
    enabled: bool = True


class ScheduleChange(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    goal: dict[str, Any] | None = None
    trigger: dict[str, Any] | None = None
    computer: bool | None = None
    enabled: bool | None = None


class Quiet(BaseModel):
    enabled: bool | None = None
    start: str | None = Field(default=None, alias="from")
    end: str | None = Field(default=None, alias="to")


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


async def _checked_goal(request: Request, goal: dict[str, Any] | None) -> None:
    """The goal as an autonomous run would take it - refused now, not at three in the morning."""
    if goal is None:
        return
    try:
        await request.app.state.db.acall(lambda conn: check_goal(conn, goal))
    except AgentError as exc:
        raise _refused(exc) from exc


@router.get("/schedules")
async def list_schedules(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    db = request.app.state.db
    return {"schedules": await db.acall(sc.schedules), "quiet": await db.acall(sc.quiet_hours)}


@router.post("/schedules", status_code=201)
async def add_schedule(body: Schedule, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    await _checked_goal(request, body.goal)
    try:
        found = await request.app.state.db.acall(lambda conn: sc.add_schedule(conn, body.model_dump(), datetime.now()))
    except sc.ScheduleError as exc:
        raise _refused(exc) from exc
    await request.app.state.bus.publish("schedule.added", {"schedule": found})
    return found


@router.put("/schedules/{sid}")
async def change_schedule(sid: str, body: ScheduleChange, request: Request,
                          client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    _app_only(client)
    await _checked_goal(request, body.goal)
    try:
        found = await request.app.state.db.acall(lambda conn: sc.change_schedule(conn, sid, body.model_dump(),
                                                                                 datetime.now()))  # fmt: skip
    except sc.ScheduleError as exc:
        raise _refused(exc) from exc
    if found is None:
        raise HTTPException(status_code=404, detail="no such schedule")
    await request.app.state.bus.publish("schedule.changed", {"schedule": found})
    return found


@router.delete("/schedules/{sid}")
async def delete_schedule(sid: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    if not await request.app.state.db.acall(lambda conn: sc.delete_schedule(conn, sid)):
        raise HTTPException(status_code=404, detail="no such schedule")
    await request.app.state.bus.publish("schedule.removed", {"id": sid})
    return {"removed": sid}


@router.post("/schedules/{sid}/run")
async def run_schedule(sid: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Now, not waiting for its time or the morning."""
    _app_only(client)
    run_id = await request.app.state.scheduler.run_now(sid)
    if run_id is None:
        raise HTTPException(status_code=409, detail="not started: no such schedule, or its last run still goes")
    return {"run_id": run_id}


@router.get("/agent/quiet")
async def get_quiet(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    return await request.app.state.db.acall(sc.quiet_hours)


@router.put("/agent/quiet")
async def set_quiet(body: Quiet, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    change = {k: v for k, v in {"enabled": body.enabled, "from": body.start, "to": body.end}.items() if v is not None}
    try:
        quiet = await request.app.state.db.acall(lambda conn: sc.set_quiet_hours(conn, change))
    except sc.ScheduleError as exc:
        raise _refused(exc) from exc
    await request.app.state.bus.publish("schedule.quiet", {"quiet": quiet})
    return quiet
