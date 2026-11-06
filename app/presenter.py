"""What the window shows, as plain text - no Qt here, so all of it is easy to test."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from common.i18n import N_, plural, tr

__all__ = ["plural"]  # presenter.plural: the views' plural forms (common.i18n)

EFFORTS = [
    ("low", N_("низкая")),
    ("medium", N_("средняя")),
    ("high", N_("высокая")),
    ("xhigh", N_("очень высокая")),
    ("max", N_("максимальная")),
]
STATE_NAMES = {"ready": N_("готова"), "starting": N_("загружается"), "stopping": N_("выгружается"),
               "shutdown": N_("выгружается")}  # fmt: skip


def efforts() -> list[tuple[str, str]]:
    """(value, label) in the window's language."""
    return [(value, tr(label)) for value, label in EFFORTS]


def _state(state: str) -> str:
    return tr(STATE_NAMES[state]) if state in STATE_NAMES else state


def seconds(value: float) -> str:
    if value < 60:
        return tr("{value:.1f} с", value=value)
    return tr("{minutes} мин {seconds} с", minutes=int(value // 60), seconds=int(value % 60))


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
        return tr("{name} · локальная", name=model["name"])
    prices = model.get("prices") or {}
    if prices:
        return tr("{name} · ${input}/${output} за 1M", name=model["name"], input=_price(prices.get("input", 0)),
                  output=_price(prices.get("output", 0)))  # fmt: skip
    return tr("{name} · облако", name=model["name"])


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
        parts.append(tr("{speed:.1f} т/с", speed=stats["tok_s"]))
    out = usage.get("output_tokens")
    if out:
        parts.append(tr("{tokens} ток.", tokens=f"{'~' if usage.get('estimated') else ''}{out}"))
    cached = usage.get("cached_input_tokens")
    if cached:
        parts.append(tr("кэш {tokens}", tokens=cached))
    cost = cost if cost is not None else usage.get("cost_usd")
    if cost:
        parts.append(money(cost))
    if stats.get("subscription"):  # Claude Code: the subscription pays; its own estimate for scale (D12)
        estimate = stats.get("cost_estimate_usd")
        parts.append(tr("подписка, по оценке ≈{cost}", cost=money(estimate)) if estimate else tr("подписка"))
    if usage.get("cancelled"):
        parts.append(tr("остановлено"))
    if usage.get("stop_reason") == "max_tokens":
        parts.append(tr("обрезано по лимиту"))
    return " · ".join(parts)


def claude_code_limit(info: dict[str, Any]) -> str:
    """A notice about the Claude Code subscription's limits (D12): near, or reached and until when."""
    resets = info.get("resetsAt")
    when = ""
    if isinstance(resets, int | float) and resets > 0:
        seconds = resets / 1000 if resets > 10**11 else resets  # seconds or milliseconds: the docs do not say
        when = datetime.fromtimestamp(seconds).strftime("%d.%m %H:%M")
    if info.get("status") == "rejected":
        if when:
            return tr("Claude Code: лимит подписки исчерпан до {when}", when=when)
        return tr("Claude Code: лимит подписки исчерпан")
    if when:
        return tr("Claude Code: лимит подписки близко до {when}", when=when)
    return tr("Claude Code: лимит подписки близко")


LIMIT_WINDOWS = {"five_hour": N_("5 часов"), "seven_day": N_("неделя"), "seven_day_opus": N_("неделя Opus"),
                 "seven_day_sonnet": N_("неделя Sonnet"), "overage": N_("сверх лимита")}  # fmt: skip


def _reset_time(seconds: float | None) -> str:
    if not seconds:
        return ""
    at = datetime.fromtimestamp(seconds)
    return at.strftime("%H:%M") if at.date() == datetime.now().date() else at.strftime("%d.%m %H:%M")


def limit_line(state: dict[str, Any]) -> str:
    """One window of the subscription's limits (20.3): «5 часов — 85 %, сброс в 15:45»; reached - «… — исчерпан»."""
    window = tr(LIMIT_WINDOWS[state["window"]]) if state.get("window") in LIMIT_WINDOWS else tr("лимит")
    when = _reset_time(state.get("resets_at"))
    if state.get("status") == "rejected":
        said = tr("исчерпан")
    elif state.get("used") is not None:
        said = f"{round(state['used'] * 100)} %"
    else:
        said = tr("близко") if state.get("status") == "allowed_warning" else tr("в норме")
    return f"{window} — {said}" + (tr(", сброс в {when}", when=when) if when else "")


def subscription(limits: list[dict[str, Any]]) -> tuple[str, str, bool]:
    """Claude Code's subscription (20.3): the window nearest its limit in a line, the tooltip (every window) and
    whether to warn - ("", "", False) when Claude Code has told nothing yet."""
    if not limits:
        return "", "", False
    reached = [s for s in limits if s.get("status") == "rejected"]
    worst = reached[0] if reached else max(limits, key=lambda s: (s.get("status") == "allowed_warning",
                                                                  s.get("used") or 0))  # fmt: skip
    tip = tr("Лимиты подписки Claude Code (по его последнему сообщению):") + "\n" + "\n".join(map(limit_line, limits))
    warn = bool(reached) or worst.get("status") == "allowed_warning"
    return limit_line(worst), tip, warn


def status_text(status: dict[str, Any] | None) -> str:
    """GPU, memory and the loaded local model in one line for the status bar."""
    if not status:
        return ""
    parts = []
    gpu = status.get("gpu")
    if gpu:
        line = tr(
            "VRAM {used:.1f}/{total:.1f} ГБ", used=gpu["vram_used_mib"] / 1024, total=gpu["vram_total_mib"] / 1024
        )
        if gpu.get("util_pct") is not None:
            line += f" · {gpu['util_pct']}%"
        if gpu.get("temp_c") is not None:
            line += f" · {gpu['temp_c']}°C"
        parts.append(line)
    memory = status.get("memory")
    if memory:
        parts.append(
            # tenths: whole numbers showed 63/63 - "full" - for 62.5 of 63.4
            tr("ОЗУ {used:.1f}/{total:.1f} ГБ, выделено {committed:.0f}/{limit:.0f} ГБ", used=memory["ram_used_gib"],
               total=memory["ram_total_gib"], committed=memory["commit_used_gib"], limit=memory["commit_limit_gib"])
        )  # fmt: skip
    local = status.get("local") or {}
    if not local.get("reachable"):
        parts.append(tr("llama-swap не отвечает"))
    elif local.get("running"):
        loaded = ", ".join(f"{r['model']} ({_state(r['state'])})" for r in local["running"])
        parts.append(tr("загружена: {models}", models=loaded))
    else:
        parts.append(tr("локальная модель не загружена"))
    return "  │  ".join(parts)


def commit_is_tight(status: dict[str, Any] | None, margin_gib: float = 4.0) -> bool:
    """Windows is close to its commit limit (RAM + page file): loading a big model may fail."""
    memory = (status or {}).get("memory")
    return bool(memory) and memory["commit_limit_gib"] - memory["commit_used_gib"] < margin_gib


def usage_text(totals: dict[str, Any] | None) -> str:
    if not totals:
        return ""
    return tr("Расходы: сегодня {today} · всего {total}", today=money(totals.get("today_cost_usd", 0)),
              total=money(totals.get("total_cost_usd", 0)))  # fmt: skip


def tokens_short(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}K"
    return f"{count / 1_000_000:.2f}M"


@dataclass
class Session:
    """What the models did since this window opened: tokens and money, for the status bar."""

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0

    def add(self, usage: dict[str, Any] | None, cost: float | None) -> None:
        usage = usage or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.cached_tokens += usage.get("cached_input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cost_usd += cost or 0.0
        self.requests += 1

    def text(self) -> str:
        tokens = tokens_short(self.input_tokens + self.output_tokens)
        return tr("Сессия: {tokens} ток. · {cost}", tokens=tokens, cost=money(self.cost_usd))

    def tooltip(self, totals: dict[str, Any] | None) -> str:
        lines = [
            tr("С открытия окна: {count} запросов", count=self.requests),
            tr("вход {input} (из кэша {cached}), выход {output}", input=tokens_short(self.input_tokens),
               cached=tokens_short(self.cached_tokens), output=tokens_short(self.output_tokens)),
            tr("стоимость {cost}", cost=money(self.cost_usd)),
        ]  # fmt: skip
        if totals:
            lines.append(usage_text(totals))
        return "\n".join(lines)


def hub_port(base_url: str | None) -> str:
    return ":" + base_url.rsplit(":", 1)[-1] if base_url else ""


def vram_meter(status: dict[str, Any] | None) -> tuple[float, str] | None:
    gpu = (status or {}).get("gpu")
    if not gpu or not gpu.get("vram_total_mib"):
        return None
    ratio = gpu["vram_used_mib"] / gpu["vram_total_mib"]
    return ratio, tr("{used:.1f}/{total:.1f} ГБ", used=gpu["vram_used_mib"] / 1024, total=gpu["vram_total_mib"] / 1024)


def commit_meter(status: dict[str, Any] | None) -> tuple[float, str] | None:
    memory = (status or {}).get("memory")
    if not memory or not memory.get("commit_limit_gib"):
        return None
    ratio = memory["commit_used_gib"] / memory["commit_limit_gib"]
    return ratio, tr("{used:.0f}/{total:.0f} ГБ", used=memory["commit_used_gib"], total=memory["commit_limit_gib"])


def gpu_load(status: dict[str, Any] | None) -> str:
    gpu = (status or {}).get("gpu") or {}
    parts = [f"{gpu['util_pct']}%"] if gpu.get("util_pct") is not None else []
    if gpu.get("temp_c") is not None:
        parts.append(f"{gpu['temp_c']}°C")
    return "GPU " + " · ".join(parts) if parts else ""


def local_model(status: dict[str, Any] | None) -> str:
    local = (status or {}).get("local") or {}
    if not local.get("reachable"):
        return tr("llama-swap не отвечает")
    running = local.get("running") or []
    if not running:
        return tr("видеопамять свободна")
    return ", ".join(f"{r['model']} · {_state(r['state'])}" for r in running)


# --- budgets (D12) ---

PERIODS = [("day", N_("за день")), ("week", N_("за неделю")), ("month", N_("за месяц"))]
HARD_ACTIONS = [("ask", N_("спрашивать")), ("block", N_("запрещать"))]
PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
}
STATE_ORDER = {"ok": 0, "soft": 1, "hard": 2}


def periods() -> list[tuple[str, str]]:
    return [(value, tr(label)) for value, label in PERIODS]


def hard_actions() -> list[tuple[str, str]]:
    return [(value, tr(label)) for value, label in HARD_ACTIONS]


def provider_name(ref: str) -> str:
    return PROVIDER_NAMES.get(ref, ref)


def budget_title(budget: dict[str, Any]) -> str:
    """'Все платные модели за месяц' / 'Anthropic за день'."""
    who = provider_name(budget["scope_ref"]) if budget["scope"] == "provider" else tr("Все платные модели")
    return f"{who} {dict(periods()).get(budget['period'], budget['period'])}"


def budget_line(budget: dict[str, Any]) -> str:
    """'Anthropic за день: $4.10 из $10.00 (предупреждение с $5.00)'."""
    used = money(budget.get("spent_usd", 0))
    if budget.get("hard_usd") is not None:
        text = tr("{used} из {limit}", used=used, limit=money(budget["hard_usd"]))
        if budget.get("soft_usd") is not None:
            text += tr(" (предупреждение с {soft})", soft=money(budget["soft_usd"]))
    else:
        text = tr("{used}, предупреждение с {soft}", used=used, soft=money(budget["soft_usd"]))
    return f"{budget_title(budget)}: {text}"


def budgets_state(budgets: list[dict[str, Any]]) -> str:
    """The worst state among the budgets: 'ok', 'soft' or 'hard'."""
    return max((b.get("state", "ok") for b in budgets), key=STATE_ORDER.get, default="ok")


def budget_question(over: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"• {budget_line(b)}" for b in over)
    return tr("Бюджет исчерпан:\n{lines}\n\nОтправить запрос всё равно? Это отметится в журнале хаба.", lines=lines)


def budget_event_text(kind: str, budget: dict[str, Any]) -> str:
    """The status-bar line for budget.warning / budget.exceeded."""
    title, used = budget_title(budget), money(budget.get("spent_usd", 0))
    if kind == "warning":
        return tr("Бюджет «{title}»: потрачено {used}, достигнут порог предупреждения {soft}", title=title, used=used,
                  soft=money(budget["soft_usd"]))  # fmt: skip
    if budget.get("hard_action") == "block":
        return tr("Бюджет «{title}» исчерпан: {used} из {limit}. Новые платные запросы — запрещены", title=title,
                  used=used, limit=money(budget["hard_usd"]))  # fmt: skip
    return tr("Бюджет «{title}» исчерпан: {used} из {limit}. Новые платные запросы — только с подтверждением",
              title=title, used=used, limit=money(budget["hard_usd"]))  # fmt: skip
