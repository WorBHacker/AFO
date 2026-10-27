"""The command palette (Ctrl+K): actions, chats, models and budgets that match what was typed. No Qt here.

An item's id says what to do: "action:<name>", "chat:<id>", "model:<id>", "budget:<id>", "project:<id>" (the
controller runs it).
"""

from __future__ import annotations

import html
from typing import Any

from .. import presenter

# name, title, icon, key hint; "current" - only with a chat open
ACTIONS: list[tuple[str, str, str, str, bool]] = [
    ("new", "Новый чат", "plus", "Ctrl N", False),
    ("mode:chats", "Перейти к чатам", "chat", "Ctrl 1", False),
    ("mode:projects", "Перейти к проектам", "folder", "Ctrl 2", False),
    ("mode:agent", "Перейти к агенту: задачи автономного агента", "agent", "Ctrl 3", False),
    ("add-project", "Добавить проект…", "folder-plus", "", False),
    ("search-project", "Найти в файлах проекта", "search", "Ctrl Shift F", False),
    ("new-terminal", "Новый терминал в проекте", "terminal", "Ctrl Shift `", False),
    ("terminals", "Показать или скрыть терминалы", "terminal", "Ctrl `", False),
    ("models", "Выбрать модель…", "cpu", "", False),
    ("model-settings", "Настройка моделей: параметры, промпты, цены…", "sliders", "", False),
    ("budgets", "Бюджеты…", "wallet", "Ctrl B", False),
    ("feed", "Лента событий", "activity", "Ctrl E", False),
    ("approvals", "Подтверждения: кто ждёт", "shield", "Ctrl Shift A", False),
    ("agents", "Агенты", "agent", "Ctrl Shift G", False),
    ("programs", "Программы для агента…", "shield", "", False),
    ("settings", "Настройки…", "sliders", "Ctrl ,", False),
    ("connections", "Подключения: ключи API и токены…", "key", "Ctrl Shift K", False),
    ("sync", "Обновить список моделей", "refresh", "", False),
    ("unload", "Выгрузить локальную модель из видеопамяти", "cpu", "Ctrl Shift U", False),
    ("theme:system", "Тема: как в системе", "layers", "", False),
    ("theme:light", "Тема: светлая", "sun", "", False),
    ("theme:dark", "Тема: тёмная", "moon", "", False),
    ("rename", "Переименовать этот чат", "edit", "F2", True),
    ("delete", "Удалить этот чат", "trash", "", True),
    ("stop-hub", "Остановить хаб и выйти", "power", "", False),
]
RECENT_CHATS = 6
MAX_MATCHES = 8


def highlight(text: str, query: str) -> str:
    """The text as StyledText with the first match in bold; everything else escaped."""
    at = text.lower().find(query) if query else -1
    if at < 0:
        return html.escape(text)
    end = at + len(query)
    return f"{html.escape(text[:at])}<b>{html.escape(text[at:end])}</b>{html.escape(text[end:])}"


def palette_items(
    query: str,
    *,
    chats: list[dict[str, Any]],
    models: list[dict[str, Any]],
    budgets: list[dict[str, Any]],
    has_current: bool,
    projects: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    q = query.strip().lower()

    def hit(*texts: str) -> bool:
        return not q or any(q in t.lower() for t in texts)

    items: list[dict[str, Any]] = []

    def section(title: str, entries: list[dict[str, Any]]) -> None:
        if entries:
            items.append({"section": title, "id": ""})
            items.extend(entries)

    section(
        "Действия",
        [
            {"id": f"action:{name}", "name": highlight(title, q), "icon": icon, "key": key, "note": ""}
            for name, title, icon, key, needs_chat in ACTIONS
            if (has_current or not needs_chat) and hit(title)
        ],
    )
    matched_chats = [c for c in chats if hit(c["title"])]
    section(
        "Чаты" if q else "Недавние чаты",
        [
            {
                "id": f"chat:{c['id']}",
                "name": highlight(c["title"], q),
                "icon": "cloud" if c.get("cloud") else "cpu",
                "note": c.get("section", ""),
                "key": "",
            }
            for c in matched_chats[: MAX_MATCHES if q else RECENT_CHATS]
        ],
    )
    section(
        "Проекты",
        [
            {
                "id": f"project:{p['id']}",
                "name": highlight(p["name"], q),
                "icon": "folder",
                "note": p["path"],
                "key": "",
            }
            for p in projects or []
            if hit(p["name"], p["path"])
        ][:MAX_MATCHES],
    )
    if q:
        section(
            "Модели",
            [
                {
                    "id": f"model:{m['id']}",
                    "name": highlight(m["name"], q),
                    "icon": "cpu" if m.get("locality") == "local" else "cloud",
                    "note": presenter.model_label(m).removeprefix(m["name"]).lstrip(" ·"),
                    "key": "",
                }
                for m in presenter.sorted_models(models)
                if hit(m["name"], m["id"])
            ][:MAX_MATCHES],
        )
        section(
            "Бюджеты",
            [
                {
                    "id": f"budget:{b['id']}",
                    "name": highlight(presenter.budget_title(b), q),
                    "icon": "wallet",
                    "note": presenter.budget_line(b).split(": ", 1)[-1],
                    "key": "",
                }
                for b in budgets
                if hit(presenter.budget_title(b), "бюджет")
            ],
        )
    return items


def budget_items(budgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Budgets as cards: title, spent, limit, the bar and where the warning starts on it."""
    cards = []
    for b in budgets:
        soft, hard, spent = b.get("soft_usd"), b.get("hard_usd"), b.get("spent_usd", 0)
        scale = hard or soft or 1
        parts = []
        if soft is not None:
            parts.append(f"предупредить с {presenter.money(soft)}")
        if hard is not None:
            action = dict(presenter.HARD_ACTIONS).get(b.get("hard_action"), "")
            parts.append(f"после лимита — {action}")
        cards.append(
            {
                "id": b["id"],
                "title": presenter.budget_title(b),
                "spent": presenter.money(spent),
                "limit": f"из {presenter.money(hard)}" if hard is not None else "без лимита",
                "ratio": min(1.0, spent / scale),
                "softAt": (soft / scale) if soft is not None and hard else -1,
                "state": b.get("state", "ok"),
                "note": (parts[0][:1].upper() + " · ".join(parts)[1:]) if parts else "",
                "scopeKey": f"{b['scope']}:{b['scope_ref']}",
                "period": b["period"],
                "soft": "" if soft is None else f"{soft:g}",
                "hard": "" if hard is None else f"{hard:g}",
                "action": b.get("hard_action", "ask"),
            }
        )
    return cards


def budget_scopes(models: list[dict[str, Any]], budgets: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What a budget can cover: all paid models, or a provider that has paid models or a budget already."""
    providers = {(m.get("provider") or m["id"].partition("/")[0]) for m in models if m.get("prices")}
    providers |= {b["scope_ref"] for b in budgets if b["scope"] == "provider"}
    return [{"id": "total:", "name": "Все платные модели"}] + [
        {"id": f"provider:{p}", "name": presenter.provider_name(p)} for p in sorted(providers)
    ]
