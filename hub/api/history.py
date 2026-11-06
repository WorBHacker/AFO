"""/v1/.../history - a project's files version after version (stage 26, hub/history.py): what the hub kept of each
change it saw, to compare and to take back. The window's alone: other clients read files through MCP.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import changes, history, projects
from ..auth import Client
from ..changes import WriteError
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/projects/{project_id}/history")
async def project_history(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str = "",
    limit: int = 100,
) -> dict:  # fmt: skip
    """The versions of `path`, newest first; without it - the project's files that have a history, the latest change
    first (deleted ones too)."""
    db, folder = request.app.state.db, request.app.state.settings.paths.snapshots
    if not path:
        return {"files": await db.acall(lambda conn: history.files(conn, project_id, max(1, min(limit, 500))))}
    found = await db.acall(lambda conn: history.versions(conn, folder, project_id, path, max(1, min(limit, 500))))
    return {"path": path, "versions": found}


@router.get("/history/{version_id}")
async def get_version(
    version_id: int, request: Request, client: Annotated[Client, Depends(require_client)],
    against: Literal["previous", "current"] = "previous",
) -> dict:  # fmt: skip
    """A version with its text and a diff: from the version before it (`previous`), or from the file as it is now to
    this version (`current`: what taking it back changes)."""
    state = request.app.state
    folder = state.settings.paths.snapshots

    def read(conn):
        version = history.get_version(conn, version_id)
        if version is None:
            return None, None, None
        return version, history.previous(conn, version), projects.get_project(conn, version["project_id"])

    try:
        version, before, project = await state.db.acall(read)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if version is None:
        raise HTTPException(status_code=404, detail=f"no such version: {version_id}")

    def build() -> dict:
        data = None if version["deleted"] else changes.load(folder, version["hash"])
        if against == "current":
            try:
                base = projects.resolve_inside(project.root, version["path"]).read_bytes()
            except ProjectError, OSError:
                base = None
        else:
            base = None if before is None or before["deleted"] else changes.load(folder, before["hash"])
        shown = projects.decoded(data) if data is not None else {"binary": False, "text": "", "encoding": "", "eol": ""}
        return {**version, **shown, "diff": history.diff(version["path"], base, data)}

    try:
        return await asyncio.to_thread(build)
    except WriteError as exc:  # its content went over D30's size meanwhile
        raise HTTPException(status_code=410, detail=str(exc)) from exc
