"""Hub data -> the plain dicts the QML view shows. No Qt here, so all of it is easy to test.

Colours live inside the HTML, so rows are rendered again after a theme switch (the raw markdown is kept for that).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from common.i18n import N_, decimal, tr
from common.plain_errors import explain

from .. import palette, presenter
from ..render import Code, highlight_code, language_label, page, render_plain, render_segments


def segments(markdown: str) -> list[dict[str, str]]:
    """An answer as pieces for QML: prose HTML, and code blocks with their language, coloured HTML and raw text.
    Every piece has the same keys: the view keeps them in a ListModel, whose roles come from the first piece."""
    out = []
    for segment in render_segments(markdown) if markdown else []:
        if isinstance(segment, Code):
            spans = highlight_code(segment.code, segment.lang)
            html = f'<pre style="margin:0; font-family:{palette.MONO_CSS}; line-height:140%;">{spans}</pre>'
            out.append({"kind": "code", "lang": language_label(segment.lang), "html": html, "code": segment.code})
        else:
            out.append({"kind": "prose", "lang": "", "html": page(segment.html), "code": ""})
    return out


def thinking(text: str, elapsed: float | None, *, running: bool = False, open_: bool = False) -> dict[str, Any]:
    return {
        "text": text,
        "seconds": presenter.seconds(elapsed) if elapsed is not None else "",
        "running": running,
        "open": open_,
    }


def model_name(models: dict[str, dict], model_id: str | None) -> str:
    if not model_id:
        return tr("Модель")
    return models.get(model_id, {}).get("name") or model_id


# a chat's rights (D17) as its input's chip shows them (2026-10-28, the user's word): how often the model asks
CHAT_MODES = [
    ("read_only", N_("Только чтение"), N_("Модель только читает. Правки файлов предложит словами, команды не "
                                          "запускает.")),
    ("ask", N_("Спрашивать"), N_("Каждую правку файла и команду модель показывает карточкой и ждёт вашего ответа.")),
    ("sandbox", N_("Песочница"), N_("Правит без вопроса копию проекта (git worktree), потом вы смотрите изменения и "
                                    "применяете или отбрасываете. Нужен git-проект; Claude Code здесь спрашивает.")),
    ("trusted", N_("Доверенный"), N_("Правит файлы, запускает команды, ходит в сеть без вопроса — правки можно "
                                     "откатить. Спрашивает только разрушительное (удаление, git push), всё после "
                                     "чтения сайтов, писем и чужих окон, а сеть — если в чате проект «только "
                                     "локально».")),
]  # fmt: skip


def kept_text(usage: dict[str, dict[str, int]]) -> str:
    """What the hub keeps on disk now, by kind (D30): «снимки файлов 12 МБ · снимки экрана 40 МБ · …»."""
    names = [("snapshots", tr("снимки файлов")), ("screens", tr("снимки экрана")),
             ("terminals", tr("журналы терминалов")), ("files", tr("файлы из чатов")),
             ("pictures", tr("картинки"))]  # fmt: skip
    return " · ".join(f"{word} {mb((usage.get(kind) or {}).get('bytes', 0))}" for kind, word in names)


def mb(size: int) -> str:
    if size >= 1024**3:
        return tr("{size} ГБ", size=decimal(f"{size / 1024**3:.1f}"))
    return tr("{size} МБ", size=round(size / 1024**2))


def chat_mode_items(mode: str) -> list[dict[str, Any]]:
    return [{"section": tr("Права модели в этом чате — что она делает без вопроса"), "id": ""}] + [
        {"id": m, "name": tr(n), "note": tr(h), "icon": "shield", "selected": m == (mode or "ask")}
        for m, n, h in CHAT_MODES
    ]


# Strata's refusal vector (abliteration at runtime, 2026-10-28) for one chat or task: "" - as the model is set
REFUSAL = [("on", N_("Включено"), N_("Strata убирает у модели отказы отвечать (abliterated).")),
           ("off", N_("Выключено"), N_("Обычная модель, как её выпустили: на некоторые темы может "
                                       "отказаться."))]  # fmt: skip


def model_vector(models: dict[str, dict], model_id: str | None) -> bool | None:
    """How a model with Strata's refusal vector is set (True: on, the default); None when it has none to switch."""
    model = models.get(model_id or "") or {}
    return bool(model.get("refusal_vector", True)) if "refusal_vector" in (model.get("accepts") or []) else None


