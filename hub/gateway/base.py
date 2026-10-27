"""Shared gateway types: the model record, the unified message blocks, the stream event, providers.

A message's content is a list of blocks in the hub's own format:
    {"type": "text", "text": "..."}        the visible answer
    {"type": "reasoning", "text": "..."}   the model's thinking (not sent back to a model as input, D14)
    {"type": "tool_call", "id", "name", "input"}           an assistant asks the hub to run a tool (stage 3)
    {"type": "tool_result", "id", "name", "output", "error"}   the hub's answer, in a message of role "tool"
Each provider adapter turns these into its own wire format and back. A tool is described to an adapter as
{name, description, parameters (JSON schema)}.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


class GatewayError(Exception):
    """Base for gateway problems the API turns into a clean error for the client."""


class ModelNotFound(GatewayError):
    pass


class ProviderError(GatewayError):
    """The upstream provider refused or failed; `detail` is safe to show."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Model:
    id: str  # "<provider>/<model_ref>", e.g. "llama-swap/coder"
    provider: str
    model_ref: str  # the id the provider itself expects
    name: str
    locality: str  # "local" | "cloud"
    capabilities: dict[str, Any] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)  # USD per 1M tokens


@dataclass
class StreamEvent:
    """One piece of a streamed answer.

    kind "loading"   - the provider is still getting ready (llama-swap model swap); not part of the answer
    kind "reasoning" - a thinking delta
    kind "content"   - an answer delta
    kind "end"       - the final event: usage and stats, no text

    The "end" event may also carry why the model stopped (`stop_reason`), the provider's message as received
    (`raw`, stored as is for providers that need their own blocks back, D7/D14) and, when the provider declined
    the request, a safe explanation (`refusal`): the tokens were still spent and must be accounted for.
    """

    kind: str
    text: str = ""
    usage: dict[str, int] | None = None
    stats: dict[str, float] | None = None
    stop_reason: str | None = None
    raw: dict[str, Any] | None = None
    refusal: str | None = None
    tool_calls: list[dict[str, Any]] | None = None  # [{id, name, input}] the model wants run


@dataclass
class Completion:
    """What a finished run produced, assembled from the stream."""

    text: str = ""
    reasoning: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=dict)
    stop_reason: str | None = None
    raw: dict[str, Any] | None = None
    refusal: str | None = None
    cancelled: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def blocks(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.reasoning:
            out.append(reasoning_block(self.reasoning))
        if self.text or not self.tool_calls:
            out.append(text_block(self.text))
        out += [tool_call_block(c["id"], c["name"], c["input"]) for c in self.tool_calls]
        return out


def text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def reasoning_block(text: str) -> dict[str, str]:
    return {"type": "reasoning", "text": text}


def tool_call_block(call_id: str, name: str, args: Any) -> dict[str, Any]:
    return {"type": "tool_call", "id": call_id, "name": name, "input": args}


def tool_result_block(call_id: str, name: str, output: str, error: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "id": call_id, "name": name, "output": output, "error": error}


def blocks_to_prompt_text(blocks: list[dict[str, Any]]) -> str:
    """The text a model should receive as input: visible text only, never reasoning (D14)."""
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def cost_usd(usage: dict[str, int], prices: dict[str, float]) -> float:
    """USD for one request from its token counts and the model's per-1M prices. Local models cost 0.

    `input_tokens` is the whole prompt; the parts read from or written to the cache are inside it and are
    priced at their own rates. A provider that knows the request's price itself - OpenRouter says it, DeepSeek's
    depends on the hour - puts it in `cost_microusd`, which then counts instead.
    """
    if "cost_microusd" in usage:
        return round(usage["cost_microusd"] / 1_000_000, 6)
    if not prices:
        return 0.0
    cached = usage.get("cached_input_tokens", 0)
    cache_write = usage.get("cache_write_tokens", 0)
    fresh_input = max(usage.get("input_tokens", 0) - cached - cache_write, 0)
    million = 1_000_000
    return round(
        fresh_input / million * prices.get("input", 0)
        + cached / million * prices.get("cache_read", prices.get("input", 0))
        + cache_write / million * prices.get("cache_write", prices.get("input", 0))
        + usage.get("output_tokens", 0) / million * prices.get("output", 0),
        6,
    )


class Provider(Protocol):
    """One model provider. Adapters live next to this file (openai_compat for llama-swap, etc.)."""

    name: str

    async def list_models(self) -> list[Model]: ...

    def stream_chat(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        effort: str,
        cancel: asyncio.Event,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yields StreamEvents for one completion.

        `messages` are {role, content} in OpenAI shape, content being the visible text. An assistant turn may
        also carry "provider" and "provider_raw": the message exactly as that provider returned it. An adapter
        replays its own raw messages (Claude needs its thinking blocks back unchanged, D14) and ignores others.
        An assistant turn may carry "tool_calls" [{id, name, input}]; a turn of role "tool" answers one call:
        {tool_call_id, name, content, is_error}. With `tools`, the model may ask for them; the "end" event then
        lists the calls it made.

        Must stop promptly once `cancel` is set, and must end with exactly one kind=="end" event
        (even when cancelled), so the caller always gets final usage and stats.
        """
        ...
