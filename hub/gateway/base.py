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
from datetime import date
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
    prices: dict[str, float] = field(default_factory=dict)  # USD per 1M tokens; a long prompt's tier too (long_tier)


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
    params: dict[str, Any] = field(default_factory=dict)  # the answer's parameters it was asked with (D39)
    settings_version: int = 0  # the version of the model's settings in force then
    fallback: dict[str, Any] | None = None  # the spare answered instead of {from, from_name, reason} (D39)

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


PRICES_FRESH_DAYS = 60  # a price table checked longer ago is marked: compare it with the provider's page (20.1)


def long_tier(prices: dict[str, float], over: int, input_x: float, output_x: float) -> dict[str, float]:
    """The prices with a dearer tier for a prompt over `over` tokens - the whole request at it (stage 20.1): input
    and the cache `input_x` times theirs, output `output_x` times. Kept in the same table as `long_over` and
    `long_<part>`, so a user's own price for a part leaves the tier as the provider has it."""
    dearer = {f"long_{k}": round(v * (output_x if k == "output" else input_x), 6) for k, v in prices.items()}
    return {**prices, "long_over": over, **dearer}


def tier(prices: dict[str, float], prompt_tokens: int) -> dict[str, float]:
    """The prices one request pays: the long tier's when its prompt (cache included) is over `long_over` tokens; a
    part the tier does not name keeps its usual price."""
    usual = {k: v for k, v in prices.items() if not k.startswith("long_")}
    over = prices.get("long_over")
    if over is None or prompt_tokens <= over:
        return usual
    return usual | {k.removeprefix("long_"): v for k, v in prices.items() if k.startswith("long_") and k != "long_over"}


def scheduled(
    schedule: list[tuple[str, dict[str, float]]], day: date, asof: str, *, promo_until: str | None = None
) -> tuple[dict[str, float], dict[str, Any]]:
    """A provider's dated prices (an announced rise; a promotion's end - `promo_until`, the day the usual price is
    back): the prices on `day` and what the catalog row says of them (capabilities: `prices_asof` - when the table
    was checked, `prices_ahead` - the changes to come, which the row makes on their day by itself, `on_day`). The
    first entry is the price from before; its date is not looked at."""
    ahead = [{"since": since, "prices": prices, **({"promo_end": True} if since == promo_until else {})}
             for since, prices in schedule[1:]]  # fmt: skip
    caps: dict[str, Any] = {"prices_asof": asof, **({"promo_until": promo_until} if promo_until else {})}
    row = on_day({"prices": schedule[0][1], "capabilities": {**caps, "prices_ahead": ahead}}, day)
    if not row["capabilities"].get("prices_ahead"):
        row["capabilities"].pop("prices_ahead", None)
    return row["prices"], row["capabilities"]


def on_day(model: dict[str, Any], day: date) -> dict[str, Any]:
    """A catalog row on `day` (stage 20.1): a price change it carries that has come is its price now - a hub that
    runs for weeks does not wait for the next catalog read. A promotion that ended after the table was checked says
    so (`promo_ended`): the usual price after it is the provider's from before, and the window asks to compare."""
    caps = model.get("capabilities") or {}
    ahead = caps.get("prices_ahead") or []
    now = day.isoformat()
    come = [change for change in ahead if change["since"] <= now]
    if not come:
        return model
    caps = {**caps, "prices_ahead": [change for change in ahead if change["since"] > now]}
    if not caps["prices_ahead"]:
        del caps["prices_ahead"]
    ended = [change["since"] for change in come if change.get("promo_end")]
    if ended:
        caps.pop("promo_until", None)
        if ended[-1] > str(caps.get("prices_asof") or ""):
            caps["promo_ended"] = ended[-1]
    return {**model, "prices": come[-1]["prices"], "capabilities": caps}


def cost_usd(usage: dict[str, int], prices: dict[str, float]) -> float:
    """USD for one request from its token counts and the model's per-1M prices. Local models cost 0.

    `input_tokens` is the whole prompt; the parts read from or written to the cache are inside it and are
    priced at their own rates - all of it at the long tier's when the prompt is over its line (`tier`). A provider
    that knows the request's price itself - OpenRouter says it, DeepSeek's depends on the hour - puts it in
    `cost_microusd`, which then counts instead.
    """
    if "cost_microusd" in usage:
        return round(usage["cost_microusd"] / 1_000_000, 6)
    if not prices:
        return 0.0
    prices = tier(prices, usage.get("input_tokens", 0))
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

    def accepts(self, model_ref: str) -> frozenset[str]:
        """The answer's parameters this model takes (hub/model_settings.PARAMS): the window shows only these, and
        the gateway sends only these (Claude has no temperature, D15)."""
        ...

    def stream_chat(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        effort: str,
        cancel: asyncio.Event,
        tools: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yields StreamEvents for one completion.

        `messages` are {role, content} in OpenAI shape, content being the visible text. An assistant turn may
        also carry "provider" and "provider_raw": the message exactly as that provider returned it. An adapter
        replays its own raw messages (Claude needs its thinking blocks back unchanged, D14) and ignores others.
        An assistant turn may carry "tool_calls" [{id, name, input}]; a turn of role "tool" answers one call:
        {tool_call_id, name, content, is_error}. With `tools`, the model may ask for them; the "end" event then
        lists the calls it made. `params` are the answer's parameters the user set (D39), already cut to what
        `accepts` names; the adapter puts them into its provider's own request.

        Must stop promptly once `cancel` is set, and must end with exactly one kind=="end" event
        (even when cancelled), so the caller always gets final usage and stats.
        """
        ...
