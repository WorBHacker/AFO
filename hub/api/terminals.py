"""/v1/terminals - terminals the hub runs (stage 2, D4, D31).

Reading the list and the output is open to every client with a token; starting, typing into, resizing, stopping and
removing terminals is the app's (a terminal runs anything; rights for other clients come with D17-D19).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import projects
from ..auth import APP_CLIENT, Client
from ..projects import ProjectError
from ..terminals import TerminalError
from .deps import require_client

router = APIRouter(prefix="/v1")


class NewTerminal(BaseModel):
    project_id: str | None = None
    path: str = ""  # a folder inside the project; the project's root by default
    cwd: str | None = None  # a folder anywhere, for a terminal outside projects
    command: str | None = None  # run this and end with its exit code; an interactive shell without it
    shell: str = ""  # "pwsh", "powershell" or "cmd"; the first found otherwise
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=30, ge=5, le=200)
    title: str = ""


class Input(BaseModel):
    data: str


class Size(BaseModel):
    cols: int = Field(ge=20, le=500)
    rows: int = Field(ge=5, le=200)


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may run terminals")


def _error(exc: TerminalError) -> HTTPException:
    text = str(exc)
    status = 404 if text.startswith("no such terminal") else 409 if "running" in text else 400
    return HTTPException(status_code=status, detail=text)


@router.get("/terminals")
async def list_terminals(
    request: Request, client: Annotated[Client, Depends(require_client)], project_id: str | None = None, limit: int = 50
) -> dict:
    return {"terminals": await request.app.state.terminals.list(project_id, max(1, min(limit, 500)))}


@router.post("/terminals", status_code=201)
async def start_terminal(
    body: NewTerminal, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    if body.project_id:
        try:
            project = await request.app.state.db.acall(lambda conn: projects.get_project(conn, body.project_id))
            cwd = projects.resolve_inside(project.root, body.path)
        except ProjectError as exc:
            raise HTTPException(status_code=404 if "no such project" in str(exc) else 400, detail=str(exc)) from exc
    else:
        cwd = Path(body.cwd).expanduser() if body.cwd else Path.home()
    try:
        return await request.app.state.terminals.create(
            cwd=cwd,
            by=client.name,
            project_id=body.project_id,
            command=body.command,
            shell=body.shell,
            cols=body.cols,
            rows=body.rows,
            title=body.title,
        )
    except TerminalError as exc:
        raise _error(exc) from exc


@router.get("/terminals/{terminal_id}")
async def get_terminal(terminal_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    try:
        return await request.app.state.terminals.get(terminal_id)
    except TerminalError as exc:
        raise _error(exc) from exc


@router.get("/terminals/{terminal_id}/output")
async def terminal_output(
    terminal_id: str,
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    max_chars: int = 65536,
    since: int | None = None,
) -> dict:
    """The last output. While the terminal runs, `start` and `end` are the offsets of its first character and right
    after it: `terminal.output` events with a smaller offset are already in `data`. `since`: the output from that
    offset on, when the hub still has it (then `start` equals it) - what a client missed after a break."""
    try:
        limit = max(1, min(max_chars, 1024 * 1024))
        return await request.app.state.terminals.output(terminal_id, limit, since)
    except TerminalError as exc:
        raise _error(exc) from exc


@router.post("/terminals/{terminal_id}/input", status_code=204)
async def terminal_input(
    terminal_id: str, body: Input, request: Request, client: Annotated[Client, Depends(require_client)]
) -> None:
    _app_only(client)
    try:
        await request.app.state.terminals.write(terminal_id, body.data)
    except TerminalError as exc:
        raise _error(exc) from exc


@router.post("/terminals/{terminal_id}/resize", status_code=204)
async def terminal_resize(
    terminal_id: str, body: Size, request: Request, client: Annotated[Client, Depends(require_client)]
) -> None:
    _app_only(client)
    try:
        await request.app.state.terminals.resize(terminal_id, body.cols, body.rows)
    except TerminalError as exc:
        raise _error(exc) from exc


@router.post("/terminals/{terminal_id}/stop", status_code=204)
async def stop_terminal(terminal_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    try:
        await request.app.state.terminals.kill(terminal_id)
    except TerminalError as exc:
        raise _error(exc) from exc


@router.delete("/terminals/{terminal_id}", status_code=204)
async def remove_terminal(
    terminal_id: str, request: Request, client: Annotated[Client, Depends(require_client)]
) -> None:
    _app_only(client)
    try:
        await request.app.state.terminals.remove(terminal_id)
    except TerminalError as exc:
        raise _error(exc) from exc
    await request.app.state.bus.publish("terminal.removed", {"terminal_id": terminal_id}, persist=False)
