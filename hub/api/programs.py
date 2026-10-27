"""/v1/programs - the programs of this computer and what the autonomous agent may control (D43). The desktop app's
alone: an agent or an MCP client neither reads the list nor decides for itself."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import programs
from ..auth import APP_CLIENT, Client
from ..db.database import utc_now
from .deps import require_client

router = APIRouter(prefix="/v1")


def require_app(client: Annotated[Client, Depends(require_client)]) -> Client:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app decides which programs the agent may use")
    return client


class Change(BaseModel):
    decision: str | None = None  # allow / deny / null - not decided (no)
    later: bool = False  # "decide later": stop asking, still not allowed


class Added(BaseModel):
    path: str


@router.get("/programs")
async def list_all(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict[str, Any]:
    catalog = request.app.state.programs
    found, offer = await request.app.state.db.acall(lambda conn: (programs.list_programs(conn), programs.offer(conn)))
    return {
        "programs": found,
        "scanning": catalog.scanning,
        "scanned_at": catalog.scanned_at,
        "error": catalog.error,
        "offer": offer,
    }


@router.post("/programs/scan", status_code=202)
async def scan(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict[str, bool]:
    """A look now; what it finds comes as `programs.scanned` (and `programs.found` for new programs)."""
    catalog = request.app.state.programs
    if not catalog.scanning:
        asyncio.create_task(catalog.scan())  # noqa: RUF006 - the catalog's lock keeps it to one; the bus reports it
    return {"scanning": True}


@router.put("/programs/{pid}")
async def change(pid: str, body: Change, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    db, bus = request.app.state.db, request.app.state.bus
    if body.later:
        found = await db.acall(lambda conn: programs.put_off(conn, pid))
        if found is None:
            raise HTTPException(status_code=404, detail="no such program")
        return found
    if body.decision is not None and body.decision not in programs.DECISIONS:
        raise HTTPException(status_code=422, detail="decision is allow, deny or null")
    now = utc_now()
    found = await db.acall(lambda conn: programs.decide(conn, pid, body.decision, client.name, now))
    if found is None:
        raise HTTPException(status_code=404, detail="no such program")
    await bus.publish("program.decided", {"id": pid, "name": found["name"], "decision": body.decision})
    return found


@router.post("/programs", status_code=201)
async def add(body: Added, request: Request, response: Response, client: Annotated[Client, Depends(require_app)]):
    """A program the look does not see (portable, without a shortcut): an .exe the user picked."""
    path = os.path.normpath(body.path)
    if not path.lower().endswith(".exe") or not os.path.isfile(path):
        raise HTTPException(status_code=422, detail="not a program: pick an .exe file")
    name = (await asyncio.to_thread(programs.file_info, path)).get("FileDescription") or os.path.splitext(
        os.path.basename(path)
    )[0]
    now = utc_now()
    found, new = await request.app.state.db.acall(lambda conn: programs.add_manual(conn, path, name, now))
    if not new:
        response.status_code = 200
    else:
        await request.app.state.bus.publish("programs.scanned", {"added": found["id"]}, persist=False)
    return found


@router.delete("/programs/{pid}", status_code=204)
async def remove(pid: str, request: Request, client: Annotated[Client, Depends(require_app)]) -> Response:
    """Only a program added by hand; the ones the look finds are decided, not removed."""
    if not await request.app.state.db.acall(lambda conn: programs.remove_manual(conn, pid)):
        raise HTTPException(status_code=404, detail="no hand-added program with that id")
    await request.app.state.bus.publish("programs.scanned", {"removed_manual": pid}, persist=False)
    return Response(status_code=204)


@router.post("/programs/offer/dismiss", status_code=204)
async def dismiss_offer(request: Request, client: Annotated[Client, Depends(require_app)]) -> Response:
    """The list was offered after the first look: not again."""
    await request.app.state.db.acall(programs.dismiss_offer)
    return Response(status_code=204)
