"""A conversation that grew past the model's context window (D14, D22): the request still fits.

Every step of a chat or an agent's run sends the whole conversation; a long task (an autonomous agent looking at a
window again and again - each look a long tree of elements) outgrows the window, and the model refuses ("prompt
leaves no room to answer"). Before each request the prompt is measured (roughly: characters / 3, safe for Russian,
English and code alike) against the window less room for the answer and the tool definitions; when it is over:

1. the oldest tool results give way first - each becomes a one-line note of what it was (the call and its answer
   stay paired, as providers require);
2. then long old texts keep only their beginning and end;
3. the last messages stay whole while that is enough - the model works from them; a small window takes their tool
   results too, all but the very last. When even that is not enough, the request goes as it is and the provider
   says so (in plain words: «Разговор не помещается в модель»).
Before that, always: a look at a window (screen_look - a long tree of its elements) is stale once the agent has
looked again; only the last LOOKS_KEPT looks go whole, the older ones as a note - the request stays small and fast.

The measure is a guess; the model's own count of the last request (its usage) corrects it for the next one, and a
request the provider still refuses as too long ("exceeds the context window: prompt (N tokens)") is shortened by
that count and sent again (hub/chat.py).

The stored conversation stays whole: only the request is shortened.
"""

from __future__ import annotations

import json
import re
from typing import Any

CHARS_PER_TOKEN = 3.0
KEEP_TAIL = 8  # the last messages stay whole
TOOL_MIN = 400  # a tool result shorter than this is not worth cutting
TEXT_MAX = 2000  # an old text longer than this keeps its beginning and end
STUB = ("[{name}: прежний результат ({chars} знаков) убран, чтобы разговор поместился в контекст модели; "
        "вызовите снова, если он нужен]")  # fmt: skip
LOOKS = frozenset({"screen_look"})  # stale once the agent looked again
LOOKS_KEPT = 2
STALE = "[{name}: прежний осмотр окна ({chars} знаков) убран — окно с тех пор менялось; смотрите последний осмотр]"
OVERFLOW = re.compile(r"(?i)exceed\w*\s+(?:the\s+)?context|context\s+(?:window|length)|maximum\s+context|"
                      r"prompt\s+is\s+too\s+long|too\s+many\s+tokens")  # fmt: skip


def tokens(value: Any) -> int:
    """About how many tokens a message (or the tool definitions) takes."""
    if not value:
        return 0
    return int(len(json.dumps(value, ensure_ascii=False, default=str)) / CHARS_PER_TOKEN) + 4


def estimate(prompt: list[dict[str, Any]], tools: list[dict] | None = None) -> int:
    return sum(tokens(entry) for entry in prompt) + tokens(tools)


def calibrate(actual: int, prompt: list[dict[str, Any]], tools: list[dict] | None = None) -> float:
    """How much the guess fell short of the model's own count of a request (at least 1)."""
    guessed = estimate(prompt, tools)
    return max(1.0, actual / guessed) if actual > 0 and guessed > 0 else 1.0


def overflow(detail: str) -> int | None:
    """A provider's refusal of a request too long for the model: the tokens it counted (0 when it did not say);
    None - not that refusal."""
    if not OVERFLOW.search(detail or ""):
        return None
    counted = re.search(r"(?i)prompt\s*\((\d+)\s*tokens?\)|(\d+)\s*(?:input\s+)?tokens?\s*>", detail)
    return int(next(g for g in counted.groups() if g)) if counted else 0


def stale_looks(out: list[dict[str, Any]]) -> int:
    """All but the last LOOKS_KEPT looks at a window as a note (in place); how many."""
    looks = [i for i, entry in enumerate(out) if entry["role"] == "tool" and entry.get("name") in LOOKS
             and isinstance(entry.get("content"), str) and len(entry["content"]) > TOOL_MIN]  # fmt: skip
    for i in looks[:-LOOKS_KEPT]:
        out[i] = {**out[i], "content": STALE.format(name=out[i]["name"], chars=len(out[i]["content"]))}
    return max(len(looks) - LOOKS_KEPT, 0)


def room(window: int) -> int:
    """What a request may take of a window: the rest is the answer's."""
    return window - max(2048, window // 8)


def fit(prompt: list[dict[str, Any]], *, window: int | None, tools: list[dict] | None = None, scale: float = 1.0
        ) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """The prompt with its stale looks as notes and shortened to fit `window` tokens (see the module's text), and how
    many messages were shortened; `scale` - how much the guess falls short for this model (calibrate)."""
    out = [dict(entry) for entry in prompt]
    shortened = stale_looks(out)
    if not window:
        return (out, shortened) if shortened else (prompt, 0)

    def cost(value: Any) -> float:  # the guess, as this model counts
        return tokens(value) * scale

    budget = room(window) - cost(tools)
    total = sum(cost(entry) for entry in out)
    if total <= budget:
        return (out, shortened) if shortened else (prompt, 0)
    end = max(len(out) - KEEP_TAIL, 0)
    for i in range(end):  # 1. the oldest tool results
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] != "tool" or not isinstance(text, str) or len(text) <= TOOL_MIN:
            continue
        before = cost(entry)
        entry["content"] = STUB.format(name=entry.get("name") or "инструмент", chars=len(text))
        total -= before - cost(entry)
        shortened += 1
        if total <= budget:
            return out, shortened
    for i in range(end):  # 2. long old texts: their beginning and end
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] not in ("user", "assistant") or not isinstance(text, str) or len(text) <= TEXT_MAX:
            continue
        before = cost(entry)
        entry["content"] = text[:1200] + "\n[…]\n" + text[-400:]
        entry.pop("provider_raw", None)  # the provider's own blocks would bring the whole text back
        entry.pop("provider", None)
        total -= before - cost(entry)
        shortened += 1
        if total <= budget:
            return out, shortened
    for i in range(end, len(out) - 2):  # 3. a small window: the recent tool results too, all but the last
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] != "tool" or not isinstance(text, str) or len(text) <= TOOL_MIN:
            continue
        before = cost(entry)
        entry["content"] = STUB.format(name=entry.get("name") or "инструмент", chars=len(text))
        total -= before - cost(entry)
        shortened += 1
        if total <= budget:
            break
    return out, shortened
