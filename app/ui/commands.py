"""The command palette (Ctrl+K): actions, chats, models and budgets that match what was typed. No Qt here.

An item's id says what to do: "action:<name>", "chat:<id>", "model:<id>", "budget:<id>", "project:<id>" (the
controller runs it).
"""

from __future__ import annotations

import html
from typing import Any

from common.i18n import N_, tr

from .. import presenter

# name, title, icon, key hint; "current" - only with a chat open
ACTIONS: list[tuple[str, str, str, str, bool]] = [
    ("new", N_("Новый чат"), "plus", "Ctrl N", False),
    ("mode:chats", N_("Перейти к чатам"), "chat", "Ctrl 1", False),
    ("mode:projects", N_("Перейти к проектам"), "folder", "Ctrl 2", False),
    ("mode:agent", N_("Перейти к агенту: задачи автономного агента"), "agent", "Ctrl 3", False),
    ("add-project", N_("Добавить проект…"), "folder-plus", "", False),
    ("search-project", N_("Найти в файлах проекта"), "search", "Ctrl Shift F", False),
    ("new-terminal", N_("Новый терминал в проекте"), "terminal", "Ctrl Shift `", False),
    ("terminals", N_("Показать или скрыть терминалы"), "terminal", "Ctrl `", False),
    ("models", N_("Выбрать модель…"), "cpu", "", False),
    ("model-settings", N_("Настройка моделей: параметры, промпты, цены…"), "sliders", "", False),
    ("install-models", N_("Установить локальную модель…"), "download", "", False),
    ("setup", N_("Мастер первого запуска: движок и первая модель…"), "sparkle", "", False),
    ("check-system", N_("Проверить систему и собрать журналы…"), "activity", "", False),
    ("memory", N_("Память агента: факты и навыки…"), "layers", "", False),
    ("agent-access", N_("Папки и сайты для агента…"), "folder", "", False),
    ("budgets", N_("Бюджеты…"), "wallet", "Ctrl B", False),
    ("feed", N_("Лента событий"), "activity", "Ctrl E", False),
    ("approvals", N_("Подтверждения: кто ждёт"), "shield", "Ctrl Shift A", False),
    ("agents", N_("Агенты"), "agent", "Ctrl Shift G", False),
    ("proving", N_("Полигон: задания и экзамен моделей…"), "flask", "", False),
    ("programs", N_("Программы для агента…"), "shield", "", False),
    ("settings", N_("Настройки…"), "sliders", "Ctrl ,", False),
    ("connections", N_("Подключения: ключи API и токены…"), "key", "Ctrl Shift K", False),
    ("mcp", N_("Серверы MCP: инструменты для чатов и агента…"), "layers", "", False),
    ("sync", N_("Обновить список моделей"), "refresh", "", False),
    ("unload", N_("Выгрузить локальную модель из видеопамяти"), "cpu", "Ctrl Shift U", False),
    ("theme:system", N_("Тема: как в системе"), "layers", "", False),
    ("theme:light", N_("Тема: светлая"), "sun", "", False),
    ("theme:dark", N_("Тема: тёмная"), "moon", "", False),
    ("rename", N_("Переименовать этот чат"), "edit", "F2", True),
    ("delete", N_("Удалить этот чат"), "trash", "", True),
    ("stop-hub", N_("Остановить хаб и выйти"), "power", "", False),
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

    actions = [(name, tr(title), icon, key, needs_chat) for name, title, icon, key, needs_chat in ACTIONS]
    section(
        tr("Действия"),
        [
            {"id": f"action:{name}", "name": highlight(title, q), "icon": icon, "key": key, "note": ""}
            for name, title, icon, key, needs_chat in actions
            if (has_current or not needs_chat) and hit(title)
        ],
    )
    matched_chats = [c for c in chats if hit(c["title"])]
    section(
        tr("Чаты") if q else tr("Недавние чаты"),
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
        tr("Проекты"),
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
            tr("Модели"),
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
            tr("Бюджеты"),
            [
                {
                    "id": f"budget:{b['id']}",
                    "name": highlight(presenter.budget_title(b), q),
                    "icon": "wallet",
                    "note": presenter.budget_line(b).split(": ", 1)[-1],
                    "key": "",
                }
                for b in budgets
                if hit(presenter.budget_title(b), "бюджет")  # i18n: keep
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
            parts.append(tr("предупредить с {soft}", soft=presenter.money(soft)))
        if hard is not None:
            action = dict(presenter.hard_actions()).get(b.get("hard_action"), "")
            parts.append(tr("после лимита — {action}", action=action))
        cards.append(
            {
                "id": b["id"],
                "title": presenter.budget_title(b),
                "spent": presenter.money(spent),
                "limit": tr("из {limit}", limit=presenter.money(hard)) if hard is not None else tr("без лимита"),
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
    return [{"id": "total:", "name": tr("Все платные модели")}] + [
        {"id": f"provider:{p}", "name": presenter.provider_name(p)} for p in sorted(providers)
    ]
