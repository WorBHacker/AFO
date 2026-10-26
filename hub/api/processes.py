"""/v1/processes - processes and ports in project folders (stage 2, D4), and stopping one (the app only)."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import APP_CLIENT, Client
from ..processes import is_alive, stop_tree
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/processes")
async def list_processes(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """project id -> its processes (the latest scan, or a fresh one). The same arrives as `processes.changed`."""
    monitor = request.app.state.processes
    found = monitor.latest if monitor.latest is not None else await monitor.sample()
    return {"projects": found}


@router.post("/processes/{pid}/stop")
async def stop_process(pid: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Ends a process of a project and its children. Never the hub, never a process outside the projects."""
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may stop processes")
    monitor = request.app.state.processes
    await monitor.sample()  # the pid must belong to a project now, not at the last tick
    found = monitor.project_of(pid)
    if found is None:
        raise HTTPException(status_code=404, detail="no such process in any project")
    project_id, process = found
    if process["hub"]:
        raise HTTPException(status_code=409, detail="that is the hub itself; stop it with POST /v1/shutdown")
    stopped = await asyncio.to_thread(stop_tree, pid)
    still = await asyncio.to_thread(is_alive, pid)
    await request.app.state.bus.publish(
        "process.stopped",
        {"pid": pid, "name": process["name"], "cmdline": process["cmdline"], "pids": stopped, "by": client.name},
        project_id=project_id,
    )
    monitor.soon()
    return {"stopped": not still, "pids": stopped}
