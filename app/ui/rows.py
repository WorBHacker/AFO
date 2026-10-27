"""Hub data -> the plain dicts the QML view shows. No Qt here, so all of it is easy to test.

Colours live inside the HTML, so rows are rendered again after a theme switch (the raw markdown is kept for that).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
        return "Модель"
    return models.get(model_id, {}).get("name") or model_id


def is_local(models: dict[str, dict], model_id: str | None) -> bool:
    model = models.get(model_id or "")
    return model.get("locality") == "local" if model else (model_id or "").startswith("llama-swap/")


def user_row(message: dict[str, Any]) -> dict[str, Any]:
    text, _ = presenter.split_blocks(message["content"])
    return {"key": f"m{message['id']}", "role": "user", "text": text, "html": page(render_plain(text))}


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
        "notice": "",
        "tools": tools_of(message),
    }


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
            speed.append(f"{m['tok_s']:g} т/с")
        if m.get("ttft_s") is not None:
            speed.append(f"первый токен {m['ttft_s']:g} с")
        if m.get("answer_s") is not None:
            speed.append(f"ответ {m['answer_s']:g} с")
        more = [f"ошибки {m['error_rate'] * 100:.0f}%" if m.get("errors") else "без ошибок"]
        if m.get("cost_usd"):
            more.append(presenter.money(m["cost_usd"]))
        elif m.get("estimate_usd"):
            more.append(f"подписка, оценка {presenter.money(m['estimate_usd'])}")
        if m.get("runs"):
            more.append(f"задачи агентов {m['runs_done']}/{m['runs']}")
        out.append({"id": m["model_id"], "name": names.get(m["model_id"], m["model_id"]), "speed": " · ".join(speed),
                    "more": " · ".join(more), "warn": (m.get("error_rate") or 0) >= 0.2})  # fmt: skip
    return out


def forecast_text(spending: dict[str, Any] | None) -> str:
    """The month's money in one line (D12): so far, the pace, where it ends."""
    if not spending:
        return ""
    text = f"В этом месяце {presenter.money(spending['month_to_date_usd'])}"
    if spending.get("daily_avg_7d_usd"):
        text += (f"; последние 7 дней — {presenter.money(spending['daily_avg_7d_usd'])} в день, к концу месяца "
                 f"≈ {presenter.money(spending['forecast_month_usd'])}")  # fmt: skip
    if spending.get("subscription_estimate_usd"):
        text += f". Claude Code по подписке — оценка {presenter.money(spending['subscription_estimate_usd'])}"
    return text + "."


def tool_label(name: str, args: Any) -> str:
    """What a tool call does, in a few words: 'Читаю hub/chat.py', 'Ищу «budget»'."""
    a = args if isinstance(args, dict) else {}
    name = name.removeprefix(HUB_MCP)
    path = a.get("path") if isinstance(a.get("path"), str) else ""
    file = short_path(str(a.get("file_path") or a.get("notebook_path") or ""))
    match name:
        case "Read":
            return f"Читаю {file}"
        case "Edit" | "MultiEdit" | "NotebookEdit":
            return f"Правлю {file}"
        case "Write":
            return f"Пишу {file}"
        case "Bash" | "PowerShell" | "run_command":
            command = " ".join(str(a.get("command", "")).split())
            return f"Выполняю {command[:80]}" + ("…" if len(command) > 80 else "")
        case "Glob":
            return f"Ищу файлы {a.get('pattern', '')}"
        case "Grep":
            return f"Ищу «{a.get('pattern', '')}»"
        case "LS":
            return f"Смотрю папку {short_path(path)}"
        case "WebFetch" | "web_fetch":
            return f"Открываю {a.get('url', '')}"
        case "WebSearch":
            return f"Ищу в сети «{a.get('query', '')}»"
        case "TodoWrite":
            return "Обновляю список задач"
        case "delegate_task":
            return f"Поручаю агенту «{a.get('agent', '')}»: {' '.join(str(a.get('task', '')).split())[:80]}"
        case "Task" | "Agent":
            return f"Поручаю подагенту: {a.get('description', '')}"
        case "edit_file":
            return f"Правлю {path}"
        case "write_file":
            return f"Пишу {path}"
        case "get_ecosystem_map":
            return "Смотрю карту проектов"
        case "get_project_card":
            return f"Читаю карточку «{a.get('project', '')}»"
        case "list_relations":
            return "Смотрю связи проектов"
        case "find_symbol":
            return f"Ищу определение {a.get('name', '')}"
        case "file_outline":
            return f"Смотрю структуру {path}"
        case "git_log":
            return "Смотрю историю git"
        case "list_terminals":
            return "Смотрю терминалы"
        case "read_terminal":
            return f"Читаю терминал «{a.get('terminal', '')}»"
        case "get_events":
            return "Смотрю, что происходило"
        case "read_file":
            start = a.get("start_line")
            return f"Читаю {path}" + (f" со строки {start}" if isinstance(start, int) and start > 1 else "")
        case "list_files":
            return f"Смотрю папку {path}" if path else "Смотрю файлы проекта"
        case "search_code":
            return f"Ищу «{a.get('query', '')}»"
        case "git_status":
            return "Смотрю состояние git"
        case "git_diff":
            return f"Смотрю изменения в {path}" if path else "Смотрю изменения"
        case "terminal_output":
            return f"Читаю терминал «{a['terminal']}»" if a.get("terminal") else "Смотрю терминалы"
        case "list_processes":
            return "Смотрю процессы"
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
        out.append(
            {
                **tool,
                "state": "error" if result.get("error") else "ok",
                "summary": first if len(first) <= 120 else first[:119] + "…",
                "output": output[:TOOL_OUTPUT_SHOWN] + ("\n…" if len(output) > TOOL_OUTPUT_SHOWN else ""),
            }
        )
    return out


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
            return "Карта проектов"
        case "project":
            return f"Проект «{project}»"
        case "file":
            return f"Файл {project}/{rest.partition(':')[2]}"
        case "live":
            return f"Сейчас в «{project}»"
        case "tools":
            return "Инструменты и правила"
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
    return {"key": key, "role": "error", "text": text, "html": page(render_plain(text))}


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
        out.append(user_row(m) if m["role"] == "user" else assistant_row(m, models))
    return out


