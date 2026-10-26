"""/v1/projects - the registry, a project's tree, files and git (stage 2, D4).

Reading (tree, file, git) is open to every client with a token; adding, changing and removing projects is the
app's. File and git work runs on worker threads, so a slow `git` never holds up the hub.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import projects
from ..auth import APP_CLIENT, Client
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


class NewProject(BaseModel):
    path: str
    name: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    cloud_ok: bool | None = None


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change the project list")


async def _project(request: Request, project_id: str) -> projects.Project:
    try:
        return await request.app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _off_loop(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects")
async def list_projects(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    found = await request.app.state.db.acall(projects.list_projects)
    return {"projects": [p.to_json() for p in found]}


@router.post("/projects", status_code=201)
async def add_project(body: NewProject, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        project = await request.app.state.db.acall(lambda conn: projects.add_project(conn, body.path, body.name))
    except ProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.bus.publish("project.added", {"project": project.to_json()}, project_id=project.id)
    return project.to_json()


@router.patch("/projects/{project_id}")
async def change_project(
    project_id: str, body: ProjectPatch, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    await _project(request, project_id)
    project = await request.app.state.db.acall(
        lambda conn: projects.update_project(
            conn, project_id, name=body.name, description=body.description, cloud_ok=body.cloud_ok
        )
    )
    await request.app.state.bus.publish("project.changed", {"project": project.to_json()}, project_id=project_id)
    return project.to_json()


@router.delete("/projects/{project_id}", status_code=204)
async def remove_project(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    """Forgets the project; its folder is not touched."""
    _app_only(client)
    if not await request.app.state.db.acall(lambda conn: projects.remove_project(conn, project_id)):
        raise HTTPException(status_code=404, detail="no such project")
    await request.app.state.bus.publish("project.removed", {"id": project_id}, project_id=project_id)


@router.get("/projects/{project_id}/tree")
async def project_tree(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str = ""
) -> dict:
    project = await _project(request, project_id)
    return {"path": path, "entries": await _off_loop(projects.tree, project.root, path)}


@router.get("/projects/{project_id}/file")
async def project_file(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str
) -> dict:
    project = await _project(request, project_id)
    return await _off_loop(projects.read_file, project.root, path)


@router.get("/projects/{project_id}/git")
async def project_git(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], log: int = 20
) -> dict:
    project = await _project(request, project_id)
    status = await _off_loop(projects.git_status, project.root)
    commits = await _off_loop(projects.git_log, project.root, max(0, min(log, 200)))
    return {**status, "log": commits}


@router.get("/projects/{project_id}/diff")
async def project_diff(
    project_id: str,
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    path: str = "",
    staged: bool = False,
) -> dict:
    project = await _project(request, project_id)
    return await _off_loop(projects.git_diff, project.root, path, staged=staged)
