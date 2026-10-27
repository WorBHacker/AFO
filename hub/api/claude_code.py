"""/v1/claude-code - the user's Claude Code under the hub (stage 4, D8): is it installed, and run it in a terminal.

Chats with a Claude Code model run it per message (hub/claude_code.py); a terminal runs it interactively, with the
hub's MCP. Starting a terminal is the app's, as with every terminal. A project only local models may see is not
opened to Claude Code, a cloud model (D10).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import projects
from ..auth import APP_CLIENT, Client
from ..gateway import GatewayError
from ..projects import ProjectError
from ..terminals import TerminalError
from .deps import require_client

router = APIRouter(prefix="/v1")


class Size(BaseModel):
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=30, ge=5, le=200)


@router.get("/claude-code")
async def status(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    runner = request.app.state.claude_code
    try:
        command = await asyncio.to_thread(runner.cli)
    except GatewayError as exc:
        return {"installed": False, "command": None, "detail": str(exc), "running": []}
    return {"installed": True, "command": command, "detail": "", "running": sorted(runner.turns)}


@router.post("/projects/{project_id}/claude-code/terminal", status_code=201)
async def open_terminal(
    project_id: str, body: Size, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may run terminals")
    try:
        project = await request.app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not project.cloud_ok:
        raise HTTPException(
            status_code=409,
            detail=f"«{project.name}» is for local models only (D10): allow the cloud in its settings first",
        )
    try:
        argv = await asyncio.to_thread(request.app.state.claude_code.terminal_command, project)
        return await request.app.state.terminals.create(
            cwd=project.root, by=client.name, project_id=project.id, argv=argv, cols=body.cols, rows=body.rows,
            title="Claude Code",
        )  # fmt: skip
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TerminalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
