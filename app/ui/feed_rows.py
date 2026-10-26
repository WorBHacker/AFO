"""The event log as the feed shows it: one readable line (and a detail) per event that means something to a person.
No Qt here.

The hub keeps every stored event (D29); the feed shows those a person cares about later - projects, terminals,
ports, git, answers in chats, budgets, the hub itself - and leaves out the plumbing (single messages, titles,
catalog syncs). Each item belongs to a group the feed can be filtered by.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import presenter
from . import links_text

GROUPS = [
    ("all", "Всё"),
    ("projects", "Проекты"),
    ("terminals", "Терминалы"),
    ("processes", "Процессы"),
    ("git", "Git"),
    ("chats", "Чаты"),
    ("budgets", "Бюджеты"),
]
MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def _local(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def day_label(ts: str, now: datetime | None = None) -> str:
    """'Сегодня', 'Вчера' or '25 октября' (with the year when it is not this one)."""
    when = _local(ts)
    if when is None:
        return ""
    now = (now or datetime.now()).astimezone()
    days = (now.date() - when.date()).days
    if days <= 0:
        return "Сегодня"
    if days == 1:
        return "Вчера"
    label = f"{when.day} {MONTHS[when.month - 1]}"
    return label if when.year == now.year else f"{label} {when.year}"


def describe(event: dict[str, Any], *, projects: dict[str, str], chats: dict[str, str]) -> dict[str, Any] | None:
    """{group, icon, level (""/"warn"/"error"), title, detail} for an event worth showing; None otherwise."""
    kind, p = event.get("type", ""), event.get("payload") or {}

    def item(group: str, icon: str, title: str, detail: str = "", level: str = "") -> dict[str, Any]:
        return {"group": group, "icon": icon, "title": title, "detail": detail, "level": level}

    chat = chats.get(p.get("conversation_id", ""), "") or "без названия"
    match kind:
        case "project.added":
            info = p.get("project") or {}
            return item("projects", "folder-plus", f"Добавлен проект «{info.get('name', '')}»", info.get("path", ""))
        case "project.changed":
            info = p.get("project") or {}
            return item("projects", "folder", f"Проект «{info.get('name', '')}» изменён")
        case "project.removed":
            name = projects.get(p.get("id", ""), "") or p.get("name", "")
            return item("projects", "folder", f"Проект «{name}» убран из списка" if name else "Проект убран из списка")
        case "terminal.started":
            info = p.get("terminal") or {}
            return item("terminals", "terminal", f"Запущен терминал: {info.get('title', '')}", info.get("cwd", ""))
        case "terminal.exited":
            title, code = p.get("title", ""), p.get("exit_code")
            if p.get("stopped"):
                return item("terminals", "terminal", f"Терминал «{title}» остановлен")
            failed = code not in (0, None)
            text = f"Терминал «{title}» завершился" + (f" с кодом {code}" if code is not None else "")
            return item("terminals", "terminal", text, level="error" if failed else "")
        case "link.confirmed":
            kind = links_text.KIND_LABELS.get(p.get("kind", ""), "связь")
            note = f" ({p['description']})" if p.get("description") else ""
            return item("projects", "layers", f"Связь: {p.get('a_name')} → {p.get('b_name')}, {kind}{note}")
        case "card.drafted":
            return item("projects", "edit", f"Черновик карточки «{p.get('name', '')}» готов — проверьте и подтвердите")
        case "card.confirmed":
            name = p.get("name", "")
            again = f" (возвращена версия {p['restored_from']})" if p.get("restored_from") else ""
            return item("projects", "check", f"Карточка «{name}» подтверждена, версия {p.get('version')}{again}")
        case "card.failed":
            return item("projects", "edit", "Черновик карточки не составлен", p.get("detail", ""), "error")
        case "process.stopped":
            return item("processes", "power", f"Завершён {p.get('name', '')} (PID {p.get('pid', '')})",
                        p.get("cmdline", ""))  # fmt: skip
        case "port.opened":
            return item("processes", "cpu", f"Открыт порт :{p.get('port')} — {p.get('name', '')}", p.get("args", ""))
        case "port.closed":
            return item("processes", "cpu", f"Закрыт порт :{p.get('port')} ({p.get('name', '')})")
        case "git.head":
            sha, subject, branch = p.get("sha", ""), p.get("subject", ""), p.get("branch", "")
            if p.get("kind") == "branch":
                return item("git", "branch", f"Ветка {p.get('from', '')} → {branch}", f"{sha} {subject}".strip())
            if p.get("kind") == "commit":
                return item("git", "commit", f"Коммит в {branch}: {subject}", f"{sha} · {p.get('author', '')}")
            return item("git", "commit", f"{branch} теперь на {sha}: {subject}")
        case "chat.done":
            cost = p.get("cost_usd") or 0
            tokens = (p.get("usage") or {}).get("output_tokens") or 0
            parts = [f"{presenter.tokens_short(tokens)} ток. ответа" if tokens else "",
                     presenter.money(cost) if cost else "бесплатно"]  # fmt: skip
            title = f"Ответ в чате «{chat}»" + (" — остановлен" if p.get("cancelled") else "")
            return item("chats", "chat", title, " · ".join(x for x in parts if x))
        case "chat.error":
            label = "Отказ модели" if p.get("refusal") else "Ошибка"
            return item("chats", "chat", f"{label} в чате «{chat}»", p.get("detail", ""), "error")
        case "budget.warning" | "budget.exceeded":
            budget = p.get("budget") or {}
            level = "warn" if kind == "budget.warning" else "error"
            return item("budgets", "wallet", presenter.budget_event_text(kind.removeprefix("budget."), budget),
                        level=level)  # fmt: skip
        case "budget.overridden":
            return item("budgets", "wallet", "Запрос отправлен сверх бюджета (с подтверждением)", level="warn")
        case "budget.changed":
            return item("budgets", "wallet", "Бюджеты изменены")
        case "secrets.changed":
            return item("all", "key", "Ключ API изменён")
        case "hub.started":
            return item("all", "layers", "Хаб запущен", f"версия {p.get('version', '')}")
        case "hub.stopping":
            return item("all", "power", "Хаб остановлен")
    return None


def feed_rows(
    events: list[dict[str, Any]],
    *,
    projects: dict[str, str],
    chats: dict[str, str],
    group: str = "all",
    sections: bool = True,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Events (newest first) as rows: a day heading when the day changes, then one row per event."""
    rows: list[dict[str, Any]] = []
    day = None
    for event in events:
        shown = describe(event, projects=projects, chats=chats)
        if shown is None or (group != "all" and shown["group"] != group):
            continue
        when = _local(event.get("ts", ""))
        label = day_label(event.get("ts", ""), now)
        if sections and label != day:
            rows.append({"section": label, "id": f"d:{label}"})
            day = label
        rows.append(
            {
                **shown,
                "id": event["id"],
                "section": "",
                "time": when.strftime("%H:%M") if when else "",
                "project": projects.get(event.get("project_id") or "", ""),
            }
        )
    return rows
