"""The OpenAI adapter (D6): GPT models by API key over the Responses API (`POST /v1/responses`), streamed, with httpx.

Why not chat completions, which the other cloud providers use: there OpenAI's current models either take no tools at
all (GPT-6 Astra, GPT-6.1 Sol and Terra) or take them only with reasoning off (the rest) - and the hub's chats and
agents work through tools. The Responses API has both (developers.openai.com, read 2026-10-27).

What it takes care of:
- stateless (`store: false`): nothing is kept at OpenAI; each request carries the history. The model's reasoning
  items come back encrypted (by default now) and every output item is replayed on its own turns, from the stored raw
  answer, as received - "preserve every item in the response's output array" - so it keeps its thinking across tool
  calls (D14); a readable summary (opted in) streams as reasoning
- effort: the hub's effort as each model takes it (its own list below)
- tools: flat function definitions, not strict - the hub's schemas have optional fields
- usage: input (with cached and cache-written parts) and output (thinking included); a prompt over 272K tokens costs
  2x input / cache and 1.5x output for the whole request - priced here, the catalog shows the normal price
- the catalog: the models of the price table (PRICES_ASOF) the key can use
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from .base import Model, ProviderError, StreamEvent, cost_usd

log = logging.getLogger("hub.gateway.openai")

API = "https://api.openai.com/v1"
PRICES_ASOF = "2026-10-27"
LONG_PROMPT = 272_000  # over this, the whole request costs 2x input and cache, 1.5x output
ALL_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]
REASONING_DELTAS = ("response.reasoning_summary_text.delta", "response.reasoning_text.delta")  # summary, or raw text
# id: (name, USD per 1M tokens, the efforts it takes)
MODELS: dict[str, tuple[str, dict[str, float], list[str]]] = {
    "gpt-6-astra": ("GPT-6 Astra", {"input": 10.0, "cache_read": 1.0, "cache_write": 12.5, "output": 50.0},
                    ALL_EFFORTS[1:]),
    "gpt-6.1-sol": ("GPT-6.1 Sol", {"input": 2.0, "cache_read": 0.10, "cache_write": 2.5, "output": 10.0},
                    ALL_EFFORTS[1:]),
    "gpt-6.1-terra": ("GPT-6.1 Terra", {"input": 1.0, "cache_read": 0.05, "cache_write": 1.25, "output": 5.0},
                      ALL_EFFORTS),
    "gpt-6-luna": ("GPT-6 Luna", {"input": 0.10, "cache_read": 0.01, "cache_write": 0.125, "output": 0.50},
                   ALL_EFFORTS),
    "gpt-5.6-sol": ("GPT-5.6 Sol", {"input": 4.0, "cache_read": 0.40, "cache_write": 5.0, "output": 20.0},
                    ALL_EFFORTS),
    "gpt-5.5": ("GPT-5.5", {"input": 5.0, "cache_read": 0.50, "output": 30.0}, ALL_EFFORTS[:-1]),
    "gpt-5.4-mini": ("GPT-5.4 mini", {"input": 0.75, "cache_read": 0.075, "output": 4.50}, ALL_EFFORTS[:-1]),
}  # fmt: skip


def effort_for(model_ref: str, wanted: str) -> str:
    """The hub's effort, or the strongest one below it that the model takes."""
    takes = MODELS[model_ref][2] if model_ref in MODELS else ALL_EFFORTS
    if wanted in takes:
        return wanted
    below = [e for e in takes if ALL_EFFORTS.index(e) <= ALL_EFFORTS.index(wanted)] if wanted in ALL_EFFORTS else []
    return below[-1] if below else takes[0]


