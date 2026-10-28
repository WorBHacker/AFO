"""/v1/links - links between projects (stage 3, D25): proposed from evidence, confirmed by the user.

Reading is open to every client; scanning, confirming, rejecting, editing and deleting are the app's.
`links.changed` (live) after any change; `link.confirmed` goes into the event log.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import links, projects
from ..auth import APP_CLIENT, Client
from ..links import LinkError
from .deps import require_client

router = APIRouter(prefix="/v1")


class NewLink(BaseModel):
    a_id: str
    b_id: str
    kind: str = "uses"
    description: str = ""


class LinkPatch(BaseModel):
    status: str | None = None
    kind: str | None = None
    description: str | None = None


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change links between projects")


async def _all(request: Request) -> list[dict]:
    def read(conn):
        names = {p.id: p.name for p in projects.list_projects(conn)}
        return [{**link, "a_name": names.get(link["a_id"], "?"), "b_name": names.get(link["b_id"], "?")}
                for link in links.list_links(conn)]  # fmt: skip

    return await request.app.state.db.acall(read)


async def _changed(request: Request) -> None:
    await request.app.state.bus.publish("links.changed", {}, persist=False)


@router.get("/links")
async def list_links(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"links": await _all(request)}


@router.post("/links/scan")
async def scan_links(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Looks for evidence between every pair of projects; new proposals join the list."""
    _app_only(client)
    added = await links.look(request.app.state.db, request.app.state.processes)
    await _changed(request)
    return {"added": added, "links": await _all(request)}


@router.post("/links", status_code=201)
async def add_link(body: NewLink, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        link = await request.app.state.db.acall(
            lambda conn: links.add_link(conn, body.a_id, body.b_id, body.kind, body.description)
        )
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _confirmed(request, link)
    return link


@router.put("/links/{link_id}")
async def change_link(
    link_id: int, body: LinkPatch, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    try:
        before = await request.app.state.db.acall(
            lambda conn: conn.execute("SELECT status FROM project_links WHERE id = ?", (link_id,)).fetchone()
        )
        link = await request.app.state.db.acall(
            lambda conn: links.update_link(
                conn, link_id, status=body.status, kind=body.kind, description=body.description
            )
        )
    except LinkError as exc:
        raise HTTPException(status_code=404 if "no such" in str(exc) else 422, detail=str(exc)) from exc
    if link["status"] == "confirmed" and (before is None or before["status"] != "confirmed"):
        await _confirmed(request, link)
    else:
        await _changed(request)
    return link


async def _confirmed(request: Request, link: dict) -> None:
    names = {p.id: p.name for p in await request.app.state.db.acall(projects.list_projects)}
    await request.app.state.bus.publish(
        "link.confirmed",
        {"a_name": names.get(link["a_id"], "?"), "b_name": names.get(link["b_id"], "?"), "kind": link["kind"],
         "description": link["description"]},
        project_id=link["a_id"],
    )  # fmt: skip
    await _changed(request)


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(link_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    if not await request.app.state.db.acall(lambda conn: links.delete_link(conn, link_id)):
        raise HTTPException(status_code=404, detail="no such link")
    await _changed(request)
