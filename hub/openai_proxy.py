"""The hub as an OpenAI-compatible server (stage 6, D33): other programs - Cline, scripts - reach every model of the
hub through it, and their requests are counted and held to the budgets like the hub's own.

POST /v1/openai/chat/completions takes the OpenAI shape (messages, tools, stream, reasoning_effort) and answers in it,
streamed as server-sent events or whole; GET /v1/openai/models lists the hub's models. Underneath it is the same
gateway as the chat (hub/gateway): llama-swap's models, Claude by API key - each provider's own format is the
adapter's business, so a Cline set up for "OpenAI compatible" gets Claude too. Claude Code is not offered: it is an
agent, not a model to call. The client's token is the API key; each request is recorded as usage of that client
(D12), and a paid model whose budget is used up is refused (no one is there to confirm). The client sends its own
data, so the hub adds nothing to it: no projects, no context.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .claude_code import PROVIDER as CLAUDE_CODE

EFFORTS = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}


class ProxyError(Exception):
    """An OpenAI-style error: status, type, message."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message

    def body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.kind, "code": self.kind}}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # parts: text is kept; images are not passed on yet
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def to_hub_messages(messages: Any) -> list[dict[str, Any]]:
    """OpenAI messages -> the gateway's prompt shape (system, user, assistant with tool_calls, tool results)."""
    if not isinstance(messages, list) or not messages:
        raise ProxyError(400, "invalid_request_error", "`messages` must be a non-empty list")
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        if role in ("system", "developer"):
            out.append({"role": "system", "content": _text(m.get("content"))})
        elif role == "user":
            out.append({"role": "user", "content": _text(m.get("content"))})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": _text(m.get("content"))}
            calls = []
            for call in m.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = {}
                calls.append({"id": call.get("id", ""), "name": function.get("name", ""), "input": arguments})
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "name": m.get("name", ""),
                        "content": _text(m.get("content")), "is_error": False})  # fmt: skip
        else:
            raise ProxyError(400, "invalid_request_error", f"unknown message role {role!r}")
    return out


def to_hub_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out = []
    for tool in tools:
        function = (tool or {}).get("function") or {}
        if tool.get("type") != "function" or not function.get("name"):
            raise ProxyError(400, "invalid_request_error", "only function tools are supported")
        out.append({"name": function["name"], "description": function.get("description", ""),
                    "parameters": function.get("parameters") or {"type": "object", "properties": {}}})  # fmt: skip
    return out


def client_params(body: dict[str, Any]) -> dict[str, Any]:
    """The answer's parameters the client sent, by the hub's names (D39): they count over the model's own settings.
    llama.cpp's own names (top_k, min_p, repeat_penalty) are taken too."""
    from .model_settings import SettingsError, check_params

    wanted: dict[str, Any] = {k: body[k] for k in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty")
                              if body.get(k) is not None}  # fmt: skip
    length = body.get("max_completion_tokens", body.get("max_tokens"))
    if length is not None:
        wanted["max_tokens"] = length
    stop = body.get("stop")
    if stop is not None:
        wanted["stop"] = [stop] if isinstance(stop, str) else stop
    try:
        return check_params(wanted)
    except SettingsError as exc:
        raise ProxyError(400, "invalid_request_error", str(exc)) from exc


def model_list(models: list[dict[str, Any]]) -> dict[str, Any]:
    return {"object": "list", "data": [
        {"id": m["id"], "object": "model", "created": 0, "owned_by": m["provider"]}
        for m in models if m["provider"] != CLAUDE_CODE and not m.get("hidden")
    ]}  # fmt: skip


def usage_of(usage: dict[str, int]) -> dict[str, int]:
    prompt, completion = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    out = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if usage.get("cached_input_tokens"):
        out["prompt_tokens_details"] = {"cached_tokens": usage["cached_input_tokens"]}
    return out


def tool_calls_of(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"index": n, "id": c["id"] or f"call_{uuid.uuid4().hex[:12]}", "type": "function",
             "function": {"name": c["name"], "arguments": json.dumps(c.get("input") or {}, ensure_ascii=False)}}
            for n, c in enumerate(calls)]  # fmt: skip


def finish_reason(stop: str | None, calls: list[dict[str, Any]], cancelled: bool) -> str:
    if calls:
        return "tool_calls"
    if stop in ("max_tokens", "length"):
        return "length"
    if stop == "refusal":
        return "content_filter"
    return "stop"


class Chunks:
    """Builds the chat.completion.chunk lines of one streamed answer."""

    def __init__(self, model_id: str):
        self.id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        self.model = model_id
        self.created = int(time.time())

    def line(self, delta: dict[str, Any], finish: str | None = None) -> bytes:
        choice = {"index": 0, "delta": delta, "finish_reason": finish}
        chunk = {"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
                 "choices": [choice]}  # fmt: skip
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

    def usage_line(self, usage: dict[str, int]) -> bytes:
        """The last chunk when the client asked for usage (stream_options.include_usage): no choices, the usage."""
        chunk = {"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
                 "choices": [], "usage": usage}  # fmt: skip
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

    @staticmethod
    def error_line(message: str) -> bytes:
        body = {"error": {"message": message, "type": "upstream_error"}}
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()

    def whole(self, text: str, reasoning: str, calls: list[dict[str, Any]], finish: str, usage: dict) -> dict:
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if reasoning:
            message["reasoning_content"] = reasoning
        if calls:
            message["tool_calls"] = tool_calls_of(calls)
        return {"id": self.id, "object": "chat.completion", "created": self.created, "model": self.model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}  # fmt: skip