class OpenAIProvider:
    def __init__(self, key_getter: Callable[[], str | None], name: str = "openai", api: str = API,
                 transport: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        self.name = name
        self.tools_always = True  # one tool list from the first message on (D14)
        self._key_getter = key_getter  # read on every use: the user may add or change the key while the hub runs
        self._api = api.rstrip("/")
        self._transport = transport  # tests put a mock here

    def _key(self) -> str | None:
        try:
            return self._key_getter()
        except Exception as exc:
            raise ProviderError(f"{self.name}: cannot read the API key from the secret store ({exc})") from exc

    async def list_models(self) -> list[Model]:
        """The priced models this key can use. No key - no models, and no network call."""
        key = self._key()
        if not key:
            return []
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            try:
                response = await http.get(f"{self._api}/models", headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: cannot list models (network: {type(exc).__name__})") from exc
        if response.status_code != 200:
            raise ProviderError(f"{self.name}: cannot list models ({_describe(response.status_code, response.text)})")
        available = {m.get("id") for m in response.json().get("data", [])}
        return [Model(id=f"{self.name}/{ref}", provider=self.name, model_ref=ref, name=label, locality="cloud",
                      capabilities={"vision": True, "prices_asof": PRICES_ASOF}, prices=prices)
                for ref, (label, prices, _efforts) in MODELS.items() if ref in available]  # fmt: skip

    async def stream_chat(
        self,
        model_ref: str,
        messages: list[dict[str, Any]],
        effort: str,
        cancel: asyncio.Event,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        key = self._key()
        if not key:
            raise ProviderError("no OpenAI API key is set: add it in the window's Connections")
        instructions, items = to_input(messages, self.name)
        body: dict[str, Any] = {
            "model": model_ref,
            "input": items,
            "stream": True,
            "store": False,  # nothing kept at OpenAI: the history travels with each request
            "stream_options": {"include_obfuscation": False},  # no padding field on every delta
            "reasoning": {"effort": effort_for(model_ref, effort), "summary": "auto"},
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                              "parameters": t["parameters"], "strict": False} for t in tools]  # fmt: skip

        started = time.monotonic()
        first_at: float | None = None
        streamed_chars = 0
        output: list[dict[str, Any]] = []  # the answer's items as OpenAI finished them: replayed later
        refusal: list[str] = []
        final: dict[str, Any] = {}
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with (
                httpx.AsyncClient(timeout=timeout, transport=self._transport) as http,
                http.stream("POST", f"{self._api}/responses", json=body, headers=headers) as response,
            ):
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(f"{self.name}: {_describe(response.status_code, detail)}")
                async for line in response.aiter_lines():
                    if cancel.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip() or "{}")
                    kind = event.get("type", "")
                    if kind == "response.output_text.delta" and event.get("delta"):
                        first_at = first_at or time.monotonic()
                        streamed_chars += len(event["delta"])
                        yield StreamEvent("content", event["delta"])
                    elif kind in REASONING_DELTAS and event.get("delta"):
                        first_at = first_at or time.monotonic()
                        streamed_chars += len(event["delta"])
                        yield StreamEvent("reasoning", event["delta"])
                    elif kind == "response.refusal.delta":
                        refusal.append(event.get("delta", ""))
                    elif kind == "response.output_item.done" and isinstance(event.get("item"), dict):
                        output.append(event["item"])
                    elif kind in ("response.completed", "response.incomplete"):
                        final = event.get("response") or {}
                    elif kind == "response.failed":
                        error = ((event.get("response") or {}).get("error") or {}).get("message", "the answer failed")
                        raise ProviderError(f"{self.name}: {error}")
                    elif kind == "error":
                        raise ProviderError(f"{self.name}: {event.get('message') or 'the answer broke off'}")
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: cannot reach the OpenAI API ({type(exc).__name__})") from exc
        except ValueError as exc:
            raise ProviderError(f"{self.name}: an answer that could not be read; try again") from exc

        counted = _usage(final.get("usage") or {})
        if cancel.is_set() and not counted["output_tokens"]:
            counted["output_tokens"] = -(-streamed_chars // 3)
            counted["estimated"] = 1
        if counted["input_tokens"] > LONG_PROMPT and model_ref in MODELS:
            counted["cost_microusd"] = long_prompt_cost(counted, MODELS[model_ref][1])
        reason = (final.get("incomplete_details") or {}).get("reason")
        calls = []
        for item in output:
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = {"__invalid_json__": item.get("arguments", "")}
                calls.append({"id": item.get("call_id") or item.get("id", ""), "name": item.get("name", ""),
                              "input": args})  # fmt: skip
        said_no = "".join(refusal).strip()
        refused = f"OpenAI declined: {said_no[:300]}" if said_no else None
        if reason == "content_filter":
            refused = refused or "OpenAI stopped the answer (content filter). Rephrase it or choose another model."
        yield StreamEvent(
            "end",
            usage=counted,
            stats=_stats(counted["output_tokens"], started, first_at),
            stop_reason="max_tokens" if reason == "max_output_tokens" else (reason or final.get("status")),
            raw={"output": output},
            refusal=refused,
            tool_calls=calls,
        )


def to_input(messages: list[dict[str, Any]], provider_name: str) -> tuple[str, list[dict[str, Any]]]:
    """The conversation as Responses input items. System messages before the first turn are the instructions; the
    hub's later context messages stay where they are, as developer messages. OpenAI's own turns come back from their
    stored items (encrypted reasoning, calls, text) unchanged; other models' turns as text and function calls."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            if message["content"] and not items:
                instructions.append(message["content"])
            elif message["content"]:
                items.append({"role": "developer", "content": message["content"]})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"] or "(empty)"})  # fmt: skip
        elif role == "assistant":
            raw = message.get("provider_raw")
            if raw and message.get("provider") == provider_name and raw.get("output"):
                items += [dict(item) for item in raw["output"]]
                continue
            if message["content"]:
                items.append({"role": "assistant", "content": message["content"]})
            items += [{"type": "function_call", "call_id": c["id"], "name": c["name"],
                       "arguments": json.dumps(c["input"], ensure_ascii=False)}
                      for c in message.get("tool_calls") or []]  # fmt: skip
        elif message["content"]:
            items.append({"role": "user", "content": message["content"]})
    return "\n\n".join(instructions), items


def long_prompt_cost(usage: dict[str, int], prices: dict[str, float]) -> int:
    """A request over LONG_PROMPT tokens: 2x input and cache prices, 1.5x output, for all of it (micro-USD)."""
    doubled = {k: v * 2 for k, v in prices.items() if k != "output"} | {"output": prices["output"] * 1.5}
    return round(cost_usd(usage, doubled) * 1_000_000)


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    details = usage.get("input_tokens_details") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(details.get("cached_tokens") or 0),
        "cache_write_tokens": int(details.get("cache_write_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _stats(output_tokens: int, started: float, first_at: float | None) -> dict[str, float]:
    end = time.monotonic()
    tok_s = output_tokens / (end - first_at) if first_at and end > first_at else 0.0
    return {
        "tok_s": round(tok_s, 1),
        "ttft_s": round(first_at - started, 2) if first_at else 0.0,
        "total_s": round(end - started, 2),
    }


def _describe(status: int, detail: str) -> str:
    """A short, safe description of a refusal: OpenAI's message, never the request or the key."""
    try:
        message = (json.loads(detail).get("error") or {}).get("message") or ""
    except ValueError, AttributeError:
        message = detail
    if status == 401:
        return "the API key was rejected (401)"
    if status == 403:
        return "this key may not use that (403)"
    if status == 404:
        return "no such model (404)"
    if status == 429:
        return "rate limit or quota reached (429), try again later"
    return f"HTTP {status}: {' '.join(str(message).split())[:300]}"
