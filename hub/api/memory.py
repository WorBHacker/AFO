"""/v1/memory and /v1/skills - the autonomous agent's memory as the user sees and corrects it (stage 9.3, D40, Q24):
the facts kept for good, a run's notes, and the skills. Every client may read the facts a cloud model may read; the
whole list - personal facts too - and every change are the desktop app's (the user's own memory)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import memory
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


def _app(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app changes the agent's memory")


async def _db(request: Request, fn) -> Any:
    try:
        return await request.app.state.db.acall(fn)
    except memory.MemoryError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/memory/facts")
async def facts(request: Request, client: Annotated[Client, Depends(require_client)], q: str = "") -> dict:
    cloud = client.name != APP_CLIENT  # an outside client is treated as cloud (D32): what a cloud model may read
    return {"facts": await _db(request, lambda conn: memory.facts(conn, q, cloud=cloud))}


class NewFact(BaseModel):
    text: str = Field(min_length=1, max_length=memory.FACT_CHARS)
    about: str = ""
    personal: bool = False
    cloud_ok: bool = False


@router.post("/memory/facts", status_code=201)
async def add_fact(body: NewFact, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """A fact the user writes in themselves."""
    _app(client)
    fact = await _db(request, lambda conn: memory.add_fact(conn, body.text, about=body.about, source="user",
                                                           personal=body.personal, cloud_ok=body.cloud_ok))  # fmt: skip
    await request.app.state.bus.publish("memory.changed", {"fact_id": fact["id"]}, persist=False)
    return fact


class FactChange(BaseModel):
    text: str | None = None
    about: str | None = None
    personal: bool | None = None
    cloud_ok: bool | None = None


@router.put("/memory/facts/{fact_id}")
async def change_fact(fact_id: int, body: FactChange, request: Request,
                      client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    _app(client)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    fact = await _db(request, lambda conn: memory.change_fact(conn, fact_id, **changes))
    if fact is None:
        raise HTTPException(status_code=404, detail="no such fact")
    await request.app.state.bus.publish("memory.changed", {"fact_id": fact_id}, persist=False)
    return fact


@router.delete("/memory/facts/{fact_id}", status_code=204)
async def delete_fact(fact_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app(client)
    if not await _db(request, lambda conn: memory.delete_fact(conn, fact_id)):
        raise HTTPException(status_code=404, detail="no such fact")
    await request.app.state.bus.publish("memory.changed", {"fact_id": fact_id, "deleted": True}, persist=False)


@router.get("/runs/{run_id}/notes")
async def run_notes(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"notes": await _db(request, lambda conn: memory.notes(conn, run_id))}


@router.get("/skills")
async def skills(request: Request, client: Annotated[Client, Depends(require_client)], q: str = "") -> dict:
    return {"skills": await _db(request, lambda conn: memory.skills(conn, q))}


class NewSkill(BaseModel):
    run_id: str
    name: str = ""


@router.post("/skills", status_code=201)
async def add_skill(body: NewSkill, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """A finished task's plan kept as a skill - the user chose to."""
    _app(client)
    found = await _db(request, lambda conn: memory.add_skill(conn, body.run_id, body.name))
    await request.app.state.bus.publish("memory.changed", {"skill_id": found["id"]}, persist=False)
    return found


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app(client)
    if not await _db(request, lambda conn: memory.delete_skill(conn, skill_id)):
        raise HTTPException(status_code=404, detail="no such skill")
    await request.app.state.bus.publish("memory.changed", {"skill_id": skill_id, "deleted": True}, persist=False)
