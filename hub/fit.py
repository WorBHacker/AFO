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
The stored conversation stays whole: only the request is shortened.
"""

from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 3.0
KEEP_TAIL = 8  # the last messages stay whole
TOOL_MIN = 400  # a tool result shorter than this is not worth cutting
TEXT_MAX = 2000  # an old text longer than this keeps its beginning and end
STUB = ("[{name}: прежний результат ({chars} знаков) убран, чтобы разговор поместился в контекст модели; "
        "вызовите снова, если он нужен]")  # fmt: skip


def tokens(value: Any) -> int:
    """About how many tokens a message (or the tool definitions) takes."""
    if not value:
        return 0
    return int(len(json.dumps(value, ensure_ascii=False, default=str)) / CHARS_PER_TOKEN) + 4


def room(window: int) -> int:
    """What a request may take of a window: the rest is the answer's."""
    return window - max(2048, window // 8)


def fit(prompt: list[dict[str, Any]], *, window: int | None, tools: list[dict] | None = None
        ) -> tuple[list[dict[str, Any]], int]:  # fmt: skip
    """The prompt shortened to fit `window` tokens (see the module's text) and how many messages were shortened;
    the prompt itself when it fits or the window is not known."""
    if not window:
        return prompt, 0
    budget = room(window) - tokens(tools)
    total = sum(tokens(entry) for entry in prompt)
    if total <= budget:
        return prompt, 0
    out = [dict(entry) for entry in prompt]
    end = max(len(out) - KEEP_TAIL, 0)
    shortened = 0
    for i in range(end):  # 1. the oldest tool results
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] != "tool" or not isinstance(text, str) or len(text) <= TOOL_MIN:
            continue
        before = tokens(entry)
        entry["content"] = STUB.format(name=entry.get("name") or "инструмент", chars=len(text))
        total -= before - tokens(entry)
        shortened += 1
        if total <= budget:
            return out, shortened
    for i in range(end):  # 2. long old texts: their beginning and end
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] not in ("user", "assistant") or not isinstance(text, str) or len(text) <= TEXT_MAX:
            continue
        before = tokens(entry)
        entry["content"] = text[:1200] + "\n[…]\n" + text[-400:]
        entry.pop("provider_raw", None)  # the provider's own blocks would bring the whole text back
        entry.pop("provider", None)
        total -= before - tokens(entry)
        shortened += 1
        if total <= budget:
            return out, shortened
    for i in range(end, len(out) - 2):  # 3. a small window: the recent tool results too, all but the last
        entry = out[i]
        text = entry.get("content") or ""
        if entry["role"] != "tool" or not isinstance(text, str) or len(text) <= TOOL_MIN:
            continue
        before = tokens(entry)
        entry["content"] = STUB.format(name=entry.get("name") or "инструмент", chars=len(text))
        total -= before - tokens(entry)
        shortened += 1
        if total <= budget:
            break
    return out, shortened
