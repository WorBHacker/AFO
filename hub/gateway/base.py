"""Shared gateway types: the model record, the unified message blocks, the stream event, providers.

A message's content is a list of blocks in the hub's own format. Stage 1 uses two kinds:
    {"type": "text", "text": "..."}        the visible answer
    {"type": "reasoning", "text": "..."}   the model's thinking (not sent back to a model as input, D14)
Each provider adapter turns these into its own wire format and back.
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
    """

    kind: str
    text: str = ""
    usage: dict[str, int] | None = None
    stats: dict[str, float] | None = None


@dataclass
class Completion:
    """What a finished run produced, assembled from the stream."""

    text: str = ""
    reasoning: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stats: dict[str, float] = field(default_factory=dict)
    cancelled: bool = False

    def blocks(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if self.reasoning:
            out.append(reasoning_block(self.reasoning))
        out.append(text_block(self.text))
        return out


def text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def reasoning_block(text: str) -> dict[str, str]:
    return {"type": "reasoning", "text": text}


def blocks_to_prompt_text(blocks: list[dict[str, Any]]) -> str:
    """The text a model should receive as input: visible text only, never reasoning (D14)."""
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def cost_usd(usage: dict[str, int], prices: dict[str, float]) -> float:
    """USD for one request from its token counts and the model's per-1M prices. Local models cost 0."""
    if not prices:
        return 0.0
    cached = usage.get("cached_input_tokens", 0)
    fresh_input = max(usage.get("input_tokens", 0) - cached, 0)
    million = 1_000_000
    return round(
        fresh_input / million * prices.get("input", 0)
        + cached / million * prices.get("cache_read", prices.get("input", 0))
        + usage.get("cache_write_tokens", 0) / million * prices.get("cache_write", 0)
        + usage.get("output_tokens", 0) / million * prices.get("output", 0),
        6,
    )


class Provider(Protocol):
    """One model provider. Adapters live next to this file (openai_compat for llama-swap, etc.)."""

    name: str

    async def list_models(self) -> list[Model]: ...

    def stream_chat(
        self, model_ref: str, messages: list[dict[str, str]], effort: str, cancel: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        """Yields StreamEvents for one completion. `messages` are {role, content} in OpenAI shape.

        Must stop promptly once `cancel` is set, and must end with exactly one kind=="end" event
        (even when cancelled), so the caller always gets final usage and stats.
        """
        ...
