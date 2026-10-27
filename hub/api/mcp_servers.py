"""/v1/mcp/servers - the outside MCP servers whose tools the hub gives to new chats and autonomous runs (9.5, D42).
The desktop app only.

GET lists them with their state, tools and classes, and the names of their environment's keys (never a value). A
change - adding a server, its environment, turning it on or off, a tool's class, removing it - needs the connections'
password (the X-LocalChat-Unlock ticket, D13): a server is a program that runs as the user. Restarting one already
added does not.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import mcp_client as mc
from ..auth import Client
from .connections import Ticket, _app_only, _open
from .deps import require_client

router = APIRouter(prefix="/v1/mcp/servers")


class NewServer(BaseModel):
    name: str = Field(min_length=1, max_length=mc.NAME_CHARS)
    target: str = Field(min_length=1, max_length=2000)
    env: str = Field(default="", max_length=10_000)  # KEY=value lines


class ServerChange(BaseModel):
    enabled: bool | None = None
    env: str | None = Field(default=None, max_length=10_000)


class ToolClass(BaseModel):
    cls: str = Field(alias="class")


def _view(state, server: dict[str, Any]) -> dict[str, Any]:
    status = state.mcp.status.get(server["name"]) or {"state": "off" if not server["enabled"] else "stopped",
                                                      "detail": "", "tools": 0}  # fmt: skip
    return {"name": server["name"], "transport": server["transport"], "target": server["target"],
            "enabled": server["enabled"], "state": status["state"], "detail": status["detail"],
            "tools": state.mcp.listed(server["name"]), "env": sorted(state.mcp.env(server["name"]))}  # fmt: skip


async def _found(state, name: str) -> dict[str, Any]:
    server = await state.db.acall(lambda conn: mc.get_server(conn, name))
    if server is None:
        raise HTTPException(status_code=404, detail=f"no MCP server named {name!r}")
    return server


@router.get("")
async def list_servers(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    state = request.app.state
    found = await state.db.acall(mc.servers)
    return {"servers": [_view(state, s) for s in found], "classes": list(mc.CLASSES)}


@router.post("", status_code=201)
async def add_server(body: NewServer, request: Request, client: Annotated[Client, Depends(require_client)],
                     ticket: Ticket = None) -> dict:  # fmt: skip
    """Added, started, and its tools listed - or why not (it stays added: the user may fix its environment)."""
    await _open(request, client, ticket)
    state = request.app.state
    try:
        env = mc.check_env(body.env)
        server = await state.db.acall(lambda conn: mc.add_server(conn, {"name": body.name, "target": body.target}))
    except mc.McpError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if env:
        state.mcp.set_env(server["name"], env)
    await state.bus.publish("mcp.added", {"server": server["name"], "target": server["target"]})
    await state.mcp.connect(server)
    return _view(state, server)


@router.put("/{name}")
async def change_server(name: str, body: ServerChange, request: Request,
                        client: Annotated[Client, Depends(require_client)], ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    state = request.app.state
    server = await _found(state, name)
    if body.env is not None:
        try:
            state.mcp.set_env(name, mc.check_env(body.env))
        except mc.McpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.enabled is not None:
        server = await state.db.acall(lambda conn: mc.set_enabled(conn, name, body.enabled))
    if server["enabled"]:
        await state.mcp.connect(server)  # a new environment needs a new start
    else:
        await state.mcp.disconnect(name)
    return _view(state, server)


@router.post("/{name}/restart")
async def restart_server(name: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    state = request.app.state
    server = await _found(state, name)
    if not server["enabled"]:
        raise HTTPException(status_code=409, detail="the server is off: turn it on first")
    await state.mcp.connect(server)
    return _view(state, server)


@router.put("/{name}/tools/{tool}")
async def set_tool_class(name: str, tool: str, body: ToolClass, request: Request,
                         client: Annotated[Client, Depends(require_client)],
                         ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    state = request.app.state
    server = await _found(state, name)
    if body.cls not in mc.CLASSES:
        raise HTTPException(status_code=422, detail=f"a class: {', '.join(mc.CLASSES)}")
    await state.mcp.set_class(name, tool, body.cls)
    return _view(state, server)


@router.delete("/{name}")
async def remove_server(name: str, request: Request, client: Annotated[Client, Depends(require_client)],
                        ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    state = request.app.state
    await _found(state, name)
    await state.mcp.disconnect(name)
    await state.db.acall(lambda conn: mc.remove_server(conn, name))
    state.mcp.set_env(name, {})
    await state.bus.publish("mcp.removed", {"server": name})
    return {"removed": name}