def refusal_name(models: dict[str, dict], model_id: str | None, chosen: str) -> str:
    """The chip: what this chat or task gets - its own choice, else the model's."""
    on = chosen == "on" if chosen else bool(model_vector(models, model_id))
    return tr("Снятие отказов: {state}", state=tr("вкл") if on else tr("выкл"))


def refusal_items(models: dict[str, dict], model_id: str | None, chosen: str, whose: str) -> list[dict[str, Any]]:
    """The chip's menu: as the model is set (and how that is now), on, off - each explained."""
    model = tr("включено") if model_vector(models, model_id) else tr("выключено")
    return [{"section": tr("Снятие отказов (abliterated) — для {whose}", whose=whose), "id": ""},
            {"id": "model", "name": tr("Как в настройках модели"), "note": tr("Сейчас там: {state}.", state=model),
             "icon": "sliders", "selected": not chosen}] + [
        {"id": a, "name": tr(n), "note": tr(h), "icon": "key", "selected": a == chosen}
        for a, n, h in REFUSAL]  # fmt: skip


def is_local(models: dict[str, dict], model_id: str | None) -> bool:
    model = models.get(model_id or "")
    return model.get("locality") == "local" if model else (model_id or "").startswith("llama-swap/")


def user_row(message: dict[str, Any]) -> dict[str, Any]:
    text, _ = presenter.split_blocks(message["content"])
    files = [{"name": b.get("name", ""), "picture": b["type"] == "image"}
             for b in message["content"] if b.get("type") in ("image", "file")]  # fmt: skip
    return {"key": f"m{message['id']}", "role": "user", "text": text, "html": page(render_plain(text)), "files": files}


# the hub's word to the model between its turns (D40, D44): a line in the chat, its text on a click
HUB_NOTES = {"start": N_("договор цели"), "continuation": N_("продолжение цели"),
             "budget_limit": N_("последний ход цели")}  # fmt: skip
GOAL_LOOP = re.compile(r'<goal_loop type="(\w+)">')
# why the strong model works a goal's turn (11.7)
WORKER_WHY = {"plan": N_("план"), "investigate": N_("поиск причины"), "fix": N_("правка после поиска причины"),
              "another_way": N_("другой путь после отката")}  # fmt: skip


def hub_row(message: dict[str, Any], models: dict[str, dict]) -> dict[str, Any]:
    text, _ = presenter.split_blocks(message["content"])
    found = GOAL_LOOP.search(text)
    what = tr(HUB_NOTES.get(found.group(1), N_("цель"))) if found else tr("напоминание")
    notes = []
    if found:
        missing = sum(1 for line in text.splitlines() if line.startswith("- ") and "HARD CONSTRAINTS" not in line)
        if "CHECK OF YOUR LAST TURN" in text and missing:
            notes.append(tr("не выполнено: {count}", count=missing))
        if "THE JUDGE SAYS" in text:
            notes.append(tr("слово проверяющей модели"))
        if "THE USER ADDED" in text:
            notes.append(tr("ваше уточнение"))
    worker = next((b["worker"] for b in message["content"] if b.get("worker")), None)
    if worker:  # a goal with two models (11.7): who works this turn, and why the strong one (D11)
        why = WORKER_WHY.get(worker.get("why") or "")
        notes.append(tr("ход ведёт «{name}»", name=model_name(models, worker.get("model_id")))
                     + (f" — {tr(why)}" if why else ""))  # fmt: skip
    return {"key": f"m{message['id']}", "role": "hub", "text": text, "html": "",
            "title": tr("Хаб → модели: {what}", what=what), "note": " · ".join(notes), "goal": bool(found)}  # fmt: skip


def assistant_row(message: dict[str, Any], models: dict[str, dict]) -> dict[str, Any]:
    text, reasoning = presenter.split_blocks(message["content"])
    usage = message.get("usage") or {}
    think = (usage.get("stats") or {}).get("think_s")
    return {
        "key": f"m{message['id']}",
        "role": "assistant",
        "author": model_name(models, message.get("model_id")),
        "local": is_local(models, message.get("model_id")),
        "markdown": text,
        "segments": segments(text),
        "thinking": thinking(reasoning, think) if reasoning else None,
        "footer": presenter.answer_meta(usage),
        "streaming": False,
        "notice": fallback_note(usage.get("fallback")),
        "tools": tools_of(message),
    }


