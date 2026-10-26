"""Hub data -> the plain dicts the QML view shows. No Qt here, so all of it is easy to test.

Colours live inside the HTML, so rows are rendered again after a theme switch (the raw markdown is kept for that).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import palette, presenter
from ..render import Code, highlight_code, language_label, page, render_plain, render_segments


def segments(markdown: str) -> list[dict[str, str]]:
    """An answer as pieces for QML: prose HTML, and code blocks with their language, coloured HTML and raw text."""
    out = []
    for segment in render_segments(markdown) if markdown else []:
        if isinstance(segment, Code):
            spans = highlight_code(segment.code, segment.lang)
            html = f'<pre style="margin:0; font-family:{palette.MONO_CSS}; line-height:140%;">{spans}</pre>'
            out.append({"kind": "code", "lang": language_label(segment.lang), "html": html, "code": segment.code})
        else:
            out.append({"kind": "prose", "html": page(segment.html), "code": ""})
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
    "git_status": "branch",
    "git_diff": "diff",
    "terminal_output": "terminal",
    "list_processes": "cpu",
}
TOOL_OUTPUT_SHOWN = 6000  # characters of a result shown when opened


def tool_label(name: str, args: Any) -> str:
    """What a tool call does, in a few words: 'Читаю hub/chat.py', 'Ищу «budget»'."""
    a = args if isinstance(args, dict) else {}
    path = a.get("path") if isinstance(a.get("path"), str) else ""
    match name:
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
            "icon": TOOL_ICONS.get(b["name"], "dots"),
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
        if m["role"] == "tool":
            for row in reversed(out):
                if row.get("tools"):
                    row["tools"] = with_results(row["tools"], m["content"])
                    break
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


def picker_items(models: list[dict[str, Any]], loaded: set[str], chosen: str | None) -> list[dict[str, Any]]:
    """Sections and models for the picker: local ones (which is in VRAM now), then cloud ones with prices."""
    items: list[dict[str, Any]] = []
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
            else:
                prices = m.get("prices") or {}
                note = f"${prices.get('input', 0):g} / ${prices.get('output', 0):g} за 1M"
                if caps.get("context"):
                    note += f" · контекст {caps['context'] // 1000}K"
            items.append(
                {
                    "section": "",
                    "id": m["id"],
                    "name": m["name"],
                    "note": note,
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
