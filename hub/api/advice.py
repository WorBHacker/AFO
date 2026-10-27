"""/v1/advice - which model for this chat, and what an agent's task may cost (stage 6, D11, D12). Open to every
client; the hub only advises - nothing is switched (D11)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import advisor, agents
from ..agents import AgentError
from ..auth import Client
from .deps import require_client

router = APIRouter(prefix="/v1")


def _loaded(request: Request) -> set[str]:
    """The local models in video memory now, as the status monitor last saw them."""
    latest = getattr(request.app.state.status, "latest", None) or {}
    running = (latest.get("local") or {}).get("running") or []
    return {item["model"] for item in running if item.get("state") in ("ready", "")}


@router.get("/advice")
async def advice(
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    conversation_id: str | None = None,
    project_ids: str = "",  # a new chat's projects, comma separated
    priority: str = "balanced",
) -> dict:
    loaded = _loaded(request)
    ids = [p for p in project_ids.split(",") if p]
    return await request.app.state.db.acall(
        lambda conn: advisor.advise(
            conn, conversation_id=conversation_id, project_ids=ids, priority=priority, loaded=loaded
        )
    )


@router.get("/agents/{agent_id}/estimate")
async def estimate(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    def read(conn):
        return advisor.run_estimate(conn, agents.get_profile(conn, agent_id))

    try:
        return await request.app.state.db.acall(read)
    except AgentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
