"""What the window shows, as plain text and HTML - no widgets here, so all of it is easy to test."""

from __future__ import annotations

from typing import Any

from .render import STYLE_SHEET, render, render_plain

EFFORTS = [
    ("low", "низкая"),
    ("medium", "средняя"),
    ("high", "высокая"),
    ("xhigh", "очень высокая"),
    ("max", "максимальная"),
]
STATE_NAMES = {"ready": "готова", "starting": "загружается", "stopping": "выгружается", "shutdown": "выгружается"}


def money(usd: float) -> str:
    if usd == 0:
        return "$0"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _price(value: float) -> str:
    return f"{value:g}"


def model_label(model: dict[str, Any]) -> str:
    """'Flash-Next Coder · локальная' or 'Claude Opus 5.5 · $4/$20 за 1M'."""
    if model.get("locality") == "local":
        return f"{model['name']} · локальная"
    prices = model.get("prices") or {}
    if prices:
        return f"{model['name']} · ${_price(prices.get('input', 0))}/${_price(prices.get('output', 0))} за 1M"
    return f"{model['name']} · облако"


def sorted_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Local models first (free, private), then cloud ones; each group by name."""
    return sorted(models, key=lambda m: (m.get("locality") != "local", m.get("name", "").lower()))


def split_blocks(content: list[dict[str, Any]]) -> tuple[str, str]:
    """A stored message's (visible text, reasoning)."""
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    reasoning = "".join(b.get("text", "") for b in content if b.get("type") == "reasoning")
    return text, reasoning


def answer_meta(usage: dict[str, Any] | None, *, stats: dict | None = None, cost: float | None = None) -> str:
    """'46.0 т/с · 120 ток. · $0.0012' - what an answer took. Cancelled and estimated runs say so."""
    if not usage:
        return ""
    stats = stats if stats is not None else usage.get("stats") or {}
    parts = []
    if stats.get("tok_s"):
        parts.append(f"{stats['tok_s']:.1f} т/с")
    out = usage.get("output_tokens")
    if out:
        parts.append(f"{'~' if usage.get('estimated') else ''}{out} ток.")
    cached = usage.get("cached_input_tokens")
    if cached:
        parts.append(f"кэш {cached}")
    cost = cost if cost is not None else usage.get("cost_usd")
    if cost:
        parts.append(money(cost))
    if usage.get("cancelled"):
        parts.append("остановлено")
    if usage.get("stop_reason") == "max_tokens":
        parts.append("обрезано по лимиту")
    return " · ".join(parts)


def status_text(status: dict[str, Any] | None) -> str:
    """GPU, memory and the loaded local model in one line for the status bar."""
    if not status:
        return ""
    parts = []
    gpu = status.get("gpu")
    if gpu:
        line = f"VRAM {gpu['vram_used_mib'] / 1024:.1f}/{gpu['vram_total_mib'] / 1024:.1f} ГБ"
        if gpu.get("util_pct") is not None:
            line += f" · {gpu['util_pct']}%"
        if gpu.get("temp_c") is not None:
            line += f" · {gpu['temp_c']}°C"
        parts.append(line)
    memory = status.get("memory")
    if memory:
        parts.append(
            f"ОЗУ {memory['ram_used_gib']:.0f}/{memory['ram_total_gib']:.0f} ГБ, "
            f"выделено {memory['commit_used_gib']:.0f}/{memory['commit_limit_gib']:.0f} ГБ"
        )
    local = status.get("local") or {}
    if not local.get("reachable"):
        parts.append("llama-swap не отвечает")
    elif local.get("running"):
        loaded = ", ".join(f"{r['model']} ({STATE_NAMES.get(r['state'], r['state'])})" for r in local["running"])
        parts.append(f"загружена: {loaded}")
    else:
        parts.append("локальная модель не загружена")
    return "  │  ".join(parts)


def commit_is_tight(status: dict[str, Any] | None, margin_gib: float = 4.0) -> bool:
    """Windows is close to its commit limit (RAM + page file): loading a big model may fail."""
    memory = (status or {}).get("memory")
    return bool(memory) and memory["commit_limit_gib"] - memory["commit_used_gib"] < margin_gib


def usage_text(totals: dict[str, Any] | None) -> str:
    if not totals:
        return ""
    return f"Расходы: сегодня {money(totals.get('today_cost_usd', 0))} · всего {money(totals.get('total_cost_usd', 0))}"


def _page(body: str) -> str:
    # QLabel has no default style sheet, so the document carries its own
    return f"<html><head><style>{STYLE_SHEET}</style></head><body>{body}</body></html>"


def markdown_html(text: str) -> tuple[str, list[str]]:
    """An answer as HTML for a QLabel, and the raw text of its code blocks (the 'Копировать' links)."""
    body, codes = render(text)
    return _page(body), codes


def plain_html(text: str) -> str:
    return _page(render_plain(text))