def fallback_note(fallback: dict[str, Any] | None, answering: str | None = None) -> str:
    """The line over an answer the spare model gave (D39): which model did not answer, and why. `answering`: the
    spare model's name while it answers now (the notice as the run goes on)."""
    if not fallback:
        return ""
    reason = " ".join(str(fallback.get("reason") or "").split())
    failed = fallback.get("from_name") or fallback.get("from")
    if answering is None:
        note = tr("Ответила запасная модель: «{failed}» не ответила", failed=failed)
    else:
        note = tr("Отвечает запасная «{name}»: «{failed}» не ответила", name=answering, failed=failed)
    return note + (f" — {reason[:160]}" if reason else "")


TOOL_ICONS = {
    "list_files": "folder",
    "read_file": "file",
    "search_code": "search",
    "find_symbol": "search",
    "file_outline": "outline",
    "git_status": "branch",
    "git_diff": "diff",
    "git_log": "commit",
    "terminal_output": "terminal",
    "list_terminals": "terminal",
    "read_terminal": "terminal",
    "list_processes": "cpu",
    "get_ecosystem_map": "layers",
    "get_project_card": "file",
    "list_relations": "layers",
    "get_events": "activity",
    "write_file": "edit",
    "edit_file": "edit",
    "run_command": "terminal",
    "web_fetch": "cloud",
    "delegate_task": "agent",
    "delegate_tasks": "agent",
    "generate_image": "image",  # 29.6
    "generate_video": "image",  # 29.8б
    # Claude Code's own tools (stage 4)
    "Read": "file",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Write": "edit",
    "NotebookEdit": "edit",
    "Bash": "terminal",
    "PowerShell": "terminal",
    "Glob": "search",
    "Grep": "search",
    "LS": "folder",
    "WebFetch": "cloud",
    "WebSearch": "cloud",
    "TodoWrite": "check",
    "Task": "agent",
    "Agent": "agent",
}
HUB_MCP = "mcp__localchat__"  # the hub's tools as Claude Code calls them
TOOL_OUTPUT_SHOWN = 6000  # characters of a result shown when opened


def short_path(path: str) -> str:
    """An absolute path by its last parts (Claude Code names files by absolute path); a relative one as it is."""
    norm = path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    absolute = norm.startswith("/") or (len(norm) > 1 and norm[1] == ":")
    return "/".join(parts[-3:]) if absolute and len(parts) > 3 else norm


