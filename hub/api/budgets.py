"""/v1/budgets - spending limits for paid models (D12): what each allows, what it has used, whether it is reached."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import budgets
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class BudgetIn(BaseModel):
    scope: str = "total"  # "total" or "provider"
    provider: str = ""  # for scope "provider", e.g. "anthropic"
    period: str = "month"  # "day", "week" or "month"
    soft_usd: float | None = Field(default=None, ge=0)
    hard_usd: float | None = Field(default=None, ge=0)
    hard_action: str = "ask"  # "ask": the user may confirm a request over the limit; "block": never


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change budgets")


@router.get("/budgets")
async def list_budgets(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"budgets": await request.app.state.db.acall(budgets.statuses)}


@router.put("/budgets")
async def set_budget(body: BudgetIn, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Creates the budget for (scope, provider, period) or replaces its limits."""
    _app_only(client)

    def write(conn):
        budget = budgets.upsert_budget(
            conn,
            scope=body.scope,
            scope_ref=body.provider,
            period=body.period,
            soft_usd=body.soft_usd,
            hard_usd=body.hard_usd,
            hard_action=body.hard_action,
        )
        return budgets.status(conn, budget)

    try:
        saved = await request.app.state.db.acall(write)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.bus.publish("budget.changed", {"budget": saved, "by": client.name})
    return saved


@router.delete("/budgets/{budget_id}", status_code=204)
async def delete_budget(budget_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    if not await request.app.state.db.acall(lambda conn: budgets.delete_budget(conn, budget_id)):
        raise HTTPException(status_code=404, detail="no such budget")
    await request.app.state.bus.publish("budget.changed", {"deleted": budget_id, "by": client.name})
