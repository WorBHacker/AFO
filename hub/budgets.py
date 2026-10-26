"""Budgets (D12, the base of stage 1): how much the paid models may cost per day, week or month.

A budget covers all paid models ("total") or one provider ("provider", e.g. anthropic). Reaching its soft limit
publishes a warning; once its hard limit is reached, a new request to a paid model is refused - or, with the
"ask" action, sent only after the user confirms (and that override is logged). Local models cost nothing and
are never held back. Periods follow the local calendar: the day starts at midnight, the week on Monday.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import utc_now

SCOPES = ("total", "provider")
PERIODS = ("day", "week", "month")
ACTIONS = ("ask", "block")


@dataclass(frozen=True)
class Budget:
    id: int
    scope: str
    scope_ref: str
    period: str
    soft_usd: float | None
    hard_usd: float | None
    hard_action: str

    def applies_to(self, model_id: str) -> bool:
        return self.scope == "total" or model_id.startswith(f"{self.scope_ref}/")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def period_start(period: str, now: datetime | None = None) -> str:
    """When the current period began, as the UTC text the usage table stores. `now` without a time zone (or
    None) is local time; with one, its own zone decides where days begin."""
    local = now if now is not None and now.tzinfo is not None else (now or datetime.now()).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start -= timedelta(days=start.weekday())
    elif period == "month":
        start = start.replace(day=1)
    elif period != "day":
        raise ValueError(f"unknown period {period!r}")
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _row(row: sqlite3.Row) -> Budget:
    return Budget(
        row["id"], row["scope"], row["scope_ref"], row["period"], row["soft_usd"], row["hard_usd"], row["hard_action"]
    )


def list_budgets(conn: sqlite3.Connection) -> list[Budget]:
    rows = conn.execute("SELECT * FROM budgets ORDER BY scope DESC, scope_ref, period").fetchall()
    return [_row(r) for r in rows]


def upsert_budget(
    conn: sqlite3.Connection,
    *,
    scope: str,
    scope_ref: str = "",
    period: str,
    soft_usd: float | None,
    hard_usd: float | None,
    hard_action: str = "ask",
) -> Budget:
    """Creates the budget for (scope, provider, period) or replaces its limits. ValueError says what is wrong."""
    scope_ref = scope_ref.strip() if scope == "provider" else ""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if hard_action not in ACTIONS:
        raise ValueError(f"hard_action must be one of {ACTIONS}")
    if scope == "provider" and not scope_ref:
        raise ValueError("a provider budget needs the provider's name")
    if soft_usd is None and hard_usd is None:
        raise ValueError("set a soft limit, a hard limit, or both")
    if any(v is not None and v < 0 for v in (soft_usd, hard_usd)):
        raise ValueError("limits cannot be negative")
    if soft_usd is not None and hard_usd is not None and soft_usd > hard_usd:
        raise ValueError("the soft limit (warning) must not be above the hard limit")
    conn.execute(
        """
        INSERT INTO budgets (scope, scope_ref, period, soft_usd, hard_usd, hard_action)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (scope, scope_ref, period) DO UPDATE SET
            soft_usd = excluded.soft_usd, hard_usd = excluded.hard_usd,
            hard_action = excluded.hard_action, updated_at = ?
        """,
        (scope, scope_ref, period, soft_usd, hard_usd, hard_action, utc_now()),
    )
    row = conn.execute(
        "SELECT * FROM budgets WHERE scope = ? AND scope_ref = ? AND period = ?", (scope, scope_ref, period)
    ).fetchone()
    return _row(row)


def delete_budget(conn: sqlite3.Connection, budget_id: int) -> bool:
    return bool(conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,)).rowcount)


def spent(conn: sqlite3.Connection, budget: Budget, now: datetime | None = None) -> float:
    """USD spent in the budget's current period, on the models it covers."""
    prefix = f"{budget.scope_ref}/" if budget.scope == "provider" else ""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ts >= ? AND substr(model_id, 1, ?) = ?",
        (period_start(budget.period, now), len(prefix), prefix),
    ).fetchone()
    return round(row[0], 6)


def status(conn: sqlite3.Connection, budget: Budget, now: datetime | None = None) -> dict[str, Any]:
    """The budget with what it has used: state is 'ok', 'soft' (warning reached) or 'hard' (limit reached)."""
    used = spent(conn, budget, now)
    if budget.hard_usd is not None and used >= budget.hard_usd:
        state = "hard"
    elif budget.soft_usd is not None and used >= budget.soft_usd:
        state = "soft"
    else:
        state = "ok"
    return {**budget.to_json(), "spent_usd": used, "since": period_start(budget.period, now), "state": state}


def statuses(conn: sqlite3.Connection, model_id: str | None = None, now: datetime | None = None) -> list[dict]:
    """Every budget's status, or only those that cover `model_id`."""
    return [status(conn, b, now) for b in list_budgets(conn) if model_id is None or b.applies_to(model_id)]


def reached(conn: sqlite3.Connection, model_id: str, now: datetime | None = None) -> list[dict]:
    """The budgets covering this model whose hard limit is reached: a new request must not go out unasked."""
    return [s for s in statuses(conn, model_id, now) if s["state"] == "hard"]


def crossed(conn: sqlite3.Connection, model_id: str, cost_usd: float) -> list[tuple[str, dict]]:
    """After a request of `cost_usd` was recorded: which limits it pushed spending over.
    'warning' - the soft limit, 'exceeded' - the hard one."""
    if cost_usd <= 0:
        return []
    events = []
    for current in statuses(conn, model_id):
        before = current["spent_usd"] - cost_usd
        if current["hard_usd"] is not None and before < current["hard_usd"] <= current["spent_usd"]:
            events.append(("exceeded", current))
        elif current["soft_usd"] is not None and before < current["soft_usd"] <= current["spent_usd"]:
            events.append(("warning", current))
    return events
