"""A tool call a local model wrote as text (61.6; the user's complaint of 2026-11-17, «Стабилизация»).

llama-server fills an answer's `tool_calls` only when it knows the call by the model's chat template; a model that
writes the same JSON without the template's marks - in a json block, bare, or in <tool_call> marks the server did not
take - had its call shown in the chat and nothing done. The hub reads it now: a JSON object that names a tool the
model was given, with its arguments as an object, is that call, and it goes through the usual rights. Anything else
stays text - an example in prose, a JSON of other fields, a tool the model was not given."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

MARKED = re.compile(r"<(tool_call|function_call)>\s*(.*?)\s*</\1>", re.S | re.I)
FENCED = re.compile(r"```[A-Za-z_]*[ \t]*\n(.*?)```", re.S)
LONGEST = 50_000  # characters of an answer looked through: a call is short, a long answer is prose
DECODER = json.JSONDecoder()


def _objects(text: str) -> list[Any]:
    """The JSON values standing alone in `text`, in order: each that a `{` or `[` begins and that parses whole."""
    found: list[Any] = []
    at = 0
    while (start := _next_open(text, at)) >= 0:
        try:
            value, end = DECODER.raw_decode(text, start)
        except ValueError:
            at = start + 1
            continue
        found.append(value)
        at = end
    return found


def _next_open(text: str, at: int) -> int:
    braces = [i for i in (text.find("{", at), text.find("[", at)) if i >= 0]
    return min(braces) if braces else -1


def _call(value: Any, names: set[str]) -> dict[str, Any] | None:
    """{name, input} of one call written as an object - its own shape, or OpenAI's {function: {...}} - else None."""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("function"), dict):
        value = value["function"]
    name = value.get("name")
    args = next((value[k] for k in ("arguments", "parameters", "input", "args") if k in value), None)
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return None
    if not isinstance(name, str) or name not in names or not isinstance(args, dict):
        return None
    return {"name": name, "input": args}


def found(text: str, names: set[str]) -> list[dict[str, Any]]:
    """The calls of tools in `names` that `text` writes out - {id, name, input} each, in order, once each; [] - none.
    Marked and fenced ones are looked at first; JSON standing bare in the prose only when there are none."""
    if not names or not text or len(text) > LONGEST:
        return []
    parts = [m.group(2) for m in MARKED.finditer(text)] + [m.group(1) for m in FENCED.finditer(text)]
    values = [v for part in parts for v in _objects(part)] or _objects(text)
    calls: list[dict[str, Any]] = []
    for value in values:
        for item in value if isinstance(value, list) else [value]:
            call = _call(item, names)
            if call is not None and call not in calls:
                calls.append(call)
    return [{"id": f"text_call_{uuid.uuid4().hex[:12]}", **call} for call in calls]
