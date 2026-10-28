"""What the user set for each model in the window (D39, stage 8): shown in the picker or not, its own name, what it
can do (context, tools, vision - when the provider says it wrong), its prices with their date (Q9), the answer's
parameters with a preset, the default reasoning depth and a system prompt. The row of ALL ('') holds the general
system prompt, the one for every model.

Every save is a new version and the old ones stay: an answer remembers the version and the parameters it was made
with (D14), and an earlier version can be taken back. What the provider says about a model is never overwritten -
the settings lie over it (`overlay`), field by field, only where the user said something.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from .db import utc_now
from .limits import LIMITS

ALL = ""  # the general row: the system prompt for every model

# the answer's parameters: name -> (type, lowest, highest); "stop" and "thinking" are checked on their own
NUMBERS: dict[str, tuple[type, float, float]] = {
    "temperature": (float, 0.0, 2.0),
    "top_p": (float, 0.0, 1.0),
    "top_k": (int, 1, 1000),
    "min_p": (float, 0.0, 1.0),
    "repeat_penalty": (float, 0.5, 2.0),
    "max_tokens": (int, 1, 1_000_000),
}
# "refusal_vector": a Strata model's refusal vector (abliteration at runtime) on or off - only where the model has one
PARAMS = (*NUMBERS, "stop", "thinking", "refusal_vector")
STOP_MAX = 4  # stop sequences: the fewest any provider takes (OpenAI-compatible APIs)

# "Точно / Баланс / Творчески": each fills only what the model's provider takes
PRESETS: dict[str, dict[str, float]] = {
    "precise": {"temperature": 0.2, "top_p": 0.9, "top_k": 20, "min_p": 0.05, "repeat_penalty": 1.05},
    "balanced": {"temperature": 0.7, "top_p": 0.95, "top_k": 40, "min_p": 0.05, "repeat_penalty": 1.05},
    "creative": {"temperature": 1.0, "top_p": 0.98, "top_k": 100, "min_p": 0.02, "repeat_penalty": 1.0},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CAPABILITIES = {"context": int, "tools": bool, "vision": bool}
# which model does what (D39, the general row): a new chat, project cards (local, D24), agents and the coordinator,
# the autonomous agent's planner / executor / eyes (stage 9), the judge of goals (D44, 11.2), and the spare one when a
# model fails, refuses or its budget is spent
ROLES = ("chat", "cards", "agents", "coordinator", "planner", "executor", "vision", "judge", "fallback")
PRICES = ("input", "output", "cache_read", "cache_write")  # USD per 1M tokens
SYSTEM_PROMPT_MAX = 20_000


class SettingsError(ValueError):
    """A value the hub does not take; the message says which and why, in words for the window."""


def _number(name: str, value: Any, kind: type, low: float, high: float) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SettingsError(f"{name}: нужно число")
    if kind is int and value != int(value):
        raise SettingsError(f"{name}: нужно целое число")
    if not low <= value <= high:
        raise SettingsError(f"{name}: от {low:g} до {high:g}")
    return int(value) if kind is int else float(value)


def check_params(params: Any) -> dict[str, Any]:
    """The answer's parameters as the hub keeps them; anything wrong is refused, never quietly dropped."""
    if not isinstance(params, dict):
        raise SettingsError("параметры ответа: нужен словарь")
    out: dict[str, Any] = {}
    for name, value in params.items():
        if value is None:
            continue  # not set: the provider's own default
        if name in NUMBERS:
            out[name] = _number(name, value, *NUMBERS[name])
        elif name == "stop":
            if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
                raise SettingsError("стоп-последовательности: список непустых строк")
            if len(value) > STOP_MAX:
                raise SettingsError(f"стоп-последовательности: не больше {STOP_MAX}")
            if value:
                out[name] = list(value)
        elif name == "thinking":
            if not isinstance(value, bool):
                raise SettingsError("размышления: да или нет")
            out[name] = value
        elif name == "refusal_vector":
            if not isinstance(value, bool):
                raise SettingsError("снятие отказов: да или нет")
            out[name] = value
        else:
            raise SettingsError(f"неизвестный параметр «{name}»")
    return out


