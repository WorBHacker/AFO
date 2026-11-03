"""The Anthropic adapter: Claude by API key (D6, D15), through the official `anthropic` SDK.

What it takes care of (claude-api reference, cache of 2026-10-06; re-check prices before trusting them):
- adaptive thinking with a readable summary, depth set by output_config.effort. Thinking cannot be turned off
  on the current models, and temperature / top_p / top_k are never sent: they are a 400 since 2026-10-21 (D15)
- the answer is stored as received (`raw`) and Claude's own turns are replayed from it, thinking blocks and
  their signatures unchanged: a thinking block is bound to the conversation before it, so the history is only
  ever appended to (D7, D14)
- the conversation is cached automatically (top-level cache_control): everything before the new turn is the
  stable prefix, so a long chat pays the cache-read price for it. The stable parts have points of their own (D46,
  step 13.9; the claude-api reference's advice for agent loops): the last tool - the list is the same in every chat,
  so a new chat reads it from the cache - and the system prompt; three of the four points a request may have
- a refusal arrives as HTTP 200 with stop_reason "refusal"; it is reported as a refusal and its tokens still
  count. Server-side fallbacks to another model are not requested: the model never changes silently (D11)
- the catalog is the priced models below that the key can actually use (Models API)
- Anthropic reports fresh, cache-read and cache-write input apart; usage adds them up into input_tokens
- tools (stage 3): client tools with eager input streaming; tool_choice stays the default "auto" (forcing a call
  is a 400 on the 5.5 models). The calls come from the final message; their inputs are checked by the hub before
  anything runs. A tool input the SDK cannot parse at all raises ValueError from the stream: reported as an
  error, nothing is run. Earlier tool turns go back as tool_use / tool_result blocks
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from .base import Model, ProviderError, StreamEvent

log = logging.getLogger("hub.gateway.anthropic")

PRICES_ASOF = "2026-10-06"
# USD per 1M tokens. Cache reads for Opus / Sonnet / Fable are the published ones; the rest follow the usual
# multipliers (read 0.1x, write 1.25x of input for the 5-minute cache).
_CATALOG: dict[str, tuple[str, dict[str, float]]] = {
    "claude-opus-5-5": ("Claude Opus 5.5", {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}),
    "claude-sonnet-5-5": (
        "Claude Sonnet 5.5",
        {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    ),
    # prices for prompts up to 100K tokens; longer prompts cost $0.50 / $2.50, which is not modelled yet
    "claude-haiku-5-5": (
        "Claude Haiku 5.5",
        {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    ),
    "claude-fable-5-1": (
        "Claude Fable 5.1",
        {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5},
    ),
}
CACHE_POINT = {"type": "ephemeral"}  # 5 minutes, as the automatic point: a longer one would have to come first
DEFAULT_MAX_TOKENS = 64000  # streamed, so a large cap costs nothing until it is used
CHARS_PER_TOKEN_ESTIMATE = 3  # only for a cancelled run, whose output count Anthropic never reports


def _default_client_factory(api_key: str) -> Any:
    # imported here: the SDK takes ~1.8 s to import, two thirds of the hub's start, and is only needed with a key
    import anthropic

    # a long read timeout for slow thinking, but an unreachable API is reported in seconds, not minutes
    return anthropic.AsyncAnthropic(api_key=api_key, timeout=anthropic.Timeout(600.0, connect=10.0))


class AnthropicProvider:
    def __init__(
        self,
        key_getter: Callable[[], str | None],
        name: str = "anthropic",
        client_factory: Callable[[str], Any] = _default_client_factory,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.name = name
        # the same tool list in every request of every conversation: rebuilding it would void earlier thinking
        self.tools_always = True
        self._key_getter = key_getter  # read on every use: the user may add or change the key while the hub runs
        self._client_factory = client_factory
        self._max_tokens = max_tokens
        self._client: Any = None
        self._client_key_hash: str | None = None

    def _key(self) -> str | None:
        try:
            return self._key_getter()
        except Exception as exc:
            raise ProviderError(f"{self.name}: cannot read the API key from the secret store ({exc})") from exc

    def _client_for(self, key: str) -> Any:
        """One SDK client (and its connection pool) per key; a new key gets a new client."""
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        if self._client is None or key_hash != self._client_key_hash:
            self._client = self._client_factory(key)
            self._client_key_hash = key_hash
        return self._client

    def accepts(self, model_ref: str) -> frozenset[str]:
        """Only the answer's length and stop sequences: temperature, top_p and top_k are a 400 (D15), and thinking
        cannot be turned off on these models."""
        return frozenset({"max_tokens", "stop"})

    async def list_models(self) -> list[Model]:
        """The priced Claude models this key can use. No key - no models, and no network call."""
        key = self._key()
        if not key:
            return []
        try:
            available = {info.id: info async for info in self._client_for(key).models.list()}
        except Exception as exc:
            raise ProviderError(f"{self.name}: cannot list models ({_describe(exc)})") from exc
        unpriced = sorted(set(available) - set(_CATALOG))
        if unpriced:
            log.info("Claude models without a price in this build, not offered: %s", ", ".join(unpriced))
        models = []
        for ref, (label, prices) in _CATALOG.items():
            info = available.get(ref)
            if info is None:
                continue
            models.append(
                Model(
                    id=f"{self.name}/{ref}",
                    provider=self.name,
                    model_ref=ref,
                    name=getattr(info, "display_name", None) or label,
                    locality="cloud",
                    capabilities={
                        "vision": True,
                        "context": getattr(info, "max_input_tokens", None),
                        "max_output": getattr(info, "max_tokens", None),
                        "prices_asof": PRICES_ASOF,
                    },
                    prices=prices,
                )
            )
        return models

    async def stream_chat(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        effort: str,
        cancel: asyncio.Event,
        tools: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        params = params or {}
        key = self._key()
        if not key:
            raise ProviderError("no Anthropic API key is set (python -m hub key set anthropic_api_key)")
        # mid-conversation system messages: Opus, Sonnet and Fable 5.x take them, Haiku does not
        system, turns = to_wire(messages, self.name, mid_system=not model_ref.startswith("claude-haiku"))
        request: dict[str, Any] = {
            "model": model_ref,
            "max_tokens": params.get("max_tokens", self._max_tokens),
            "messages": turns,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": effort},
            "cache_control": CACHE_POINT,
        }
        if system:
            request["system"] = [{"type": "text", "text": system, "cache_control": CACHE_POINT}]
        if params.get("stop"):
            request["stop_sequences"] = params["stop"]
        if tools:
            request["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                    "eager_input_streaming": True,  # inputs stream as they are made; the hub checks them
                }
                for t in tools
            ]
            request["tools"][-1]["cache_control"] = CACHE_POINT

        started = time.monotonic()
        first_at: float | None = None
        streamed_chars = 0
        snapshot: Any = None
        try:
            async with self._client_for(key).messages.stream(**request) as stream:
                message_started = False
                async for event in stream:
                    if cancel.is_set():
                        break
                    if event.type == "message_start":
                        message_started = True
                    elif event.type == "text" and event.text:
                        first_at = first_at or time.monotonic()
                        streamed_chars += len(event.text)
                        yield StreamEvent("content", event.text)
                    elif event.type == "thinking" and event.thinking:
                        first_at = first_at or time.monotonic()
                        streamed_chars += len(event.thinking)
                        yield StreamEvent("reasoning", event.thinking)
                if message_started:
                    snapshot = stream.current_message_snapshot
        except ValueError as exc:  # a tool input that is not JSON at all: nothing can be run from it
            raise ProviderError(f"{self.name}: the model sent a tool call that could not be read; try again") from exc
        except Exception as exc:
            raise ProviderError(f"{self.name}: {_describe(exc)}") from exc

        usage = _usage(getattr(snapshot, "usage", None))
        if cancel.is_set():
            # output tokens arrive only with the final message_delta; a cancelled run never gets them
            usage["output_tokens"] = max(usage["output_tokens"], math.ceil(streamed_chars / CHARS_PER_TOKEN_ESTIMATE))
            usage["estimated"] = 1
        stop_reason = getattr(snapshot, "stop_reason", None)
        yield StreamEvent(
            "end",
            usage=usage,
            stats=_stats(usage["output_tokens"], started, first_at),
            stop_reason=stop_reason,
            raw=snapshot.to_dict(mode="json") if snapshot is not None else None,
            refusal=_refusal_text(getattr(snapshot, "stop_details", None)) if stop_reason == "refusal" else None,
            tool_calls=[
                {"id": block.id, "name": block.name, "input": block.input}
                for block in getattr(snapshot, "content", None) or []
                if getattr(block, "type", "") == "tool_use"
            ],
        )


def to_wire(
    messages: list[dict[str, Any]], provider_name: str, *, mid_system: bool = True
) -> tuple[str, list[dict[str, Any]]]:
    """The conversation as Anthropic takes it: `system` apart, then user/assistant turns of content blocks.

    Claude's own earlier turns come from their stored raw message, so thinking blocks go back unchanged; any
    other turn is its visible text. Adjacent turns of one role are joined - deterministically, so the same
    history always gives the same bytes and the cached prefix and thinking signatures stay valid.

    A system message inside the conversation (the hub's context, D22) stays where it is: a mid-conversation
    system message on the models that take one (`mid_system`), else a text block at the end of the user turn
    before it. Only system messages before the first user turn become the top-level `system`.
    """
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            text = message["content"]
            if not text:
                continue
            if not turns:
                system_parts.append(text)
            elif mid_system and turns[-1]["role"] == "user":
                turns.append({"role": "system", "content": text})
            elif turns[-1]["role"] == "system":
                turns[-1]["content"] += "\n\n" + text
            elif turns[-1]["role"] == "user":
                turns[-1]["content"].append({"type": "text", "text": f"<system-reminder>\n{text}\n</system-reminder>"})
            else:
                turns.append({"role": "user", "content": [{"type": "text", "text": f"<system-reminder>\n{text}\n"
                                                                                   "</system-reminder>"}]})  # fmt: skip
            continue
        blocks: list[dict[str, Any]] = []
        if role == "tool":  # the hub's answer to a tool call goes back as the user's turn
            role = "user"
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"] or "(empty)",
                    "is_error": bool(message.get("is_error")),
                }
            ]
        raw = message.get("provider_raw")
        if role == "assistant" and raw and message.get("provider") == provider_name:
            blocks = replay_blocks(raw.get("content") or [])
        if not blocks and message["content"]:
            blocks = [{"type": "text", "text": message["content"]}]
        if role == "user" and message.get("images"):  # the pictures sent with it, before its text (2026-10-29)
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}}
                      for i in message["images"]] + blocks  # fmt: skip
        if role == "assistant" and message.get("tool_calls") and not any(b["type"] == "tool_use" for b in blocks):
            # another model's tool calls: Claude takes them as its own tool_use blocks
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                       for c in message["tool_calls"]]  # fmt: skip
        if not blocks:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})
    return "\n\n".join(system_parts), turns


def replay_blocks(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude's blocks as they go back to it: unchanged, minus what cannot be sent - a thinking block cut off
    before its signature arrived (a cancelled run) and empty text."""
    out: list[dict[str, Any]] = []
    for block in content:
        kind = block.get("type")
        if kind == "thinking" and block.get("signature"):
            out.append({"type": "thinking", "thinking": block.get("thinking", ""), "signature": block["signature"]})
        elif kind == "redacted_thinking" and block.get("data"):
            out.append({"type": "redacted_thinking", "data": block["data"]})
        elif kind == "text" and block.get("text"):
            out.append({"type": "text", "text": block["text"]})
        elif kind == "tool_use":
            out.append({"type": "tool_use", "id": block["id"], "name": block["name"], "input": block.get("input", {})})
    return out


def _usage(usage: Any) -> dict[str, int]:
    fresh = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return {
        "input_tokens": fresh + cache_read + cache_write,
        "cached_input_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


def _stats(output_tokens: int, started: float, first_at: float | None) -> dict[str, float]:
    end = time.monotonic()
    tok_s = output_tokens / (end - first_at) if first_at and end > first_at else 0.0
    return {
        "tok_s": round(tok_s, 1),
        "ttft_s": round(first_at - started, 2) if first_at else 0.0,
        "total_s": round(end - started, 2),
    }


def _refusal_text(details: Any) -> str:
    category = getattr(details, "category", None)
    explanation = getattr(details, "explanation", None)
    text = "Claude declined this request"
    if category:
        text += f" (category: {category})"
    if explanation:
        text += f": {_short(explanation)}"
    return text + ". Rephrase it or choose another model."


def _describe(exc: Exception) -> str:
    """A short, safe description of an SDK error: never the request body, never the key."""
    import anthropic  # loaded by now: the error came from its client

    if isinstance(exc, anthropic.AuthenticationError):
        return "the API key was rejected (401)"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "this key may not use that model (403)"
    if isinstance(exc, anthropic.NotFoundError):
        return "no such model (404)"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate limit reached (429), try again later"
    if isinstance(exc, anthropic.APIStatusError):
        return f"HTTP {exc.status_code}: {_short(exc.message)}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "cannot reach the Anthropic API (network or timeout)"
    return _short(str(exc)) or type(exc).__name__


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
