"""The event log as the feed shows it: one readable line (and a detail) per event that means something to a person.
No Qt here.

The hub keeps every stored event (D29); the feed shows those a person cares about later - projects, terminals,
ports, git, answers in chats, budgets, the hub itself - and leaves out the plumbing (single messages, titles,
catalog syncs). Each item belongs to a group the feed can be filtered by.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common.i18n import N_, tr

from .. import presenter
from . import links_text

GROUPS = [
    ("all", N_("Всё")),
    ("projects", N_("Проекты")),
    ("terminals", N_("Терминалы")),
    ("processes", N_("Процессы")),
    ("git", "Git"),
    ("chats", N_("Чаты")),
    ("budgets", N_("Бюджеты")),
    ("agent", N_("Агент")),
]
MONTHS = [
    N_("января"),
    N_("февраля"),
    N_("марта"),
    N_("апреля"),
    N_("мая"),
    N_("июня"),
    N_("июля"),
    N_("августа"),
    N_("сентября"),
    N_("октября"),
    N_("ноября"),
    N_("декабря"),
]


def _local(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _kind(kind: str) -> str:
    """A link's kind as the window names it."""
    return tr(links_text.KIND_LABELS.get(kind, N_("связь")))


def day_label(ts: str, now: datetime | None = None) -> str:
    """'Сегодня', 'Вчера' or '25 октября' (with the year when it is not this one)."""
    when = _local(ts)
    if when is None:
        return ""
    now = (now or datetime.now()).astimezone()
    days = (now.date() - when.date()).days
    if days <= 0:
        return tr("Сегодня")
    if days == 1:
        return tr("Вчера")
    label = f"{when.day} {tr(MONTHS[when.month - 1])}"
    return label if when.year == now.year else f"{label} {when.year}"


