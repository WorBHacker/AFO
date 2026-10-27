"""Which model for this chat or task (stage 6, D11), and what a task may cost before it runs (D12).

The hub ranks the models it has by what matters to the user - quality, speed and price, weighed by their priority -
from what it saw them do here (hub/stats.py): speed, errors, how agent tasks ended, money per answer; a local model
already in video memory answers without the minute-long swap. What rules a model out is said, not hidden: a chat
holding a project only local models may see (D10), a budget used up with no way past it (D12), Claude Code without
a project. Nothing is switched silently (D11): the window shows the advice, the user picks.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import budgets, stats, store
from .claude_code import PROVIDER as CLAUDE_CODE

PRIORITIES = {  # weights of quality, speed, price
    "balanced": (0.4, 0.3, 0.3),
    "quality": (0.7, 0.15, 0.15),
    "speed": (0.2, 0.6, 0.2),
    "price": (0.2, 0.2, 0.6),
}
TYPICAL = {"input_tokens": 4000, "output_tokens": 600}  # an answer's tokens when the hub has not seen the model yet
QUALITY = (("opus", 0.95), ("fable", 0.95), ("sonnet", 0.8), ("haiku", 0.55))  # a prior by family, before stats
LOCAL_QUALITY = 0.55
DEFAULT_STEPS = 6


def _prior(model: dict[str, Any]) -> float:
    name = f"{model['id']} {model['name']}".lower()
    return next((q for word, q in QUALITY if word in name), LOCAL_QUALITY if model["locality"] == "local" else 0.7)


def answer_cost(model: dict[str, Any], seen: dict[str, Any] | None) -> float:
    """Money per answer: what it cost here on average, else its prices at a typical answer's tokens."""
    if seen and seen["requests"] and seen["cost_usd"]:
        return seen["cost_usd"] / seen["requests"]
    prices = model.get("prices") or {}
    million = 1_000_000
    return (
        TYPICAL["input_tokens"] * prices.get("input", 0) + TYPICAL["output_tokens"] * prices.get("output", 0)
    ) / million


def advise(
    conn: sqlite3.Connection,
    *,
    conversation_id: str | None = None,
    project_ids: list[str] | None = None,
    priority: str = "balanced",
    loaded: set[str] | None = None,
) -> dict[str, Any]:
    """{recommended, priority, options: [{model_id, name, score, reasons, blocked, estimate_usd}]} - best first,
    the blocked ones last with why."""
    weights = PRIORITIES.get(priority, PRIORITIES["balanced"])
    seen = {m["model_id"]: m for m in stats.model_stats(conn)}
    projects: list[dict[str, Any]] = []
    if conversation_id or project_ids:  # a new chat has its projects before it exists
        ids = store.conversation_projects(conn, conversation_id) if conversation_id else project_ids or []
        for pid in ids:
            row = conn.execute("SELECT name, cloud_ok FROM projects WHERE id = ?", (pid,)).fetchone()
            if row is not None:
                projects.append(dict(row))
    local_only = [p["name"] for p in projects if not p["cloud_ok"]]
    loaded = loaded or set()
    options = []
    for model in store.list_models(conn):
        mine = seen.get(model["id"])
        reasons: list[str] = []
        blocked = None
        cloud = model["locality"] == "cloud"
        if cloud and local_only:
            blocked = f"«{', '.join(local_only)}» — только для локальных моделей (D10)"
        elif model["provider"] == CLAUDE_CODE and not projects:
            blocked = "Claude Code работает в папке проекта: прикрепите проект"
        elif model["prices"]:
            over = budgets.reached(conn, model["id"])
            if over and not all(b["hard_action"] == "ask" for b in over):
                blocked = "бюджет исчерпан"
            elif over:
                reasons.append("бюджет исчерпан — только с подтверждением")
        quality = _prior(model)
        if mine and mine.get("runs"):
            quality = 0.5 * quality + 0.5 * (mine["run_success"] or 0)
            reasons.append(f"задачи агентов: {mine['runs_done']} из {mine['runs']}")
        if mine and mine["error_rate"]:
            quality *= 1 - min(mine["error_rate"], 0.9)
            reasons.append(f"ошибки {mine['error_rate'] * 100:.0f}%")
        speed = min((mine or {}).get("tok_s") or (80 if cloud else 60), 120) / 120
        ref = model["model_ref"]
        if not cloud:
            if ref in loaded:
                speed = min(speed + 0.3, 1.0)
                reasons.insert(0, "уже в видеопамяти")
            else:
                speed *= 0.7  # a swap first
                reasons.insert(0, "загрузится в видеопамять (до пары минут)")
        if mine and mine.get("tok_s"):
            reasons.append(f"{mine['tok_s']:g} т/с здесь")
        cost = answer_cost(model, mine)
        subscription = (model.get("capabilities") or {}).get("subscription")
        if subscription:
            price = 0.9
            reasons.append("платит подписка Claude Code")
        elif cost:
            price = 1 / (1 + cost * 40)
            reasons.append(f"≈ ${cost:.4f} за ответ" + (" по прошлым ответам" if mine and mine["cost_usd"] else ""))
        else:
            price = 1.0
            reasons.append("бесплатно")
        score = round(weights[0] * quality + weights[1] * speed + weights[2] * price, 3)
        options.append({"model_id": model["id"], "name": model["name"], "score": score, "reasons": reasons,
                        "blocked": blocked, "estimate_usd": round(cost, 6)})  # fmt: skip
    options.sort(key=lambda o: (o["blocked"] is not None, -o["score"], o["name"]))
    best = next((o["model_id"] for o in options if o["blocked"] is None), None)
    return {"recommended": best, "priority": priority if priority in PRIORITIES else "balanced", "options": options}


def run_estimate(conn: sqlite3.Connection, profile: dict[str, Any]) -> dict[str, Any]:
    """What a task of this agent may cost, before it runs (D12): its past tasks, else its model's answers."""
    rows = conn.execute(
        "SELECT cost_usd, steps FROM agent_runs WHERE profile_id = ? AND status = 'done'", (profile["id"],)
    ).fetchall()
    if rows:
        cost = sum(r["cost_usd"] for r in rows) / len(rows)
        return {"estimate_usd": round(cost, 6), "basis": f"по {len(rows)} прошлым задачам этого агента"}
    model = store.get_model(conn, profile["model_id"])
    if model is None:
        return {"estimate_usd": None, "basis": "модель недоступна"}
    seen = {m["model_id"]: m for m in stats.model_stats(conn)}.get(model["id"])
    steps = (seen or {}).get("run_steps") or DEFAULT_STEPS
    cost = answer_cost(model, seen) * steps
    return {"estimate_usd": round(cost, 6), "basis": f"≈ {steps:g} шагов по цене ответа модели"}
