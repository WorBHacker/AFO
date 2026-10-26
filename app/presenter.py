"""What the window shows, as plain text and HTML - no widgets here, so all of it is easy to test."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        return f"Сессия: {tokens} ток. · {money(self.cost_usd)}"

    def tooltip(self, totals: dict[str, Any] | None) -> str:
        lines = [
            f"С открытия окна: {self.requests} запросов",
            f"вход {tokens_short(self.input_tokens)} (из кэша {tokens_short(self.cached_tokens)}), "
            f"выход {tokens_short(self.output_tokens)}",
            f"стоимость {money(self.cost_usd)}",
        ]
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
    return ratio, f"{gpu['vram_used_mib'] / 1024:.1f}/{gpu['vram_total_mib'] / 1024:.1f} ГБ"


def commit_meter(status: dict[str, Any] | None) -> tuple[float, str] | None:
    memory = (status or {}).get("memory")
    if not memory or not memory.get("commit_limit_gib"):
        return None
    ratio = memory["commit_used_gib"] / memory["commit_limit_gib"]
    return ratio, f"{memory['commit_used_gib']:.0f}/{memory['commit_limit_gib']:.0f} ГБ"


def gpu_load(status: dict[str, Any] | None) -> str:
    gpu = (status or {}).get("gpu") or {}
    parts = [f"{gpu['util_pct']}%"] if gpu.get("util_pct") is not None else []
    if gpu.get("temp_c") is not None:
        parts.append(f"{gpu['temp_c']}°C")
    return "GPU " + " · ".join(parts) if parts else ""


def local_model(status: dict[str, Any] | None) -> str:
    local = (status or {}).get("local") or {}
    if not local.get("reachable"):
        return "llama-swap не отвечает"
    running = local.get("running") or []
    if not running:
        return "видеопамять свободна"
    return ", ".join(f"{r['model']} · {STATE_NAMES.get(r['state'], r['state'])}" for r in running)


# --- budgets (D12) ---

PERIODS = [("day", "за день"), ("week", "за неделю"), ("month", "за месяц")]
HARD_ACTIONS = [("ask", "спрашивать"), ("block", "запрещать")]
PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter", "deepseek": "DeepSeek"}
STATE_ORDER = {"ok": 0, "soft": 1, "hard": 2}


def provider_name(ref: str) -> str:
    return PROVIDER_NAMES.get(ref, ref)


def budget_title(budget: dict[str, Any]) -> str:
    """'Все платные модели за месяц' / 'Anthropic за день'."""
    who = provider_name(budget["scope_ref"]) if budget["scope"] == "provider" else "Все платные модели"
    return f"{who} {dict(PERIODS).get(budget['period'], budget['period'])}"


def budget_line(budget: dict[str, Any]) -> str:
    """'Anthropic за день: $4.10 из $10.00 (предупреждение с $5.00)'."""
    used = money(budget.get("spent_usd", 0))
    if budget.get("hard_usd") is not None:
        text = f"{used} из {money(budget['hard_usd'])}"
        if budget.get("soft_usd") is not None:
            text += f" (предупреждение с {money(budget['soft_usd'])})"
    else:
        text = f"{used}, предупреждение с {money(budget['soft_usd'])}"
    return f"{budget_title(budget)}: {text}"


def budgets_state(budgets: list[dict[str, Any]]) -> str:
    """The worst state among the budgets: 'ok', 'soft' or 'hard'."""
    return max((b.get("state", "ok") for b in budgets), key=STATE_ORDER.get, default="ok")


def budget_question(over: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"• {budget_line(b)}" for b in over)
    return f"Бюджет исчерпан:\n{lines}\n\nОтправить запрос всё равно? Это отметится в журнале хаба."


def budget_event_text(kind: str, budget: dict[str, Any]) -> str:
    """The status-bar line for budget.warning / budget.exceeded."""
    title, used = budget_title(budget), money(budget.get("spent_usd", 0))
    if kind == "warning":
        return f"Бюджет «{title}»: потрачено {used}, достигнут порог предупреждения {money(budget['soft_usd'])}"
    after = "запрещены" if budget.get("hard_action") == "block" else "только с подтверждением"
    return f"Бюджет «{title}» исчерпан: {used} из {money(budget['hard_usd'])}. Новые платные запросы — {after}"
