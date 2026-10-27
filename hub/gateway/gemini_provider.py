"""The Gemini adapter (stage 6.5, D6, D7): Google's models by API key, over the Gemini API's REST generateContent
(v1beta) with httpx - no extra SDK.

What it takes care of (ai.google.dev, read 2026-10-27; Google steers new work to its Interactions API, generateContent
"remains fully supported"):
- streaming: POST models/{model}:streamGenerateContent?alt=sse, the key in the x-goog-api-key header (never in the
  address, so never in a log); each SSE `data:` line is one GenerateContentResponse
- roles user / model; the hub's answer to a function call goes back in a user turn as functionResponse {id, name,
  response}; several calls are answered together, in order (FC1, FC2, FR1, FR2)
- thinking: generationConfig.thinkingConfig - thinkingLevel (low / medium / high) on Gemini 3 and later, a
  thinkingBudget on 2.5; includeThoughts returns summaries as parts marked thought: true (shown as reasoning, never
  sent as input text, D14). Thinking tokens bill as output
- thought signatures: Gemini's own turns are replayed from the stored raw content, every part as received; streamed
  text is joined only between parts that carry no signature. A function call of another model in the history gets
  the documented placeholder signature, so the current turn's check passes
- tools: functionDeclarations with parametersJsonSchema - the hub's JSON schemas as they are
- usage: promptTokenCount is the whole prompt, cachedContentTokenCount the part read from Google's implicit cache;
  output is candidatesTokenCount + thoughtsTokenCount
- a blocked prompt (promptFeedback.blockReason) or a stop for safety is a refusal; its tokens still count
- prices: the published paid-tier table below; a model without a price here is not offered. Pro models cost more
  above 200K prompt tokens - not modelled. Google's free tier may use requests to improve its products (D10: the
  connection says so)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any

import httpx

from .base import Model, ProviderError, StreamEvent

log = logging.getLogger("hub.gateway.gemini")

API = "https://generativelanguage.googleapis.com/v1beta"
PRICES_ASOF = "2026-10-27"
# USD per 1M tokens, Standard paid tier (output includes thinking). Each model: [(valid from, prices), ...] - Google
# has announced rises for some; the newest that has started applies.
_FLASH_38 = [("2026-01-01", {"input": 0.75, "output": 3.75, "cache_read": 0.075}),
             ("2027-01-01", {"input": 1.50, "output": 7.50, "cache_read": 0.15})]  # fmt: skip
_CATALOG: dict[str, tuple[str, list[tuple[str, dict[str, float]]]]] = {
    "gemini-4-argon": ("Gemini 4 Argon", [("2026-01-01", {"input": 2.0, "output": 10.0, "cache_read": 0.10}),
                                          ("2026-12-01", {"input": 4.0, "output": 20.0, "cache_read": 0.20})]),
    "gemini-3.8-flash": ("Gemini 3.8 Flash", _FLASH_38),
    "gemini-3.6-flash": ("Gemini 3.6 Flash", _FLASH_38),
    "gemini-3.1-pro-preview": ("Gemini 3.1 Pro (preview)",
                               [("2026-01-01", {"input": 2.0, "output": 12.0, "cache_read": 0.20})]),
    "gemini-3-flash-preview": ("Gemini 3 Flash (preview)",
                               [("2026-01-01", {"input": 0.50, "output": 3.0, "cache_read": 0.05})]),
    "gemini-3.5-flash-lite": ("Gemini 3.5 Flash-Lite", [("2026-01-01", {"input": 0.30, "output": 2.50,
                                                                        "cache_read": 0.03})]),
    "gemini-3.1-flash-lite": ("Gemini 3.1 Flash-Lite", [("2026-01-01", {"input": 0.25, "output": 1.50,
                                                                        "cache_read": 0.025})]),
    "gemini-2.5-pro": ("Gemini 2.5 Pro", [("2025-01-01", {"input": 1.25, "output": 10.0, "cache_read": 0.125})]),
    "gemini-2.5-flash": ("Gemini 2.5 Flash", [("2025-01-01", {"input": 0.30, "output": 2.50, "cache_read": 0.03})]),
    "gemini-2.5-flash-lite": ("Gemini 2.5 Flash-Lite",
                              [("2025-01-01", {"input": 0.10, "output": 0.40, "cache_read": 0.01})]),
}  # fmt: skip
# the hub's effort as Gemini takes it: a level on Gemini 3 and later ("minimal" is refused by some), a budget on 2.5
LEVELS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
BUDGETS = {"low": 1024, "medium": 4096, "high": 12288, "xhigh": 24576, "max": -1}  # -1: as much as it needs
# the answer's parameters Gemini takes (D39), into generationConfig
GENERATION = {"temperature": "temperature", "top_p": "topP", "top_k": "topK", "max_tokens": "maxOutputTokens",
              "stop": "stopSequences"}  # fmt: skip
NO_ID = "gemini-call-"  # a call Gemini sent without an id: the hub's id, not sent back
PLACEHOLDER_SIGNATURE = "skip_thought_signature_validator"  # documented, for calls Gemini did not make itself
REFUSED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
CHARS_PER_TOKEN_ESTIMATE = 3  # only for a run stopped before Google reported its usage


def prices_on(schedule: list[tuple[str, dict[str, float]]], day: date) -> dict[str, float]:
    started = [prices for since, prices in schedule if date.fromisoformat(since) <= day]
    return started[-1] if started else schedule[0][1]


class GeminiProvider:
    def __init__(
        self,
        key_getter: Callable[[], str | None],
        name: str = "gemini",
        api: str = API,
        transport: httpx.AsyncBaseTransport | None = None,
        today: Callable[[], date] = date.today,
    ):
        self.name = name
        self.tools_always = True  # one tool list from the first message: the conversation's turns stay replayable
        self._key_getter = key_getter  # read on every use: the user may add or change the key while the hub runs
        self._api = api.rstrip("/")
        self._transport = transport  # tests put a mock here
        self._today = today
        self._max_output: dict[str, int] = {}

    def _key(self) -> str | None:
        try:
            return self._key_getter()
        except Exception as exc:
            raise ProviderError(f"{self.name}: cannot read the API key from the secret store ({exc})") from exc

    async def list_models(self) -> list[Model]:
        """The priced Gemini models this key can use. No key - no models, and no network call."""
        key = self._key()
        if not key:
            return []
        available: dict[str, dict[str, Any]] = {}
        page = ""
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            for _ in range(20):
                params = {"pageSize": 1000, **({"pageToken": page} if page else {})}
                try:
                    response = await http.get(f"{self._api}/models", params=params, headers={"x-goog-api-key": key})
                except httpx.HTTPError as exc:
                    raise ProviderError(f"{self.name}: cannot list models (network: {type(exc).__name__})") from exc
                if response.status_code != 200:
                    raise ProviderError(f"{self.name}: cannot list models ({_describe(response)})")
                body = response.json()
                for item in body.get("models") or []:
                    if "generateContent" in (item.get("supportedGenerationMethods") or []):
                        available[str(item.get("name", "")).removeprefix("models/")] = item
                page = body.get("nextPageToken") or ""
                if not page:
                    break
        unpriced = sorted(ref for ref in set(available) - set(_CATALOG) if ref.startswith("gemini-"))
        if unpriced:
            log.info("Gemini models without a price in this build, not offered: %s", ", ".join(unpriced))
        models = []
        for ref, (label, schedule) in _CATALOG.items():
            info = available.get(ref)
            if info is None:
                continue
            if info.get("outputTokenLimit"):
                self._max_output[ref] = int(info["outputTokenLimit"])
            models.append(
                Model(
                    id=f"{self.name}/{ref}",
                    provider=self.name,
                    model_ref=ref,
                    name=info.get("displayName") or label,
                    locality="cloud",
                    capabilities={
                        "vision": True,
                        "context": info.get("inputTokenLimit"),
                        "max_output": info.get("outputTokenLimit"),
                        "prices_asof": PRICES_ASOF,
                    },
                    prices=prices_on(schedule, self._today()),
                )
            )
        return models

    def accepts(self, model_ref: str) -> frozenset[str]:
        return frozenset(GENERATION)

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
            raise ProviderError("no Gemini API key is set: add it in the window's Connections")
        system, contents = to_wire(messages, self.name)
        thinking: dict[str, Any] = {"includeThoughts": True}
        if model_ref.startswith("gemini-2."):
            thinking["thinkingBudget"] = BUDGETS.get(effort, -1)
        else:
            thinking["thinkingLevel"] = LEVELS.get(effort, "high")
        config: dict[str, Any] = {"thinkingConfig": thinking}
        if model_ref in self._max_output:
            config["maxOutputTokens"] = self._max_output[model_ref]
        for name, wire in GENERATION.items():
            if name in params:
                config[wire] = params[name]
        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parametersJsonSchema": t["parameters"]}
                for t in tools]}]  # fmt: skip

        started = time.monotonic()
        first_at: float | None = None
        streamed_chars = 0
        parts: list[dict[str, Any]] = []  # the model's turn as it will be replayed
        usage: dict[str, Any] = {}
        finish: str | None = None
        blocked: str | None = None
        timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
        url = f"{self._api}/models/{model_ref}:streamGenerateContent"
        try:
            async with (
                httpx.AsyncClient(timeout=timeout, transport=self._transport) as http,
                http.stream("POST", url, params={"alt": "sse"}, json=body, headers={"x-goog-api-key": key}) as response,
            ):
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderError(f"{self.name}: {_describe(response)}")
                async for line in response.aiter_lines():
                    if cancel.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[5:].strip() or "{}")
                    usage = chunk.get("usageMetadata") or usage
                    blocked = (chunk.get("promptFeedback") or {}).get("blockReason") or blocked
                    for candidate in (chunk.get("candidates") or [])[:1]:
                        finish = candidate.get("finishReason") or finish
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            _add_part(parts, part)
                            text = part.get("text") or ""
                            if text:
                                first_at = first_at or time.monotonic()
                                streamed_chars += len(text)
                                yield StreamEvent("reasoning" if part.get("thought") else "content", text)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: cannot reach the Gemini API ({type(exc).__name__})") from exc
        except ValueError as exc:
            raise ProviderError(f"{self.name}: an answer that could not be read; try again") from exc
        if finish == "MALFORMED_FUNCTION_CALL":
            raise ProviderError(f"{self.name}: the model sent a function call that could not be read; try again")

        counted = _usage(usage)
        if cancel.is_set() and not counted["output_tokens"]:
            counted["output_tokens"] = math.ceil(streamed_chars / CHARS_PER_TOKEN_ESTIMATE)
            counted["estimated"] = 1
        calls = []
        for index, part in enumerate(p for p in parts if "functionCall" in p):
            call = part["functionCall"]
            calls.append({"id": call.get("id") or f"{NO_ID}{index}", "name": call.get("name", ""),
                          "input": call.get("args") or {}})  # fmt: skip
        refusal = None
        if blocked or finish in REFUSED:
            refusal = f"Gemini declined this request ({blocked or finish}). Rephrase it or choose another model."
        yield StreamEvent(
            "end",
            usage=counted,
            stats=_stats(counted["output_tokens"], started, first_at),
            stop_reason={"MAX_TOKENS": "max_tokens", "STOP": "end_turn"}.get(finish or "", (finish or "").lower()),
            raw={"content": {"role": "model", "parts": parts}, "finishReason": finish},
            refusal=refusal,
            tool_calls=calls,
        )


def _add_part(parts: list[dict[str, Any]], part: dict[str, Any]) -> None:
    """A streamed part joins the turn: text continues the text before it when neither carries a signature and both
    are of one kind (thought or answer); anything signed, and any call, stays the part it came as."""
    last = parts[-1] if parts else None
    joinable = (
        last is not None
        and set(part) <= {"text", "thought"}
        and set(last) <= {"text", "thought"}
        and bool(last.get("thought")) == bool(part.get("thought"))
    )
    if joinable:
        last["text"] = last.get("text", "") + part.get("text", "")
    elif part.get("text") or set(part) - {"text", "thought"}:
        parts.append(dict(part))


def to_wire(messages: list[dict[str, Any]], provider_name: str) -> tuple[str, list[dict[str, Any]]]:
    """The conversation as Gemini takes it: the system instruction apart, then user / model turns of parts.

    Gemini's own turns come back from their stored raw content, unchanged. A system message inside the conversation
    (the hub's context, D22) joins the user turn before it as a marked text part - deterministically, so the same
    history always gives the same request. The hub's tool results go back as functionResponse parts of a user turn.
    """
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []

    def add(role: str, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["parts"].extend(parts)
        else:
            turns.append({"role": role, "parts": parts})

    for message in messages:
        role = message["role"]
        if role == "system":
            if message["content"] and not turns:
                system_parts.append(message["content"])
            elif message["content"]:
                add("user", [{"text": f"<context>\n{message['content']}\n</context>"}])
        elif role == "tool":
            response = {"error" if message.get("is_error") else "output": message["content"] or "(empty)"}
            answer: dict[str, Any] = {"name": message.get("name", ""), "response": response}
            if not str(message["tool_call_id"]).startswith(NO_ID):
                answer["id"] = message["tool_call_id"]
            add("user", [{"functionResponse": answer}])
        elif role == "assistant":
            raw = message.get("provider_raw")
            if raw and message.get("provider") == provider_name:
                add("model", [dict(p) for p in (raw.get("content") or {}).get("parts") or []])
                continue
            parts: list[dict[str, Any]] = [{"text": message["content"]}] if message["content"] else []
            for index, call in enumerate(message.get("tool_calls") or []):
                part: dict[str, Any] = {"functionCall": {"id": call["id"], "name": call["name"], "args": call["input"]}}
                if index == 0:  # another model's call: Gemini checks the first call of a turn for a signature
                    part["thoughtSignature"] = PLACEHOLDER_SIGNATURE
                parts.append(part)
            add("model", parts)
        elif message["content"]:
            add("user", [{"text": message["content"]}])
    return "\n\n".join(system_parts), turns


def _usage(meta: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens": int(meta.get("promptTokenCount") or 0),
        "cached_input_tokens": int(meta.get("cachedContentTokenCount") or 0),
        "output_tokens": int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0),
    }


def _stats(output_tokens: int, started: float, first_at: float | None) -> dict[str, float]:
    end = time.monotonic()
    tok_s = output_tokens / (end - first_at) if first_at and end > first_at else 0.0
    return {
        "tok_s": round(tok_s, 1),
        "ttft_s": round(first_at - started, 2) if first_at else 0.0,
        "total_s": round(end - started, 2),
    }


def _describe(response: httpx.Response) -> str:
    """A short, safe description of a refusal: Google's message, never the request or the key."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    reasons = {d.get("reason") for d in error.get("details") or [] if isinstance(d, dict)}
    code = response.status_code
    if "API_KEY_INVALID" in reasons or code == 401:
        return "the API key was rejected"
    if code == 403:
        return "this key may not use that (403)"
    if code == 404:
        return "no such model (404)"
    if code == 429:
        return "rate limit or quota reached (429), try again later"
    if code == 402:
        return "the prepaid credit has run out (402)"
    message = str(error.get("message") or response.text[:200]).strip()
    return f"HTTP {code}: {message[:300]}"