def restyled(row: dict[str, Any]) -> dict[str, Any]:
    """The same row with its HTML rendered in the current theme."""
    if row["role"] == "assistant":
        return {**row, "segments": segments(row["markdown"])}
    return {**row, "html": page(render_plain(row["text"]))}


# --- the sidebar ---


def day_section(updated_at: str, now: datetime | None = None) -> str:
    """'Сегодня' / 'Вчера' / 'Последние 7 дней' / 'Ранее', by the local calendar."""
    now = (now or datetime.now()).astimezone()
    try:
        when = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(now.tzinfo)
    except ValueError:
        return "Ранее"
    days = (now.date() - when.date()).days
    if days <= 0:
        return "Сегодня"
    if days == 1:
        return "Вчера"
    if days < 7:
        return "Последние 7 дней"
    return "Ранее"


def chat_rows(
    conversations: list[dict[str, Any]], busy: set[str], models: dict[str, dict], now: datetime | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "id": c["id"],
            "title": c.get("title") or "Новый чат",
            "section": day_section(c.get("updated_at", ""), now),
            "busy": c["id"] in busy,
            "cloud": bool(c.get("last_model_id")) and not is_local(models, c.get("last_model_id")),
        }
        for c in conversations
    ]


# --- the model picker ---


def picker_items(
    models: list[dict[str, Any]], loaded: set[str], chosen: str | None, advice: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Sections and models for the picker: the hub's advice for this chat first (D11), local ones (which is in VRAM
    now), then cloud ones with prices; a model the chat may not use says why and cannot be picked."""
    items: list[dict[str, Any]] = []
    options = {o["model_id"]: o for o in (advice or {}).get("options") or []}
    best = options.get((advice or {}).get("recommended") or "")
    named = {m["id"]: m for m in models}
    if best is not None and best["model_id"] in named:
        items.append({"section": "Рекомендуется для этого чата", "id": ""})
        model = named[best["model_id"]]
        items.append({"section": "", "id": model["id"], "name": model["name"], "icon": "sparkle",
                      "note": " · ".join(best["reasons"][:3]), "selected": model["id"] == chosen,
                      "cloud": model["locality"] != "local"})  # fmt: skip
    for local in (True, False):
        group = [m for m in presenter.sorted_models(models) if (m.get("locality") == "local") == local]
        if not group:
            continue
        items.append({"section": "Локальные · на этой видеокарте" if local else "Облако", "id": ""})
        for m in group:
            ref = m.get("model_ref") or m["id"].partition("/")[2]
            caps = m.get("capabilities") or {}
            if local:
                note = "загружена в видеопамять" if ref in loaded else "загрузится при отправке"
            elif caps.get("subscription"):
                note = "подписка Claude Code · работает в папке проекта чата"
            else:
                prices = m.get("prices") or {}
                note = f"${prices.get('input', 0):g} / ${prices.get('output', 0):g} за 1M"
                if caps.get("context"):
                    note += f" · контекст {caps['context'] // 1000}K"
            blocked = (options.get(m["id"]) or {}).get("blocked")
            if blocked:
                note = f"недоступна: {blocked}"
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
