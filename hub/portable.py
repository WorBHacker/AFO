"""All of the hub's settings as one JSON file, and the file back (D46, step 13.10): a copy to keep, or the setup taken
to another computer.

Nothing secret goes in. API keys, passwords, tokens and MCP servers' environments stay in the secret store (D13), and
any text in the file that looks like a key is cut out (`redacted` says how many). What the hub found or did, rather
than was told, stays too: conversations, memory, cards, the programs it found, Telegram's link.

Projects travel by folder, agents by name: ids differ on each hub. Loading is seen before it is done -
`load(apply=False)` loads it all in a transaction and takes it back, so the preview is the loading itself, line by
line. Loading adds and changes, never removes. What would start by itself comes in turned off: an MCP server (a
program that runs as the user) and a schedule. The parts behind the connections' password load only while the
connections are open.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from common import invisible

from . import (
    agent_tools,
    agents,
    autostart,
    budgets,
    llama_config,
    mail,
    mcp_client,
    model_settings,
    retention,
    schedule,
    store,
)
from . import connections as conns
from .db import utc_now
from .projects import ProjectError, add_project, get_project, list_projects, normalize_root, update_project
from .secret_filter import MASK, redact

KIND = "localchat-settings"
VERSION = 1
RULE_CHARS = 2000
NAMES = {"name": "имя", "hidden": "скрыта", "capabilities": "возможности", "prices": "цены", "prices_asof": "дата цен",
         "params": "параметры", "preset": "заготовка", "effort": "размышления", "roles": "роли",
         "context_budget": "бюджеты контекста", "system_prompt": "системный промпт", "instructions": "инструкции",
         "model_id": "модель", "tools": "инструменты", "mode": "режим", "project_ids": "проекты",
         "max_steps": "шагов", "max_minutes": "минут", "budget_usd": "бюджет"}  # fmt: skip
PERIODS = {"day": "в день", "week": "в неделю", "month": "в месяц"}
AGENT_FIELDS = ("name", "instructions", "model_id", "tools", "mode", "max_steps", "max_minutes", "budget_usd")
BUDGET_FIELDS = ("scope", "scope_ref", "period", "soft_usd", "hard_usd", "hard_action")


class PortableError(ValueError):
    """The message is fit to show."""


@dataclass
class _Loading:
    by: str
    now: datetime
    added_projects: list[str] = field(default_factory=list)
    catalog: bool = False  # the models offered changed: the catalog follows


def _item(what: str, action: str, detail: str = "", warn: str = "") -> dict[str, str]:
    """A line of the preview: add, change, same or skip, and why."""
    return {"what": what, "action": action, "detail": detail, "warn": warn}


def _setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def _differs(old: dict[str, Any], new: dict[str, Any]) -> str:
    return ", ".join(NAMES.get(k, k) for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k))


def _hidden(*texts: Any) -> str:
    """A word when a text a model will be told holds characters a person does not see (D46, «Trojan Source»): a file
    from someone else could hide an instruction in it."""
    return next((f"{said} — проверьте текст" for t in texts if (said := invisible.summary(str(t or "")))), "")


def _whole(folder: str) -> bool:
    """A full path to a folder this computer has ("" or a relative one would be read from the hub's own folder)."""
    return bool(folder) and Path(folder).is_absolute() and Path(folder).is_dir()


def _project_ids(conn: sqlite3.Connection, folders: list[str]) -> tuple[list[str], list[str]]:
    """This hub's projects at these folders, and the folders that are none of its projects."""
    found, missing = [], []
    for folder in folders:
        pid = _project_at(conn, str(folder))
        (found if pid else missing).append(pid or str(folder))
    return found, missing


def _root(folder: str) -> Path | None:
    """The folder on this computer; None for one it does not have - or for a path that is not whole, which would
    otherwise be read from the hub's own folder."""
    if not folder or not Path(folder).is_absolute():
        return None
    try:
        return normalize_root(folder)
    except ProjectError:
        return None


def _project_at(conn: sqlite3.Connection, folder: str) -> str | None:
    root = _root(folder)
    if root is None:
        return None
    row = conn.execute("SELECT id FROM projects WHERE path = ? COLLATE NOCASE AND scratch = 0", (str(root),)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------- the models' settings (format: hub/api/model_settings)


def _models_out(conn: sqlite3.Connection, paths: dict[str, str]) -> dict[str, Any]:
    return model_settings.exported(conn)


def _models_in(conn: sqlite3.Connection, value: dict[str, Any], loading: _Loading) -> list[dict[str, str]]:
    known = {m["id"] for m in store.list_models(conn, enabled_only=False)}
    general = dict(value.get("general") or {})
    roles = dict(general.get("roles") or {})
    items = [_item(f"роль {role}", "skip", f"модели {mid} нет в этом хабе") for role, mid in roles.items()
             if mid not in known]  # fmt: skip
    entries = [(model_settings.ALL, {"system_prompt": general.get("system_prompt"),
                                     "roles": {r: m for r, m in roles.items() if m in known}})]  # fmt: skip
    entries += sorted((value.get("models") or {}).items())
    for model_id, settings in entries:
        what = model_id or "общие: системный промпт и роли"
        if model_id and model_id not in known:
            items.append(_item(what, "skip", "такой модели нет в этом хабе"))
            continue
        number, before = model_settings.current(conn, model_id)
        try:
            checked = model_settings.check(settings)
        except model_settings.SettingsError as exc:
            items.append(_item(what, "skip", str(exc)))
            continue
        if checked == before:
            items.append(_item(what, "same"))
            continue
        model_settings.save(conn, model_id, checked, by=loading.by)
        warn = _hidden(checked.get("system_prompt"))
        items.append(_item(what, "change" if number else "add", _differs(before, checked), warn))
    return items


# ---------------------------------------------------------------- budgets


def _budgets_out(conn: sqlite3.Connection, paths: dict[str, str]) -> list[dict[str, Any]]:
    return [{f: getattr(b, f) for f in BUDGET_FIELDS} for b in budgets.list_budgets(conn)]


def _budgets_in(conn: sqlite3.Connection, value: list[dict[str, Any]], loading: _Loading) -> list[dict[str, str]]:
    def usd(amount: Any) -> float | None:
        return float(amount) if amount is not None else None

    have = {(b.scope, b.scope_ref, b.period): b for b in budgets.list_budgets(conn)}
    items = []
    for entry in value:
        scope, ref, period = entry.get("scope"), str(entry.get("scope_ref") or ""), entry.get("period")
        what = f"{'все платные модели' if scope == 'total' else ref}, {PERIODS.get(period, period)}"
        limits = (usd(entry.get("soft_usd")), usd(entry.get("hard_usd")), entry.get("hard_action") or "ask")
        detail = ", ".join(p for p in (f"предупреждение ${limits[0]:g}" if limits[0] is not None else "",
                                       f"предел ${limits[1]:g}" if limits[1] is not None else "") if p)  # fmt: skip
        old = have.get((scope, ref, period))
        if old and (old.soft_usd, old.hard_usd, old.hard_action) == limits:
            items.append(_item(what, "same", detail))
            continue
        try:
            budgets.upsert_budget(conn, scope=scope, scope_ref=ref, period=period, soft_usd=limits[0],
                                  hard_usd=limits[1], hard_action=limits[2])  # fmt: skip
        except ValueError as exc:
            items.append(_item(what, "skip", str(exc)))
            continue
        items.append(_item(what, "change" if old else "add", detail))
    return items


# ---------------------------------------------------------------- projects and their rules


def _projects_out(conn: sqlite3.Connection, paths: dict[str, str]) -> list[dict[str, Any]]:
    rules: dict[str, list[str]] = {}
    for row in conn.execute("SELECT project_id, grant_key FROM permission_rules ORDER BY created_at, id"):
        rules.setdefault(row["project_id"], []).append(row["grant_key"])
    return [{"path": p.path, "name": p.name, "description": p.description, "cloud_ok": p.cloud_ok,
             "rules": rules.get(p.id, [])} for p in list_projects(conn)]  # fmt: skip


def _projects_in(conn: sqlite3.Connection, value: list[dict[str, Any]], loading: _Loading) -> list[dict[str, str]]:
    items = []
    for entry in value:
        folder = str(entry.get("path") or "")
        root = _root(folder)
        if root is None:
            items.append(_item(folder, "skip", "такой папки на этом компьютере нет"))
            continue
        name, description = str(entry.get("name") or root.name), str(entry.get("description") or "")
        cloud = bool(entry.get("cloud_ok"))
        pid = _project_at(conn, str(root))
        if pid is None:
            try:
                pid = add_project(conn, str(root), name).id
            except ProjectError as exc:
                items.append(_item(str(root), "skip", str(exc)))
                continue
            update_project(conn, pid, description=description, cloud_ok=cloud)
            loading.added_projects.append(pid)
            action, was_cloud, detail = "add", False, "облаку можно" if cloud else "только локально"
        else:
            old = get_project(conn, pid)
            changed = [label for label, a, b in (("имя", old.name, name), ("описание", old.description, description),
                                                  ("облако", old.cloud_ok, cloud)) if a != b]  # fmt: skip
            if changed:
                update_project(conn, pid, name=name, description=description, cloud_ok=cloud)
            action, was_cloud, detail = "change" if changed else "same", old.cloud_ok, ", ".join(changed)
        rules = [k for k in entry.get("rules") or [] if isinstance(k, str) and 0 < len(k) <= RULE_CHARS]
        added = [k for k in rules if conn.execute(
            "INSERT OR IGNORE INTO permission_rules (project_id, grant_key, created_by) VALUES (?, ?, ?)",
            (pid, k, loading.by)).rowcount]  # fmt: skip
        if added and action == "same":
            action, detail = "change", f"правил: +{len(added)}"
        warns = ["содержимое проекта сможет уходить облачным моделям"] if cloud and not was_cloud else []
        if added:
            warns.append("разрешено без вопроса в этом проекте: " + "; ".join(invisible.reveal(k) for k in added))
        items.append(_item(str(root), action, detail, ". ".join(warns)))
    return items


# ---------------------------------------------------------------- agents


def _agents_out(conn: sqlite3.Connection, paths: dict[str, str]) -> list[dict[str, Any]]:
    return [{**{f: p[f] for f in AGENT_FIELDS}, "projects": [paths[i] for i in p["project_ids"] if i in paths]}
            for p in agents.list_profiles(conn)]  # fmt: skip


def _agents_in(conn: sqlite3.Connection, value: list[dict[str, Any]], loading: _Loading) -> list[dict[str, str]]:
    have = {p["name"]: p for p in agents.list_profiles(conn)}
    items = []
    for entry in value:
        ids, missing = _project_ids(conn, list(entry.get("projects") or []))
        data = {**{f: entry.get(f) for f in AGENT_FIELDS}, "project_ids": ids}
        what = " ".join(str(data["name"] or "").split())
        try:
            checked = agents.check_profile(conn, data)
        except agents.AgentError as exc:
            items.append(_item(what, "skip", str(exc)))
            continue
        old = have.get(checked["name"])
        notes = f"нет проектов: {', '.join(missing)}" if missing else ""
        if old is not None and all(old[k] == checked[k] for k in checked):
            items.append(_item(what, "same", notes))
            continue
        agents.save_profile(conn, checked, old["id"] if old else None)
        trusted = "режим «Доверенный»: пишет в своих проектах без вопросов" if checked["mode"] == "trusted" else ""
        warn = "; ".join(w for w in (trusted, _hidden(checked["instructions"])) if w)
        was = {k: old[k] for k in checked} if old else {}
        detail = "; ".join(p for p in (_differs(was, checked) if old else "", notes) if p)
        items.append(_item(what, "change" if old else "add", detail, warn))
    return items


# ---------------------------------------------------------------- schedules


def _schedules_out(conn: sqlite3.Connection, paths: dict[str, str]) -> list[dict[str, Any]]:
    names = {p["id"]: p["name"] for p in agents.list_profiles(conn)}
    out = []
    for found in schedule.schedules(conn):
        goal = dict(found["goal"])
        if "project_ids" in goal:
            goal["projects"] = [paths[i] for i in goal.pop("project_ids") or [] if i in paths]
        if "profile_id" in goal:
            goal["profile"] = names.get(goal.pop("profile_id"), "")
        out.append({"title": found["title"], "goal": goal, "trigger": found["trigger"], "computer": found["computer"]})
    return out


def _schedules_in(conn: sqlite3.Connection, value: list[dict[str, Any]], loading: _Loading) -> list[dict[str, str]]:
    profiles = {p["name"]: p["id"] for p in agents.list_profiles(conn)}
    have = schedule.schedules(conn)
    items = []
    for entry in value:
        goal = dict(entry.get("goal") or {})
        what = str(entry.get("title") or goal.get("goal") or "")
        notes = []
        if "projects" in goal:
            goal["project_ids"], missing = _project_ids(conn, list(goal.pop("projects") or []))
            notes += [f"нет проектов: {', '.join(missing)}"] if missing else []
        if "profile" in goal:
            name = goal.pop("profile")
            if name not in profiles:
                items.append(_item(what, "skip", f"нет агента «{name}»"))
                continue
            goal["profile_id"] = profiles[name]
        trigger = dict(entry.get("trigger") or {})
        if trigger.get("kind") == "file" and not _whole(str(trigger.get("folder") or "")):
            items.append(_item(what, "skip", f"папки {trigger.get('folder')} на этом компьютере нет"))
            continue
        data = {"title": entry.get("title"), "goal": goal, "trigger": trigger, "computer": bool(entry.get("computer")),
                "enabled": False}  # fmt: skip
        try:
            checked = schedule.check_schedule(data)
        except schedule.ScheduleError as exc:
            items.append(_item(what, "skip", str(exc)))
            continue
        if any((s["title"], s["goal"], s["trigger"]) == (checked["title"], checked["goal"], checked["trigger"])
               for s in have):  # fmt: skip
            items.append(_item(what, "same", "; ".join(notes)))
            continue
        schedule.add_schedule(conn, data, loading.now)
        warns = ["придёт выключенным: включите его в «Расписании», когда посмотрите",
                 "его задачи смогут управлять компьютером" if checked["computer"] else "",
                 _hidden(checked["goal"].get("goal"), checked["goal"].get("done_when"))]  # fmt: skip
        warn = "; ".join(w for w in warns if w)
        items.append(_item(what, "add", "; ".join(notes), warn))
    return items


# ---------------------------------------------------------------- what agents may reach


def _access_out(conn: sqlite3.Connection, paths: dict[str, str]) -> dict[str, Any]:
    return {"folders": agent_tools.folders(conn), "domains": agent_tools.domains(conn),
            "screen_cloud": agent_tools.screen_cloud(conn)}  # fmt: skip


def _access_in(conn: sqlite3.Connection, value: dict[str, Any], loading: _Loading) -> list[dict[str, str]]:
    items = []
    folders = {f["path"].lower(): f for f in agent_tools.folders(conn)}
    before = dict(folders)
    for entry in value.get("folders") or []:
        folder = str((entry or {}).get("path") or "")
        if not _whole(folder):
            items.append(_item(folder, "skip", "такой папки на этом компьютере нет"))
            continue
        try:
            checked = agent_tools.check_folders([entry])[0]
        except agent_tools.AgentToolError as exc:
            items.append(_item(folder, "skip", str(exc)))
            continue
        old = before.get(checked["path"].lower())
        rights = ("чтение и запись" if checked["write"] else "чтение") + (", облаку можно" if checked["cloud"] else "")
        if old == checked:
            items.append(_item(checked["path"], "same", rights))
            continue
        folders[checked["path"].lower()] = checked
        warns = [w for w, now, was in (("агент сможет писать в эту папку", checked["write"], old and old["write"]),
                                       ("её файлы смогут уходить облачным моделям", checked["cloud"],
                                        old and old["cloud"])) if now and not was]  # fmt: skip
        items.append(_item(checked["path"], "change" if old else "add", rights, "; ".join(warns)))
    if folders != before:
        agent_tools.set_setting(conn, agent_tools.FOLDERS, list(folders.values()))
    have = agent_tools.domains(conn)
    try:
        sites = agent_tools.check_domains(list(value.get("domains") or []))
    except agent_tools.AgentToolError as exc:
        items.append(_item("сайты без вопроса", "skip", str(exc)))
    else:
        new = [s for s in sites if s not in have]
        if new:
            agent_tools.set_setting(conn, agent_tools.DOMAINS, sorted({*have, *new}))
        items.append(_item("сайты без вопроса", "add" if new else "same", ", ".join(new)))
    if "screen_cloud" in value:
        screen = bool(value["screen_cloud"])
        if screen == agent_tools.screen_cloud(conn):
            items.append(_item("окна программ — облачным моделям", "same", "да" if screen else "нет"))
        else:
            agent_tools.set_setting(conn, agent_tools.SCREEN_CLOUD, screen)
            items.append(_item("окна программ — облачным моделям", "change", "да" if screen else "нет",
                               "облачная модель сможет читать окна других программ" if screen else ""))  # fmt: skip
    return items


# ---------------------------------------------------------------- the hub itself


def _hub_out(conn: sqlite3.Connection, paths: dict[str, str]) -> dict[str, Any]:
    return {"quiet": schedule.quiet_hours(conn), "retention": retention.current(conn),
            "llama_swap_autostart": autostart.llama_swap_wanted(conn),
            "llama_swap_start": _setting(conn, autostart.START_SETTING, ""),
            "llama_swap_config": _setting(conn, llama_config.CONFIG_SETTING, "")}  # fmt: skip


def _hub_in(conn: sqlite3.Connection, value: dict[str, Any], loading: _Loading) -> list[dict[str, str]]:
    items = []

    def one(what: str, now: Any, new: Any, detail: str, put: Callable[[], Any]) -> None:
        if now == new:
            items.append(_item(what, "same", detail))
            return
        try:
            put()
        except ValueError as exc:
            items.append(_item(what, "skip", str(exc)))
            return
        items.append(_item(what, "change", detail))

    if "quiet" in value:
        quiet = schedule.check_quiet(dict(value["quiet"]))
        one("тихие часы", schedule.quiet_hours(conn), quiet,
            f"{quiet['from']}–{quiet['to']}" if quiet["enabled"] else "выключены",
            lambda: schedule.set_quiet_hours(conn, quiet))  # fmt: skip
    if "retention" in value:
        kept = dict(value["retention"])
        one("хранение копий файлов, снимков и журналов", retention.current(conn), kept,
            f"{kept.get('days')} дней, не больше {kept.get('max_gb')} ГБ",
            lambda: retention.save(conn, kept.get("days"), kept.get("max_gb")))  # fmt: skip
    if "llama_swap_autostart" in value:
        wanted = bool(value["llama_swap_autostart"])
        one("хаб сам запускает llama-swap", autostart.llama_swap_wanted(conn), wanted, "да" if wanted else "нет",
            lambda: autostart.set_llama_swap_wanted(conn, wanted))  # fmt: skip
    for key, setting, what in (("llama_swap_start", autostart.START_SETTING, "запуск llama-swap"),
                               ("llama_swap_config", llama_config.CONFIG_SETTING, "конфиг llama-swap")):  # fmt: skip
        chosen = str(value.get(key) or "")
        if not chosen:  # LocalChat's default in the file: this hub's own choice stays
            continue
        if not Path(chosen).is_file():
            items.append(_item(what, "skip", f"файла {chosen} на этом компьютере нет"))
            continue
        # the hub reads it again after loading (autostart.load_choices)
        one(what, _setting(conn, setting, ""), chosen, chosen,
            lambda s=setting, c=chosen: agent_tools.set_setting(conn, s, c))  # fmt: skip
    return items


# ---------------------------------------------------------------- behind the connections' password


def _connections_out(conn: sqlite3.Connection, paths: dict[str, str]) -> dict[str, Any]:
    addresses = {c.name: conns.address(conn, c.name) for c in conns.CONNECTIONS if c.kind == "address"}
    chosen = {c.provider: conns.chosen_models(conn, c.provider) for c in conns.CONNECTIONS if c.choose}
    return {"addresses": {k: v for k, v in addresses.items() if v}, "chosen": {k: v for k, v in chosen.items() if v},
            "mail": mail.account(conn)}  # fmt: skip


def _connections_in(conn: sqlite3.Connection, value: dict[str, Any], loading: _Loading) -> list[dict[str, str]]:
    items = []
    for name, address in (value.get("addresses") or {}).items():
        connection = conns.BY_NAME.get(name)
        what = connection.title if connection else name
        if connection is None or connection.kind != "address":
            items.append(_item(what, "skip", "такого адреса LocalChat не знает"))
            continue
        try:
            address = conns.clean_value(connection, str(address))
        except ValueError as exc:
            items.append(_item(what, "skip", str(exc)))
            continue
        if MASK in address:
            items.append(_item(what, "skip", "в адресе был пароль, в файл он не попал: впишите адрес сами"))
            continue
        old = conns.address(conn, name)
        if old != address:
            conns.set_address(conn, name, address)
        items.append(_item(what, "same" if old == address else ("change" if old else "add"), address))
    for provider, ids in (value.get("chosen") or {}).items():
        have = conns.chosen_models(conn, provider)
        new = sorted({str(i) for i in ids} - set(have))
        if new:
            conns.set_chosen_models(conn, provider, [*have, *new])
            loading.catalog = True
        items.append(_item(f"модели {provider} в списке", "add" if new else "same", f"+{len(new)}" if new else ""))
    if value.get("mail"):
        what = "почтовый ящик"
        try:
            account = mail.check_account(dict(value["mail"]))
        except mail.MailError as exc:
            items.append(_item(what, "skip", str(exc)))
            return items
        old = mail.account(conn)
        if old == account:
            items.append(_item(what, "same", account["address"]))
        else:
            mail.set_account(conn, account)
            items.append(_item(what, "change" if old else "add", account["address"],
                               "пароль ящика в файл не попадает: впишите его в «Подключениях»"))  # fmt: skip
    return items


def _mcp_out(conn: sqlite3.Connection, paths: dict[str, str]) -> list[dict[str, Any]]:
    return [{"name": s["name"], "target": s["target"], "classes": mcp_client.tool_classes(conn, s["name"])}
            for s in mcp_client.servers(conn)]  # fmt: skip


def _mcp_in(conn: sqlite3.Connection, value: list[dict[str, Any]], loading: _Loading) -> list[dict[str, str]]:
    items = []
    for entry in value:
        name, target = str(entry.get("name") or ""), str(entry.get("target") or "")
        if MASK in target:
            items.append(_item(name, "skip", "в команде был ключ, в файл он не попал: добавьте сервер сами"))
            continue
        old = mcp_client.get_server(conn, name.strip().lower())
        if old is not None:
            items.append(
                _item(name, "same", target)
                if old["target"] == target
                else _item(name, "skip", "сервер с таким именем уже есть, с другой командой: оставлен как есть")
            )
            continue
        try:
            server = mcp_client.add_server(conn, {"name": name, "target": target})
            mcp_client.set_enabled(conn, server["name"], False)
            for tool, cls in (entry.get("classes") or {}).items():
                mcp_client.set_tool_class(conn, server["name"], str(tool), str(cls))
        except mcp_client.McpError as exc:
            items.append(_item(name, "skip", str(exc)))
            continue
        warn = ("запускает программу от вашего имени: придёт выключенным, включите в «Подключениях», когда "
                "посмотрите; его ключи впишите там же")  # fmt: skip
        items.append(_item(name, "add", invisible.reveal(target), warn))
    return items


@dataclass(frozen=True)
class Part:
    id: str
    title: str
    out: Callable[[sqlite3.Connection, dict[str, str]], Any]
    load: Callable[[sqlite3.Connection, Any, _Loading], list[dict[str, str]]]
    locked: bool = False  # behind the connections' password (D13)


PARTS = (  # in the order of loading: what a later part names comes first
    Part("models", "Настройки моделей", _models_out, _models_in),
    Part("budgets", "Бюджеты", _budgets_out, _budgets_in),
    Part("projects", "Проекты и их правила", _projects_out, _projects_in),
    Part("agents", "Агенты", _agents_out, _agents_in),
    Part("schedules", "Расписания", _schedules_out, _schedules_in),
    Part("access", "Папки и сайты для агента", _access_out, _access_in),
    Part("hub", "Хаб: тихие часы, хранение, llama-swap", _hub_out, _hub_in),
    Part("connections", "Подключения: адреса, выбранные модели, почта", _connections_out, _connections_in, True),
    Part("mcp", "MCP-серверы", _mcp_out, _mcp_in, True),
)


def _scrubbed(value: Any) -> tuple[Any, int]:
    """Every text with what looks like a key cut out, and how many were."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        done = [_scrubbed(v) for v in value]
        return [v for v, _n in done], sum(n for _v, n in done)
    if isinstance(value, dict):
        pairs = {k: _scrubbed(v) for k, v in value.items()}
        return {k: v for k, (v, _n) in pairs.items()}, sum(n for _v, n in pairs.values())
    return value, 0


def export(conn: sqlite3.Connection) -> dict[str, Any]:
    paths = {p.id: p.path for p in list_projects(conn)}
    parts, redacted = _scrubbed({part.id: part.out(conn, paths) for part in PARTS})
    return {"kind": KIND, "version": VERSION, "exported_at": utc_now(), "redacted": redacted, **parts}


def load(conn: sqlite3.Connection, data: Any, *, apply: bool, unlocked: bool, chosen: set[str] | None = None,
         by: str = "app") -> dict[str, Any]:  # fmt: skip
    """What loading the file does, part by part - done when `apply`, else taken back. A part behind the password
    without it is shown but not loaded; a part the file holds wrongly is said and skipped, the rest still load."""
    if not isinstance(data, dict) or data.get("kind") != KIND:
        raise PortableError("это не файл настроек LocalChat")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise PortableError("файл сохранён более новой версией LocalChat: обновите эту")
    loading = _Loading(by=by, now=datetime.now())
    parts = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for part in PARTS:
            if part.id not in data or (chosen is not None and part.id not in chosen):
                continue
            locked = part.locked and not unlocked
            shown = {"id": part.id, "title": part.title, "locked": locked, "items": []}
            parts.append(shown)
            if locked and apply:
                continue
            conn.execute("SAVEPOINT part")
            try:
                shown["items"] = part.load(conn, data[part.id], loading)
            except (TypeError, ValueError, AttributeError, KeyError) as exc:
                conn.execute("ROLLBACK TO part")
                shown["items"] = [_item(part.title, "skip", f"эта часть файла не читается ({exc})")]
            conn.execute("RELEASE part")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT" if apply else "ROLLBACK")
    return {"exported_at": str(data.get("exported_at") or ""), "parts": parts,
            "added_projects": loading.added_projects if apply else [],
            "catalog": loading.catalog and apply}  # fmt: skip
