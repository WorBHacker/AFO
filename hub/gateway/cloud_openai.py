"""Cloud providers on the OpenAI chat-completions wire (D6): DeepSeek and OpenRouter, each by its key from the window's
Connections (OpenAI itself is on its Responses API: openai_responses.py). They share the llama-swap adapter
(openai_compat.py) and differ in what this file sets:

- the catalog: DeepSeek - the models of the published price table below (PRICES_ASOF) the key can use;
  OpenRouter - the models the user chose in Connections (hundreds are on offer), with the prices OpenRouter itself
  publishes for each, so they are never stale - a long prompt's dearer tier too (`pricing.overrides`, stage 20.1)
- the request: how the hub's effort becomes the provider's reasoning setting
- what is kept of an answer and sent back later: DeepSeek's reasoning_content and OpenRouter's reasoning_details, so a
  model keeps its own thinking across tool calls (D14)
- the price of a request: OpenRouter reports it, DeepSeek's depends on the hour (cheaper off-peak) - both say it in
  usage["cost_microusd"]; the catalog shows DeepSeek's peak price, so estimates and budgets stay on the safe side
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from .base import Model, ProviderError
from .openai_compat import OpenAICompatProvider, wire_messages

log = logging.getLogger("hub.gateway.cloud")

PRICES_ASOF = "2026-10-27"
# DeepSeek: (off-peak, peak) prices; cache_read is a cache hit, input a miss
DEEPSEEK_PRICES: dict[str, tuple[str, dict[str, float], dict[str, float]]] = {
    "deepseek-flash": ("DeepSeek Flash", {"input": 0.15, "cache_read": 0.003, "output": 0.60},
                       {"input": 0.30, "cache_read": 0.006, "output": 1.20}),
    "deepseek-v4-pro": ("DeepSeek V4 Pro", {"input": 0.66, "cache_read": 0.022, "output": 1.98},
                        {"input": 1.32, "cache_read": 0.044, "output": 3.96}),
}  # fmt: skip
DEEPSEEK_PEAK_HOURS_UTC = (range(1, 4), range(6, 10))  # weekdays: 01-04 and 06-10 UTC


def deepseek_peak(when: datetime) -> bool:
    when = when.astimezone(UTC)
    return when.weekday() < 5 and any(when.hour in hours for hours in DEEPSEEK_PEAK_HOURS_UTC)


def _cost_microusd(usage: dict[str, int], prices: dict[str, float]) -> int:
    cached = usage.get("cached_input_tokens", 0)
    fresh = max(usage.get("input_tokens", 0) - cached, 0)
    usd = fresh * prices["input"] + cached * prices["cache_read"] + usage.get("output_tokens", 0) * prices["output"]
    return round(usd)  # per 1M tokens x tokens = micro-USD


class CloudOpenAIProvider(OpenAICompatProvider):
    """The shared part: a key, a name, a cloud locality, errors said briefly."""

    title = "cloud"
    READ_TIMEOUT_S: ClassVar[float] = 600.0  # as Anthropic's, Gemini's and the Responses API's own

    def __init__(self, name: str, base_url: str, key_getter: Callable[[], str | None],
                 transport: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        super().__init__(name, base_url, locality="cloud", transport=transport, key_getter=self._safe(key_getter))
        self.tools_always = True  # one tool list from the first message on (D14)

    def _safe(self, getter: Callable[[], str | None]) -> Callable[[], str | None]:
        def read() -> str | None:
            try:
                return getter()
            except Exception as exc:
                raise ProviderError(f"{self.name}: cannot read the API key from the secret store ({exc})") from exc

        return read

    async def _get(self, path: str, *, auth: bool = True) -> Any:
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            try:
                response = await http.get(f"{self.base_url}{path}", headers=self._headers() if auth else {})
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: cannot list models (network: {type(exc).__name__})") from exc
        if response.status_code != 200:
            raise ProviderError(
                f"{self.name}: cannot list models ({self.describe(response.status_code, response.text)})"
            )
        return response.json()

    def describe(self, status: int, detail: str) -> str:
        if status == 401:
            return "the API key was rejected (401)"
        if status == 402:
            return "no credit left on the account (402)"
        if status == 403:
            return "this key may not use that (403)"
        if status == 404:
            return "no such model (404)"
        if status == 429:
            return "rate limit reached (429), try again later"
        return f"HTTP {status}: {' '.join(detail.split())[:300]}"


class DeepSeekProvider(CloudOpenAIProvider):
    # in thinking mode DeepSeek ignores temperature and top_p (no error): they count once thinking is off
    param_names: ClassVar[dict[str, str]] = {
        "temperature": "temperature",
        "top_p": "top_p",
        "max_tokens": "max_tokens",
        "stop": "stop",
    }

    def __init__(self, key_getter, transport=None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        super().__init__("deepseek", "https://api.deepseek.com", key_getter, transport)
        self._clock = clock

    async def list_models(self) -> list[Model]:
        if not self._key_getter():
            return []
        available = {m.get("id"): m for m in (await self._get("/models")).get("data", [])}
        return [Model(id=f"{self.name}/{ref}", provider=self.name, model_ref=ref, name=label, locality="cloud",
                      capabilities={"context": available[ref].get("context_window") or 1_000_000,
                                    "max_output": available[ref].get("max_output_tokens"),
                                    "prices_asof": PRICES_ASOF, "price_note": "peak"},
                      prices=peak)
                for ref, (label, _off, peak) in DEEPSEEK_PRICES.items() if ref in available]  # fmt: skip

    def request_body(self, model_ref, messages, effort, tools, params=None):
        # thinking on unless turned off; DeepSeek takes the hub's efforts itself (medium -> high, xhigh -> high, max)
        body = super().request_body(model_ref, messages, effort, tools, params)
        body.setdefault("thinking", {"type": "enabled"})
        return body

    def no_thinking(self, body):
        body["thinking"] = {"type": "disabled"}
        body.pop("reasoning_effort", None)

    def wire(self, messages):
        # with tools, DeepSeek wants reasoning_content back on every earlier assistant turn (a 400 otherwise);
        # another model's turns have none to give, so they carry an empty one
        return _with_kept(messages, self.name, "reasoning_content", missing="")

    def kept(self, reasoning, details):
        return {"reasoning_content": reasoning} if reasoning else None

    def price(self, model_ref, counted, reported):
        found = DEEPSEEK_PRICES.get(model_ref)
        if found is None:
            return None
        return _cost_microusd(counted, found[2] if deepseek_peak(self._clock()) else found[1])


class OpenRouterProvider(CloudOpenAIProvider):
    """Hundreds of models behind one key; the hub offers those the user chose (`chosen`)."""

    # OpenRouter's own names; a model that does not take one is routed to a provider that does, or it is ignored
    param_names: ClassVar[dict[str, str]] = {
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "min_p": "min_p",
        "repeat_penalty": "repetition_penalty",
        "max_tokens": "max_tokens",
        "stop": "stop",
    }
    thinking_switch = False

    def __init__(self, key_getter, chosen: Callable[[], Awaitable[list[str]]], transport=None):
        super().__init__("openrouter", "https://openrouter.ai/api", key_getter, transport)
        self._chosen = chosen
        self._efforts: dict[str, list[str] | None] = {}  # model -> the efforts it takes; None: no reasoning setting

    async def everything(self) -> list[dict[str, Any]]:
        """Every model that takes tools and writes text, with its prices per 1M tokens - for choosing."""
        out = []
        for item in (await self._get("/v1/models", auth=bool(self._key_getter()))).get("data", []):
            pricing = item.get("pricing") or {}
            modalities = (item.get("architecture") or {}).get("output_modalities") or ["text"]
            if "tools" not in (item.get("supported_parameters") or []) or "text" not in modalities:
                continue
            if str(item.get("id", "")).endswith(":batch"):  # batch processing, answered hours later: not for a chat
                continue
            try:
                prices = {"input": float(pricing.get("prompt", 0)) * 1e6, "output": float(pricing.get("completion",
                          0)) * 1e6, "cache_read": float(pricing.get("input_cache_read") or pricing.get("prompt",
                          0)) * 1e6}  # fmt: skip
            except TypeError, ValueError:
                continue
            if min(prices.values()) < 0:  # a router entry priced per request, not per token
                continue
            efforts = (item.get("reasoning") or {}).get("supported_efforts") or []
            long = long_prompt(pricing)
            out.append({"id": item["id"], "name": item.get("name") or item["id"],
                        "input": round(prices["input"], 4), "output": round(prices["output"], 4),
                        "cache_read": round(prices["cache_read"], 4), "context": item.get("context_length"),
                        "reasoning": "reasoning" in (item.get("supported_parameters") or []),
                        "efforts": [e for e in efforts if isinstance(e, str)],
                        **({"long": long} if long else {})})  # fmt: skip
            self._efforts[item["id"]] = out[-1]["efforts"] if out[-1]["reasoning"] else None
        return out

    async def list_models(self) -> list[Model]:
        if not self._key_getter():
            return []
        chosen = set(await self._chosen())
        if not chosen:
            return []
        return [Model(id=f"{self.name}/{m['id']}", provider=self.name, model_ref=m["id"], name=m["name"],
                      locality="cloud", capabilities={"context": m["context"], "reasoning": m["reasoning"]},
                      prices={"input": m["input"], "output": m["output"], "cache_read": m["cache_read"],
                              **m.get("long", {})})
                for m in await self.everything() if m["id"] in chosen]  # fmt: skip

    def request_body(self, model_ref, messages, effort, tools, params=None):
        body = super().request_body(model_ref, messages, effort, tools, params)
        body.pop("reasoning_effort", None)
        body.pop("stream_options", None)  # OpenRouter always reports usage now; the option is deprecated
        efforts = self._efforts.get(model_ref, [])
        if efforts is not None:  # unknown (not listed yet) or a reasoning model: say the effort it takes
            body["reasoning"] = {"effort": nearest_effort(effort, efforts)}
        return body

    def wire(self, messages):
        return _with_kept(messages, self.name, "reasoning_details")

    def price(self, model_ref, counted, reported):
        cost = reported.get("cost")  # USD, what OpenRouter charged for this request
        return round(float(cost) * 1_000_000) if isinstance(cost, int | float) else None

    def kept(self, reasoning, details):
        joined = join_details(details)
        return {"reasoning_details": joined} if joined else None


def long_prompt(pricing: dict[str, Any]) -> dict[str, float]:
    """OpenRouter's dearer tiers for a long prompt (`pricing.overrides` entries with `min_prompt_tokens`; those by the
    hour are not this) as the price table's long tier: from the first threshold, at the dearest tier's prices - an
    estimate stays on the safe side, and the charge itself comes in the answer's usage. Per token there, per 1M here;
    a part an entry does not name keeps its usual price."""
    tiers = [o for o in pricing.get("overrides") or [] if isinstance(o, dict)
             and isinstance(o.get("min_prompt_tokens"), int) and not any(k.startswith("utc_") for k in o)]  # fmt: skip
    if not tiers:
        return {}
    out = {"long_over": min(t["min_prompt_tokens"] for t in tiers) - 1}
    for ours, theirs in (("input", "prompt"), ("output", "completion"), ("cache_read", "input_cache_read")):
        try:
            found = [float(t[theirs]) * 1e6 for t in tiers if t.get(theirs) is not None]
        except TypeError, ValueError:
            return {}
        if found:
            out[f"long_{ours}"] = round(max(found), 4)
    return out if len(out) > 1 else {}


EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"]


def nearest_effort(wanted: str, supported: list[str]) -> str:
    """The hub's effort if the model takes it, else the strongest it takes below it (or its weakest)."""
    if not supported or wanted in supported:
        return wanted
    rank = {e: n for n, e in enumerate(EFFORTS)}
    below = [e for e in supported if rank.get(e, -1) <= rank.get(wanted, 2) and e in rank]
    return max(below, key=rank.__getitem__) if below else min((e for e in supported if e in rank),
                                                               key=rank.__getitem__, default=wanted)  # fmt: skip