def check(settings: Any) -> dict[str, Any]:
    """A whole settings record as it is saved."""
    if not isinstance(settings, dict):
        raise SettingsError("настройки: нужен словарь")
    out: dict[str, Any] = {}
    for key, value in settings.items():
        if value is None:
            continue
        match key:
            case "hidden":
                if not isinstance(value, bool):
                    raise SettingsError("показывать в выборе: да или нет")
                if value:
                    out[key] = True
            case "name":
                name = " ".join(str(value).split())[:80]
                if name:
                    out[key] = name
            case "capabilities":
                if not isinstance(value, dict):
                    raise SettingsError("возможности: нужен словарь")
                caps: dict[str, Any] = {}
                for cap, cap_value in value.items():
                    if cap_value is None:
                        continue
                    if cap not in CAPABILITIES:
                        raise SettingsError(f"неизвестная возможность «{cap}»")
                    if CAPABILITIES[cap] is bool and not isinstance(cap_value, bool):
                        raise SettingsError(f"{cap}: да или нет")
                    if CAPABILITIES[cap] is int:
                        cap_value = int(_number("контекст", cap_value, int, 512, 10_000_000))
                    caps[cap] = cap_value
                if caps:
                    out[key] = caps
            case "prices":
                if not isinstance(value, dict):
                    raise SettingsError("цены: нужен словарь")
                prices = {}
                for part, price in value.items():
                    if part not in PRICES:
                        raise SettingsError(f"неизвестная цена «{part}»")
                    if price is not None:
                        prices[part] = float(_number(f"цена {part}", price, float, 0.0, 10_000.0))
                if prices:
                    out[key] = prices
            case "prices_asof":
                out[key] = str(value)[:10]
            case "params":
                params = check_params(value)
                if params:
                    out[key] = params
            case "preset":
                if value not in PRESETS:
                    raise SettingsError(f"неизвестная заготовка «{value}»")
                out[key] = value
            case "effort":
                if value not in EFFORTS:
                    raise SettingsError(f"неизвестная глубина размышлений «{value}»")
                out[key] = value
            case "roles":
                if not isinstance(value, dict):
                    raise SettingsError("роли: нужен словарь")
                roles = {}
                for role, model_id in value.items():
                    if role not in ROLES:
                        raise SettingsError(f"неизвестная роль «{role}»")
                    if model_id:
                        roles[role] = str(model_id)
                if roles:
                    out[key] = roles
            case "context_budget":  # «для опытных»: the layers' budgets and the tools' portions (D22)
                if not isinstance(value, dict):
                    raise SettingsError("бюджеты контекста: нужен словарь")
                own = {}
                for name, amount in value.items():
                    if name not in LIMITS:
                        raise SettingsError(f"неизвестный бюджет «{name}»")
                    if amount is not None:
                        own[name] = int(_number(f"бюджет {name}", amount, int, *LIMITS[name][1:]))
                if own:
                    out[key] = own
            case "system_prompt":
                text = str(value).strip()
                if len(text) > SYSTEM_PROMPT_MAX:
                    raise SettingsError(f"системный промпт: не длиннее {SYSTEM_PROMPT_MAX} символов")
                if text:
                    out[key] = text
            case _:
                raise SettingsError(f"неизвестная настройка «{key}»")
    if "prices" in out and "prices_asof" not in out:
        out["prices_asof"] = utc_now()[:10]
    return out


def preset_params(preset: str, accepted: Iterable[str]) -> dict[str, Any]:
    """A preset's values for what the provider takes."""
    taken = set(accepted)
    return {k: v for k, v in PRESETS[preset].items() if k in taken}


def clean_params(params: dict[str, Any], accepted: Iterable[str]) -> dict[str, Any]:
    """What goes to the provider: only the parameters it takes (Claude has no temperature, D15)."""
    taken = set(accepted)
    return {k: v for k, v in params.items() if k in taken}


# ---------------------------------------------------------------- the table


def current(conn: sqlite3.Connection, model_id: str) -> tuple[int, dict[str, Any]]:
    """(version, settings) as the model has them now; (0, {}) when never set."""
    row = conn.execute(
        "SELECT version, settings FROM model_settings WHERE model_id = ? ORDER BY version DESC LIMIT 1", (model_id,)
    ).fetchone()
    return (row["version"], json.loads(row["settings"])) if row else (0, {})


def all_current(conn: sqlite3.Connection) -> dict[str, tuple[int, dict[str, Any]]]:
    rows = conn.execute(
        "SELECT s.model_id, s.version, s.settings FROM model_settings s JOIN (SELECT model_id, MAX(version) AS v"
        " FROM model_settings GROUP BY model_id) last ON last.model_id = s.model_id AND last.v = s.version"
    ).fetchall()
    return {r["model_id"]: (r["version"], json.loads(r["settings"])) for r in rows}


def save(conn: sqlite3.Connection, model_id: str, settings: dict[str, Any], *, by: str) -> int:
    """A new version (checked first); the same settings again make none."""
    checked = check(settings)
    version, now = current(conn, model_id)
    if version and checked == now:
        return version
    conn.execute(
        "INSERT INTO model_settings (model_id, version, settings, created_by) VALUES (?, ?, ?, ?)",
        (model_id, version + 1, json.dumps(checked, ensure_ascii=False), by),
    )
    return version + 1


def history(conn: sqlite3.Connection, model_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT version, settings, created_at, created_by FROM model_settings WHERE model_id = ?"
        " ORDER BY version DESC LIMIT ?",
        (model_id, limit),
    ).fetchall()
    return [{"version": r["version"], "settings": json.loads(r["settings"]), "created_at": r["created_at"],
             "by": r["created_by"]} for r in rows]  # fmt: skip


def version(conn: sqlite3.Connection, model_id: str, number: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT settings FROM model_settings WHERE model_id = ? AND version = ?", (model_id, number)
    ).fetchone()
    return json.loads(row["settings"]) if row else None


def overlay(model: dict[str, Any], settings: dict[str, Any], number: int = 0) -> dict[str, Any]:
    """A catalog row as the user set it: name, shown or not, capabilities and prices over the provider's."""
    out = dict(model)
    out["provider_name"] = model["name"]
    out["hidden"] = bool(settings.get("hidden"))
    out["settings_version"] = number
    out["effort"] = settings.get("effort", "")  # the reasoning depth the window picks with this model
    if settings.get("name"):
        out["name"] = settings["name"]
    if settings.get("capabilities"):
        out["capabilities"] = {**(model.get("capabilities") or {}), **settings["capabilities"]}
    if settings.get("prices"):
        out["prices"] = {**(model.get("prices") or {}), **settings["prices"]}
        out["capabilities"] = {**out.get("capabilities", {}), "prices_asof": settings.get("prices_asof", "")}
    return out
