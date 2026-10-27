"""What the hub has learnt about its models and its spending (stage 6, D11, D12).

Per model, from what it did here: how many requests, how fast (tokens per second, time to the first token, a whole
answer - medians, so one slow model swap does not skew it), how often it ended in an error, how many tokens and how
much money, and as an agent how its tasks ended (done / error / cancelled) with their steps and cost. For the money:
this month so far, the last week's daily average, and what the month comes to at that rate; Claude Code's own
estimate is counted apart - the subscription pays for it (D12). This is the ground for recommendations (D11).
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

TOOL_STOPS = {"tool_calls", "tool_use"}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def model_stats(conn: sqlite3.Connection, *, days: int = 30, now: datetime | None = None) -> list[dict[str, Any]]:
    since = _iso((now or datetime.now(UTC)) - timedelta(days=days))
    found: dict[str, dict[str, Any]] = {}

    def entry(model_id: str) -> dict[str, Any]:
        return found.setdefault(model_id, {
            "model_id": model_id, "requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "estimate_usd": 0.0, "tool_steps": 0, "cancelled": 0, "_tok_s": [], "_ttft": [], "_total": [],
            "runs": 0, "runs_done": 0, "runs_error": 0, "runs_cancelled": 0, "_steps": [], "_run_cost": [],
        })  # fmt: skip

    for row in conn.execute(
        "SELECT model_id, input_tokens, output_tokens, cost_usd, meta FROM usage WHERE ts >= ?", (since,)
    ):
        e = entry(row["model_id"])
        meta = json.loads(row["meta"] or "{}")
        stats = meta.get("stats") or {}
        e["requests"] += 1
        e["input_tokens"] += row["input_tokens"]
        e["output_tokens"] += row["output_tokens"]
        e["cost_usd"] += row["cost_usd"]
        e["estimate_usd"] += float(stats.get("cost_estimate_usd") or 0)  # Claude Code's own, for scale
        e["tool_steps"] += meta.get("stop_reason") in TOOL_STOPS
        e["cancelled"] += bool(meta.get("cancelled"))
        for key, name in (("tok_s", "_tok_s"), ("ttft_s", "_ttft"), ("total_s", "_total")):
            if stats.get(key):
                e[name].append(float(stats[key]))
    for row in conn.execute("SELECT payload FROM events WHERE type = 'chat.error' AND ts >= ?", (since,)):
        model_id = json.loads(row["payload"]).get("model_id")
        if model_id:
            entry(model_id)["errors"] += 1
    for row in conn.execute(
        "SELECT model_id, status, steps, cost_usd FROM agent_runs WHERE created_at >= ? AND status IN"
        " ('done', 'error', 'cancelled')",
        (since,),
    ):
        e = entry(row["model_id"])
        e["runs"] += 1
        e[f"runs_{row['status']}"] += 1
        e["_steps"].append(row["steps"])
        e["_run_cost"].append(row["cost_usd"])

    out = []
    for e in found.values():
        attempts = e["requests"] + e["errors"]
        out.append({
            **{k: v for k, v in e.items() if not k.startswith("_")},
            "cost_usd": round(e["cost_usd"], 6),
            "estimate_usd": round(e["estimate_usd"], 6),
            "tok_s": _median(e["_tok_s"]),
            "ttft_s": _median(e["_ttft"]),
            "answer_s": _median(e["_total"]),
            "error_rate": round(e["errors"] / attempts, 3) if attempts else 0.0,
            "run_success": round(e["runs_done"] / e["runs"], 3) if e["runs"] else None,
            "run_steps": _median([float(s) for s in e["_steps"]]),
            "run_cost_usd": round(statistics.mean(e["_run_cost"]), 6) if e["_run_cost"] else None,
        })  # fmt: skip
    return sorted(out, key=lambda m: (-m["requests"], m["model_id"]))


def spending(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """This month so far, the last 7 days' daily average, and the month at that rate (D12)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    days_in_month = calendar.monthrange(now.year, now.month)[1]

    def total(since: datetime) -> float:
        row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ts >= ?", (_iso(since),)).fetchone()
        return float(row[0])

    month, week = total(month_start), total(week_ago)
    elapsed = (now - month_start).total_seconds() / 86400
    left = max(days_in_month - elapsed, 0.0)
    daily = week / 7
    by_model = {
        r["model_id"]: round(r["spent"], 6)
        for r in conn.execute(
            "SELECT model_id, SUM(cost_usd) AS spent FROM usage WHERE ts >= ? GROUP BY model_id HAVING spent > 0"
            " ORDER BY spent DESC",
            (_iso(month_start),),
        )
    }
    estimate = sum(
        float((json.loads(r["meta"] or "{}").get("stats") or {}).get("cost_estimate_usd") or 0)
        for r in conn.execute("SELECT meta FROM usage WHERE ts >= ? AND estimated = 1", (_iso(month_start),))
    )
    return {
        "month_to_date_usd": round(month, 6),
        "daily_avg_7d_usd": round(daily, 6),
        "forecast_month_usd": round(month + daily * left, 4),
        "days_left": round(left, 1),
        "by_model_usd": by_model,
        "subscription_estimate_usd": round(estimate, 4),  # Claude Code: the subscription pays (D12)
    }