def join_details(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenRouter streams reasoning_details in pieces; consecutive pieces of one block (same index and type) are one
    block again: their text, summary or data joined, the other fields from the last piece (a signature comes last)."""
    blocks: list[dict[str, Any]] = []
    for piece in fragments:
        last = blocks[-1] if blocks else None
        if last is not None and last.get("index") == piece.get("index") and last.get("type") == piece.get("type"):
            for key in ("text", "summary", "data"):
                if isinstance(piece.get(key), str):
                    last[key] = (last.get(key) or "") + piece[key]
            last.update({k: v for k, v in piece.items() if k not in ("text", "summary", "data") and v is not None})
        else:
            blocks.append(dict(piece))
    return blocks


def _with_kept(messages: list[dict[str, Any]], provider: str, field: str, missing: Any = None) -> list[dict[str, Any]]:
    """The history on the wire, with what this provider gave earlier and wants back (`field` of its stored answer)
    on its own assistant turns; other turns get `missing` when it is not None."""
    wire = wire_messages(messages)
    own = iter([m.get("provider_raw") if m.get("provider") == provider else None
                for m in messages if m["role"] == "assistant"])  # fmt: skip
    for entry in wire:
        if entry["role"] == "assistant":
            raw = next(own, None)
            if raw and raw.get(field):
                entry[field] = raw[field]
            elif missing is not None:
                entry[field] = missing
    return wire