def describe(event: dict[str, Any], *, projects: dict[str, str], chats: dict[str, str]) -> dict[str, Any] | None:
    """{group, icon, level (""/"warn"/"error"), title, detail} for an event worth showing; None otherwise."""
    kind, p = event.get("type", ""), event.get("payload") or {}

    def item(group: str, icon: str, title: str, detail: str = "", level: str = "") -> dict[str, Any]:
        return {"group": group, "icon": icon, "title": title, "detail": detail, "level": level}

    chat = chats.get(p.get("conversation_id", ""), "") or tr("без названия")
    match kind:
        case "project.added":
            info = p.get("project") or {}
            return item("projects", "folder-plus", tr("Добавлен проект «{name}»", name=info.get("name", "")),
                        info.get("path", ""))  # fmt: skip
        case "project.changed":
            info = p.get("project") or {}
            return item("projects", "folder", tr("Проект «{name}» изменён", name=info.get("name", "")))
        case "project.removed":
            name = projects.get(p.get("id", ""), "") or p.get("name", "")
            return item("projects", "folder", tr("Проект «{name}» убран из списка", name=name) if name
                        else tr("Проект убран из списка"))  # fmt: skip
        case "project.moved":  # its folder renamed or moved on the disk: the hub follows it (2026-10-28)
            return item("projects", "folder", tr("Папка проекта «{name}» переименована — путь обновлён",
                                                 name=p.get("name", "")),
                        f"{p.get('from', '')} → {p.get('to', '')}")  # fmt: skip
        case "project.missing":
            return item("projects", "folder", tr("Папка проекта «{name}» пропала", name=p.get("name", "")),
                        p.get("path", ""), "warn")  # fmt: skip
        case "project.found":
            return item("projects", "folder", tr("Папка проекта «{name}» снова на месте", name=p.get("name", "")),
                        p.get("path", ""))  # fmt: skip
        case "terminal.started":
            info = p.get("terminal") or {}
            return item("terminals", "terminal", tr("Запущен терминал: {title}", title=info.get("title", "")),
                        info.get("cwd", ""))  # fmt: skip
        case "terminal.exited":
            title, code = p.get("title", ""), p.get("exit_code")
            if p.get("stopped"):
                return item("terminals", "terminal", tr("Терминал «{title}» остановлен", title=title))
            failed = code not in (0, None)
            if code is not None:
                text = tr("Терминал «{title}» завершился с кодом {code}", title=title, code=code)
            else:
                text = tr("Терминал «{title}» завершился", title=title)
            return item("terminals", "terminal", text, level="error" if failed else "")
        case "link.confirmed":
            kind = _kind(p.get("kind", ""))
            note = f" ({p['description']})" if p.get("description") else ""
            return item("projects", "layers", tr("Связь: {a} → {b}, {kind}{note}", a=p.get("a_name"), b=p.get("b_name"),
                                                 kind=kind, note=note))  # fmt: skip
        case "tools.fetched":  # ripgrep, fetched by the hub when there was none (2026-10-28)
            what = f"{p.get('tool', '')} {p.get('version', '')}".strip()
            return item("projects", "search", tr("Поставлен {what} — поиск по проектам теперь быстрый", what=what),
                        p.get("path", ""))  # fmt: skip
        case "links.proposed":  # the hub looked again by itself after a change (2026-10-28)
            found = p.get("links") or []
            shown = "; ".join(f"{k.get('a')} → {k.get('b')}, {_kind(k.get('kind', ''))}"
                              for k in found[:3]) + ("…" if len(found) > 3 else "")  # fmt: skip
            return item("projects", "layers", tr("Хаб нашёл возможные связи проектов: {count}",
                                                 count=p.get("added", len(found))),
                        tr("{links} — проверьте в обзоре проекта", links=shown))  # fmt: skip
        case "programs.found":
            found = p.get("programs") or []
            names = ", ".join(f"«{f.get('name', '')}»" for f in found[:3]) + ("…" if len(found) > 3 else "")
            title = tr("Найдена новая программа") if len(found) == 1 else tr("Найдено новых программ: {count}",
                                                                             count=len(found))  # fmt: skip
            return item("agent", "shield", f"{title}: {names}", tr("агенту — только после вашего разрешения"))
        case "program.decided":
            words = {"allow": tr("разрешена агенту"), "deny": tr("запрещена агенту")}
            what = words.get(p.get("decision") or "", tr("снова не решено (агенту нельзя)"))
            return item("agent", "shield", tr("Программа «{name}»: {what}", name=p.get("name", ""), what=what))
        case "run.started":
            run = p.get("run") or {}
            name = run.get("profile_name", "")
            title = tr("Агент «{name}» взял задачу (продолжает после перезапуска)", name=name) if p.get("resumed") \
                else tr("Агент «{name}» взял задачу", name=name)  # fmt: skip
            return item("chats", "agent", title, " ".join(str(run.get("task", "")).split())[:120])
        case "run.finished":
            run = p.get("run") or {}
            words = {"done": tr("готово"), "error": tr("ошибка"), "cancelled": tr("отменён")}
            level = "error" if run.get("status") == "error" else ""
            title = tr("Агент «{name}»: {status}", name=run.get("profile_name", ""),
                       status=words.get(run.get("status", ""), run.get("status", "")))  # fmt: skip
            return item("chats", "agent", title, run.get("detail") or "", level)
        case "file.changed" | "file.reverted":
            change = p.get("change") or {}
            path = change.get("path", "")
            title = tr("Откачена правка {path}", path=path) if kind == "file.reverted" else \
                tr("Модель изменила {path}", path=path)  # fmt: skip
            chat = tr("чат «{name}»", name=change["chat"]) if change.get("chat") else ""
            return item("projects", "edit", title, chat)
        case "card.drafted":
            return item("projects", "edit", tr("Черновик карточки «{name}» готов — проверьте и подтвердите",
                                               name=p.get("name", "")))  # fmt: skip
        case "card.confirmed":
            name, version = p.get("name", ""), p.get("version")
            if p.get("restored_from"):
                return item("projects", "check", tr("Карточка «{name}» подтверждена, версия {version} (возвращена "
                                                    "версия {restored})", name=name, version=version,
                                                    restored=p["restored_from"]))  # fmt: skip
            return item("projects", "check", tr("Карточка «{name}» подтверждена, версия {version}", name=name,
                                                version=version))  # fmt: skip
        case "card.failed":
            return item("projects", "edit", tr("Черновик карточки не составлен"), p.get("detail", ""), "error")
        case "process.stopped":
            return item("processes", "power", tr("Завершён {name} (PID {pid})", name=p.get("name", ""),
                                                 pid=p.get("pid", "")), p.get("cmdline", ""))  # fmt: skip
        case "port.opened":
            return item("processes", "cpu", tr("Открыт порт :{port} — {name}", port=p.get("port"),
                                               name=p.get("name", "")), p.get("args", ""))  # fmt: skip
        case "port.closed":
            return item("processes", "cpu", tr("Закрыт порт :{port} ({name})", port=p.get("port"),
                                               name=p.get("name", "")))  # fmt: skip
        case "git.head":
            sha, subject, branch = p.get("sha", ""), p.get("subject", ""), p.get("branch", "")
            if p.get("kind") == "branch":
                return item("git", "branch", tr("Ветка {old} → {branch}", old=p.get("from", ""), branch=branch),
                            f"{sha} {subject}".strip())  # fmt: skip
            if p.get("kind") == "commit":
                return item("git", "commit", tr("Коммит в {branch}: {subject}", branch=branch, subject=subject),
                            f"{sha} · {p.get('author', '')}")  # fmt: skip
            return item("git", "commit", tr("{branch} теперь на {sha}: {subject}", branch=branch, sha=sha,
                                            subject=subject))  # fmt: skip
        case "chat.done":
            cost = p.get("cost_usd") or 0
            tokens = (p.get("usage") or {}).get("output_tokens") or 0
            parts = [tr("{tokens} ток. ответа", tokens=presenter.tokens_short(tokens)) if tokens else "",
                     presenter.money(cost) if cost else tr("бесплатно")]  # fmt: skip
            title = tr("Ответ в чате «{chat}» — остановлен", chat=chat) if p.get("cancelled") else \
                tr("Ответ в чате «{chat}»", chat=chat)  # fmt: skip
            return item("chats", "chat", title, " · ".join(x for x in parts if x))
        case "chat.error":
            title = tr("Отказ модели в чате «{chat}»", chat=chat) if p.get("refusal") else \
                tr("Ошибка в чате «{chat}»", chat=chat)  # fmt: skip
            return item("chats", "chat", title, p.get("detail", ""), "error")
        case "budget.warning" | "budget.exceeded":
            budget = p.get("budget") or {}
            level = "warn" if kind == "budget.warning" else "error"
            return item("budgets", "wallet", presenter.budget_event_text(kind.removeprefix("budget."), budget),
                        level=level)  # fmt: skip
        case "budget.overridden":
            return item("budgets", "wallet", tr("Запрос отправлен сверх бюджета (с подтверждением)"), level="warn")
        case "budget.changed":
            return item("budgets", "wallet", tr("Бюджеты изменены"))
        case "secrets.changed":
            if p.get("title"):
                said = tr("Подключение «{title}» задано", title=p["title"]) if p.get("set") else \
                    tr("Подключение «{title}» удалено", title=p["title"])  # fmt: skip
            else:
                said = tr("Подключение API задано") if p.get("set") else tr("Подключение API удалено")
            return item("all", "key", said)
        case "connections.password":
            return item("all", "lock", tr("Пароль подключений изменён") if p.get("changed")
                        else tr("Пароль подключений задан"))  # fmt: skip
        case "connections.denied":
            wait = tr("следующая попытка через {wait} с", wait=p["wait"]) if p.get("wait") else ""
            return item("all", "lock", tr("Неверный пароль подключений"), wait, "warn")
        case "telegram.linked":
            return item("all", "chat", tr("Telegram-чат привязан: подтверждения приходят и на телефон"))
        case "telegram.unlinked":
            return item("all", "chat", tr("Telegram-чат отвязан"))
        case "autostart.changed":
            login = tr("хаб запускается при входе в Windows") if p.get("login") else tr("без автозапуска при входе")
            swap = tr("llama-swap запускает хаб") if p.get("llama_swap") else tr("llama-swap — вручную")
            return item("all", "power", tr("Запуск изменён"), f"{login}; {swap}")
        case "editor.command":
            code = p.get("exit_code")
            failed = code not in (0, None)
            said = tr("код {code}", code=code) if code is not None else ""
            detail = " · ".join(x for x in (said, p.get("terminal", "")) if x)
            return item("terminals", "terminal", f"VS Code: {p.get('command', '')}", detail, "error" if failed else "")
        case "llama_swap.started":
            detail = "" if p.get("ready") else tr("ещё не отвечает — подробности в logs/llama-swap.out.log")
            return item("all", "cpu", tr("Хаб запустил llama-swap"), detail, "" if p.get("ready") else "warn")
        case "hub.started":
            return item("all", "layers", tr("Хаб запущен"), tr("версия {version}", version=p.get("version", "")))
        case "hub.stopping":
            return item("all", "power", tr("Хаб остановлен"))
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