def stats_rows(found: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Per model, what the hub saw (D11): requests and speed, then errors, money and agent tasks."""
    out = []
    for m in found:
        speed = [f"{m['requests']} {presenter.plural(m['requests'], 'запрос', 'запроса', 'запросов')}"]
        if m.get("tok_s"):
            speed.append(tr("{speed:g} т/с", speed=m["tok_s"]))
        if m.get("ttft_s") is not None:
            speed.append(tr("первый токен {seconds:g} с", seconds=m["ttft_s"]))
        if m.get("answer_s") is not None:
            speed.append(tr("ответ {seconds:g} с", seconds=m["answer_s"]))
        more = [tr("ошибки {percent:.0f}%", percent=m["error_rate"] * 100) if m.get("errors") else tr("без ошибок")]
        if m.get("cost_usd"):
            more.append(presenter.money(m["cost_usd"]))
        elif m.get("estimate_usd"):
            more.append(tr("подписка, оценка {cost}", cost=presenter.money(m["estimate_usd"])))
        if m.get("runs"):
            more.append(tr("задачи агентов {done}/{total}", done=m["runs_done"], total=m["runs"]))
        out.append({"id": m["model_id"], "name": names.get(m["model_id"], m["model_id"]), "speed": " · ".join(speed),
                    "more": " · ".join(more), "warn": (m.get("error_rate") or 0) >= 0.2})  # fmt: skip
    return out


def forecast_text(spending: dict[str, Any] | None) -> str:
    """The month's money in one line (D12): so far, the pace, where it ends."""
    if not spending:
        return ""
    text = tr("В этом месяце {spent}", spent=presenter.money(spending["month_to_date_usd"]))
    if spending.get("daily_avg_7d_usd"):
        text += tr("; последние 7 дней — {daily} в день, к концу месяца ≈ {forecast}",
                   daily=presenter.money(spending["daily_avg_7d_usd"]),
                   forecast=presenter.money(spending["forecast_month_usd"]))  # fmt: skip
    if spending.get("subscription_estimate_usd"):
        text += tr(". Claude Code по подписке — оценка {estimate}",
                   estimate=presenter.money(spending["subscription_estimate_usd"]))  # fmt: skip
    return text + "."


def tool_label(name: str, args: Any) -> str:
    """What a tool call does, in a few words: 'Читаю hub/chat.py', 'Ищу «budget»'."""
    a = args if isinstance(args, dict) else {}
    name = name.removeprefix(HUB_MCP)
    path = a.get("path") if isinstance(a.get("path"), str) else ""
    file = short_path(str(a.get("file_path") or a.get("notebook_path") or ""))
    match name:
        case "Read":
            return tr("Читаю {path}", path=file)
        case "Edit" | "MultiEdit" | "NotebookEdit":
            return tr("Правлю {path}", path=file)
        case "Write":
            return tr("Пишу {path}", path=file)
        case "Bash" | "PowerShell" | "run_command":
            command = " ".join(str(a.get("command", "")).split())
            return tr("Выполняю {command}", command=command[:80] + ("…" if len(command) > 80 else ""))
        case "Glob":
            return tr("Ищу файлы {pattern}", pattern=a.get("pattern", ""))
        case "Grep":
            return tr("Ищу «{text}»", text=a.get("pattern", ""))
        case "LS":
            return tr("Смотрю папку {path}", path=short_path(path))
        case "WebFetch" | "web_fetch":
            return tr("Открываю {url}", url=a.get("url", ""))
        case "generate_image" | "generate_video":  # 29.6, 29.8б
            prompt = " ".join(str(a.get("prompt", "")).split())
            prompt = prompt[:60] + ("…" if len(prompt) > 60 else "")
            return tr("Снимаю видео «{prompt}»", prompt=prompt) if name == "generate_video" else tr(
                "Рисую «{prompt}»", prompt=prompt)  # fmt: skip
        case "WebSearch":
            return tr("Ищу в сети «{query}»", query=a.get("query", ""))
        case "TodoWrite":
            return tr("Обновляю список задач")
        case "delegate_task":
            return tr("Поручаю агенту «{agent}»: {task}", agent=a.get("agent", ""),
                      task=" ".join(str(a.get("task", "")).split())[:80])  # fmt: skip
        case "delegate_tasks":  # a batch (16.1, D50)
            given = [t for t in a.get("tasks") or [] if isinstance(t, dict)]
            names = ", ".join(f"«{n}»" for n in dict.fromkeys(str(t.get("agent_profile", "")) for t in given))
            head = tr("Поручаю {count} {tasks}", count=len(given),
                      tasks=presenter.plural(len(given), "задачу", "задачи", "задач"))  # fmt: skip
            return f"{head}: {names}" if names else head
        case "Task" | "Agent":  # Claude Code's subagent: its steps show when the call is opened (21.3)
            return tr("Поручаю подагенту: {task}", task=a.get("description", ""))
        case "edit_file":
            return tr("Правлю {path}", path=path)
        case "write_file":
            return tr("Пишу {path}", path=path)
        case "get_ecosystem_map":
            return tr("Смотрю карту проектов")
        case "get_project_card":
            return tr("Читаю карточку «{project}»", project=a.get("project", ""))
        case "list_relations":
            return tr("Смотрю связи проектов")
        case "find_symbol":
            return tr("Ищу определение {name}", name=a.get("name", ""))
        case "file_outline":
            return tr("Смотрю структуру {path}", path=path)
        case "git_log":
            return tr("Смотрю историю git")
        case "list_terminals":
            return tr("Смотрю терминалы")
        case "read_terminal":
            return tr("Читаю терминал «{terminal}»", terminal=a.get("terminal", ""))
        case "get_events":
            return tr("Смотрю, что происходило")
        case "read_file":
            start = a.get("start_line")
            if isinstance(start, int) and start > 1:
                return tr("Читаю {path} со строки {line}", path=path, line=start)
            return tr("Читаю {path}", path=path)
        case "list_files":
            return tr("Смотрю папку {path}", path=path) if path else tr("Смотрю файлы проекта")
        case "search_code":
            return tr("Ищу «{text}»", text=a.get("query", ""))
        case "git_status":
            return tr("Смотрю состояние git")
        case "git_diff":
            return tr("Смотрю изменения в {path}", path=path) if path else tr("Смотрю изменения")
        case "terminal_output":
            return (
                tr("Читаю терминал «{terminal}»", terminal=a["terminal"])
                if a.get("terminal")
                else tr("Смотрю терминалы")
            )
        case "list_processes":
            return tr("Смотрю процессы")
    return name


def tools_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls of an answer, waiting for their results."""
    return [
        {
            "id": b["id"],
            "icon": TOOL_ICONS.get(b["name"].removeprefix(HUB_MCP), "dots"),
            "label": tool_label(b["name"], b.get("input")),
            "project": (b.get("input") or {}).get("project", "") if isinstance(b.get("input"), dict) else "",
            "state": "running",
            "summary": "",
            "output": "",
            "open": False,
        }
        for b in message.get("content", [])
        if b.get("type") == "tool_call"
    ]


def with_results(tools: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls with their results (a message of role "tool" answers them by id)."""
    by_id = {r["id"]: r for r in results if r.get("type") == "tool_result"}
    out = []
    for tool in tools:
        result = by_id.get(tool["id"])
        if result is None:
            out.append(tool)
            continue
        output = result.get("output", "")
        first = output.strip().splitlines()[0] if output.strip() else ""
        kind = str(result.get("media_type", "")) if result.get("blob") else ""
        video = result["blob"] if kind == "image/webp" else ""  # a video plays from its file (29.8б)
        picture = f"image://pictures/blob/{result['blob']}" if kind.startswith("image/") and not video else ""
        if result.get("sub"):  # what a subagent did (21.3), then what it answered
            steps = [line for line in map(sub_line, result["sub"]) if line.strip()]
            output = "\n".join(steps) + "\n\n" + output
        out.append(
            {
                **tool,
                "state": "error" if result.get("error") else "ok",
                "summary": first if len(first) <= 120 else first[:119] + "…",
                "output": output[:TOOL_OUTPUT_SHOWN] + ("\n…" if len(output) > TOOL_OUTPUT_SHOWN else ""),
                "picture": picture,
                "video": video,
            }
        )
    return out


def sub_line(step: dict[str, Any]) -> str:
    """A subagent's step (21.3) as a line under the call that started it: a call as the chat says it, its result's
    error, its text."""
    match step.get("type"):
        case "call":
            return "→ " + tool_label(str(step.get("name", "")), step.get("input"))
        case "result":
            return "  " + tr("ошибка: {text}", text=step.get("output", "")[:200]) if step.get("error") else ""
        case "text":
            return step.get("text", "")
    return ""


def with_subagent_step(rows: list[dict[str, Any]], parent: str, step: dict[str, Any]) -> dict[int, list[dict]]:
    """A subagent's step as it comes (21.3): the running call that started it shows it as its last line and in its
    output. {row index: its calls}; {} when no row has that call."""
    line = sub_line(step)
    if not line.strip():
        return {}
    for index in range(len(rows) - 1, -1, -1):
        tools = rows[index].get("tools") or []
        if any(tool["id"] == parent and tool["state"] == "running" for tool in tools):
            changed = [{**t, "summary": line.strip()[:120],
                        "output": (t["output"] + "\n" + line).strip()[-TOOL_OUTPUT_SHOWN:]}
                       if t["id"] == parent else t for t in tools]  # fmt: skip
            return {index: changed}
    return {}


def answered(rows: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """The rows whose calls these results answer, matched by call id from the newest row: {row index: its calls with
    the results}. Not just the last row with calls: Claude Code stores parallel calls as separate messages."""
    changed: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        if result.get("type") != "tool_result":
            continue
        for index in range(len(rows) - 1, -1, -1):
            tools = changed.get(index) or rows[index].get("tools") or []
            if any(tool["id"] == result.get("id") for tool in tools):
                changed[index] = with_results(tools, [result])
                break
    return changed


CONTEXT_ORDER = ("map", "project", "file", "live", "tools")


def context_label(key: str, block: dict[str, Any]) -> str:
    kind, _, rest = key.partition(":")
    project = block.get("project", "")
    match kind:
        case "map":
            return tr("Карта проектов")
        case "project":
            return tr("Проект «{project}»", project=project)
        case "file":
            return tr("Файл {project}/{path}", project=project, path=rest.partition(":")[2])
        case "live":
            return tr("Сейчас в «{project}»", project=project)
        case "tools":
            return tr("Инструменты и правила")
    return key


def context_rows(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The context a chat holds (stored system messages, D22): the latest version of each block, in layer order,
    with its size and how many times it was sent."""
    latest: dict[str, dict[str, Any]] = {}
    sent: dict[str, int] = {}
    for message in messages:
        if message["role"] != "system":
            continue
        for block in message["content"]:
            if block.get("type") != "context" or block.get("key") == "header":
                continue
            latest[block["key"]] = block
            sent[block["key"]] = sent.get(block["key"], 0) + 1
    rows = [
        {
            "key": key,
            "label": context_label(key, block),
            "tokens": (len(block["text"]) + 3) // 4,
            "text": block["text"],
            "sent": sent[key],
            "local": not block.get("cloud_ok", True),
            "open": False,
        }
        for key, block in latest.items()
    ]
    return sorted(rows, key=lambda r: (CONTEXT_ORDER.index(r["key"].partition(":")[0])
                                       if r["key"].partition(":")[0] in CONTEXT_ORDER else 9, r["label"]))  # fmt: skip


def context_total(messages: list[dict[str, Any]]) -> int:
    """Tokens of all the context stored in the chat (each version stays in the conversation, D14)."""
    return sum(
        (len(b.get("text", "")) + 3) // 4
        for m in messages
        if m["role"] == "system"
        for b in m["content"]
        if b.get("type") == "context"
    )


def live_row(model_id: str, models: dict[str, dict]) -> dict[str, Any]:
    return {
        "key": "live",
        "role": "assistant",
        "author": model_name(models, model_id),
        "local": is_local(models, model_id),
        "markdown": "",
        "segments": [],
        "thinking": None,
        "footer": "",
        "streaming": True,
        "notice": "",
        "tools": [],
    }


def error_row(key: str, text: str) -> dict[str, Any]:
    """An error card: the usual ones get a title and what to do in plain words (10.6), the technical text below."""
    plain = explain(text)
    return {"key": key, "role": "error", "text": text, "html": page(render_plain(text)),
            "title": plain[0] if plain else tr("Ошибка"), "hint": plain[1] if plain else ""}  # fmt: skip


def rows_for(messages: list[dict[str, Any]], models: dict[str, dict]) -> list[dict[str, Any]]:
    """A conversation's rows. Tool results are not rows of their own: they join the answer that asked for them."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":  # the hub's context for the model (D22): the inspector shows it, not the chat
            continue
        if m["role"] == "tool":
            for index, tools in answered(out, m["content"]).items():
                out[index]["tools"] = tools
            continue
        out.append(message_row(m, models))
    return out


def message_row(message: dict[str, Any], models: dict[str, dict]) -> dict[str, Any]:
    """A stored user or assistant message as its row; the hub's notes to the model are rows of their own."""
    if message["role"] == "user":
        return hub_row(message, models) if any(b.get("hub") for b in message["content"]) else user_row(message)
    return assistant_row(message, models)


def restyled(row: dict[str, Any]) -> dict[str, Any]:
    """The same row with its HTML rendered in the current theme."""
    if row["role"] == "assistant":
        return {**row, "segments": segments(row["markdown"])}
    if row["role"] == "hub":
        return row
    return {**row, "html": page(render_plain(row["text"]))}


# --- the sidebar ---


def day_section(updated_at: str, now: datetime | None = None) -> str:
    """'Сегодня' / 'Вчера' / 'Последние 7 дней' / 'Ранее', by the local calendar."""
    now = (now or datetime.now()).astimezone()
    try:
        when = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(now.tzinfo)
    except ValueError:
        return tr("Ранее")
    days = (now.date() - when.date()).days
    if days <= 0:
        return tr("Сегодня")
    if days == 1:
        return tr("Вчера")
    if days < 7:
        return tr("Последние 7 дней")
    return tr("Ранее")


def chat_rows(
    conversations: list[dict[str, Any]], busy: set[str], models: dict[str, dict], now: datetime | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "title": c.get("title") or tr("Новый чат"),
            "section": day_section(c.get("updated_at", ""), now),
            "busy": c["id"] in busy,
            "cloud": bool(c.get("last_model_id")) and not is_local(models, c.get("last_model_id")),
        }
        for c in conversations
    ]


# --- the model picker ---


def picker_items(
    models: list[dict[str, Any]],
    loaded: set[str],
    chosen: str | None,
    advice: dict[str, Any] | None = None,
    *,
    subscription: str = "",
) -> list[dict[str, Any]]:
    """Sections and models for the picker: the hub's advice for this chat first (D11), local ones (which is in VRAM
    now), then cloud ones with prices; a model the chat may not use says why and cannot be picked. `subscription`:
    Claude Code's limits in a line (20.3), said at its models."""
    items: list[dict[str, Any]] = []
    options = {o["model_id"]: o for o in (advice or {}).get("options") or []}
    best = options.get((advice or {}).get("recommended") or "")
    named = {m["id"]: m for m in models}
    if best is not None and best["model_id"] in named:
        items.append({"section": tr("Рекомендуется для этого чата"), "id": ""})
        model = named[best["model_id"]]
        items.append({"section": "", "id": model["id"], "name": model["name"], "icon": "sparkle",
                      "note": " · ".join(best["reasons"][:3]), "selected": model["id"] == chosen,
                      "cloud": model["locality"] != "local"})  # fmt: skip
    for local in (True, False):
        # a model the user hid stays out of the picker (D39) - unless it is the chosen one
        group = [m for m in presenter.sorted_models(models)
                 if (m.get("locality") == "local") == local and (not m.get("hidden") or m["id"] == chosen)]  # fmt: skip
        if not group:
            continue
        items.append({"section": tr("Локальные · на этой видеокарте") if local else tr("Облако"), "id": ""})
        for m in group:
            ref = m.get("model_ref") or m["id"].partition("/")[2]
            caps = m.get("capabilities") or {}
            if local:
                note = tr("загружена в видеопамять") if ref in loaded else tr("загрузится при отправке")
            elif caps.get("subscription"):
                note = tr("подписка Claude Code · работает в папке проекта чата")
                if subscription:
                    note += " · " + subscription
            else:
                prices = m.get("prices") or {}
                note = tr(
                    "${input:g} / ${output:g} за 1M", input=prices.get("input", 0), output=prices.get("output", 0)
                )
                if caps.get("context"):
                    note += tr(" · контекст {size}K", size=caps["context"] // 1000)
            blocked = (options.get(m["id"]) or {}).get("blocked")
            if blocked:
                note = tr("недоступна: {reason}", reason=blocked)
            items.append(
                {
                    "section": "",
                    "id": m["id"],
                    "name": m["name"],
                    "note": note,
                    "enabled": not blocked,
                    "cloud": not local,
                    "loaded": local and ref in loaded,
                    "selected": m["id"] == chosen,
                }
            )
    return items


# --- the status bar ---


def status_parts(status: dict[str, Any] | None) -> dict[str, Any]:
    vram = presenter.vram_meter(status)
    commit = presenter.commit_meter(status)
    return {
        "local": presenter.local_model(status) if status else "",
        "vram": vram[0] if vram else 0.0,
        "vramText": vram[1] if vram else "—",
        "commit": commit[0] if commit else 0.0,
        "commitText": commit[1] if commit else "—",
        "commitWarn": presenter.commit_is_tight(status),
        "gpu": presenter.gpu_load(status),
        "details": presenter.status_text(status),
        "loaded": [r["model"] for r in ((status or {}).get("local") or {}).get("running", []) if r.get("model")],
    }
