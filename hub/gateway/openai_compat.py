"""An OpenAI-compatible adapter. Serves llama-swap (D5); the cloud ones - OpenAI, DeepSeek, OpenRouter - build on it
(hub/gateway/cloud_openai.py) through its hooks: the request body, the history on the wire, the raw answer kept.

llama-swap streams model-swap progress in the reasoning field, prefixed `llama-swap loading model:`
(sendLoadingState). Those lines are separated out as "loading" events, so they never land in the saved
reasoning, and the time-to-first-token clock starts only once loading is done.

Tools go out as OpenAI functions (llama.cpp serves them for models started with --jinja); a call streams in
fragments by index and is put together at the end. Arguments that are not valid JSON are passed on marked
({"__invalid_json__": text}), so the chat answers the model with an error instead of failing the run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import Model, ProviderError, StreamEvent

log = logging.getLogger("hub.gateway.openai")

LOADING_PREFIX = "llama-swap loading model:"


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        locality: str = "local",
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        key_getter=None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.locality = locality
        self._key_getter = key_getter or (lambda: api_key)  # read on every use: the user may change the key
        self._transport = transport  # tests inject a mock; production leaves it None

    def _headers(self) -> dict[str, str]:
        key = self._key_getter()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def list_models(self) -> list[Model]:
        async with httpx.AsyncClient(timeout=5.0, transport=self._transport) as client:
            try:
                response = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name}: cannot list models ({exc})") from exc
        models = []
        for item in response.json().get("data", []):
            ref = item["id"]
            meta = item.get("meta") or {}
            models.append(
                Model(
                    id=f"{self.name}/{ref}",
                    provider=self.name,
                    model_ref=ref,
                    name=item.get("name") or ref,
                    locality=self.locality,
                    capabilities={
                        "vision": bool(meta.get("vision")),
                        "context": meta.get("context"),
                        "description": item.get("description", ""),
                    },
                    prices={},  # local models are free; a cloud OpenAI-compat provider fills this in
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
    ) -> AsyncIterator[StreamEvent]:
        body = self.request_body(model_ref, messages, effort, tools)
        calls: dict[int, dict[str, str]] = {}  # tool calls being put together, by index
        thought: list[str] = []  # the reasoning as it came, for a provider that wants it back (DeepSeek)
        details: list[dict[str, Any]] = []  # OpenRouter's reasoning_details, to be sent back unchanged
        started = time.monotonic()
        first_at: float | None = None
        in_loading = False
        chunks = 0
        usage: dict = {}
        timings: dict = {}
        finish_reason: str | None = None
        # read has no timeout: a local model can be slow between tokens; connect/write still bounded
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        try:
            async with (
                httpx.AsyncClient(timeout=timeout, transport=self._transport) as client,
                client.stream(
                    "POST", f"{self.base_url}/v1/chat/completions", json=body, headers=self._headers()
                ) as response,
            ):
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise ProviderError(f"{self.name}: {self.describe(response.status_code, detail)}")
                async for line in response.aiter_lines():
                    if cancel.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    if isinstance(obj.get("error"), dict):  # a failure after the stream began (OpenRouter)
                        raise ProviderError(f"{self.name}: {obj['error'].get('message') or 'the answer broke off'}")
                    usage = obj.get("usage") or usage
                    timings = obj.get("timings") or timings
                    if not obj.get("choices"):
                        continue
                    finish_reason = obj["choices"][0].get("finish_reason") or finish_reason
                    delta = obj["choices"][0].get("delta") or {}
                    for part in delta.get("tool_calls") or []:
                        call = calls.setdefault(part.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                        call["id"] = part.get("id") or call["id"]
                        function = part.get("function") or {}
                        call["name"] += function.get("name") or ""
                        call["args"] += function.get("arguments") or ""
                    think = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    text = delta.get("content") or ""
                    details += [d for d in delta.get("reasoning_details") or [] if isinstance(d, dict)]
                    if think and not in_loading and first_at is None and think.lstrip().startswith(LOADING_PREFIX):
                        in_loading = True
                    if in_loading:
                        yield StreamEvent("loading", think or text)
                        if "Done!" in (think + text):
                            in_loading = False
                            started = time.monotonic()  # TTFT is measured after the swap finishes
                        continue
                    if think or text:
                        first_at = first_at or time.monotonic()
                        chunks += 1
                    if think:
                        thought.append(think)
                        yield StreamEvent("reasoning", think)
                    if text:
                        yield StreamEvent("content", text)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc
        yield StreamEvent(
            "end",
            usage=self.priced(model_ref, _usage(usage, chunks), usage),
            stats=_stats(usage, timings, started, first_at),
            stop_reason=finish_reason,
            tool_calls=[_call(index, call) for index, call in sorted(calls.items()) if call["name"]],
            raw=self.kept("".join(thought), details),
        )

    # --- hooks for the cloud providers (cloud_openai.py) ---

    def request_body(
        self, model_ref: str, messages: list[dict[str, Any]], effort: str, tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model_ref,
            # only the visible text travels; other providers' raw messages are not for this API
            "messages": self.wire(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "reasoning_effort": effort,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}}
                for t in tools
            ]
        return body

    def wire(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return wire_messages(messages)

    def kept(self, reasoning: str, details: list[dict[str, Any]]) -> dict[str, Any] | None:
        """What of the answer is stored to be sent back later (None: nothing, as for local models)."""
        return None

    def describe(self, status: int, detail: str) -> str:
        return f"HTTP {status}: {detail}"

    def priced(self, model_ref: str, counted: dict[str, int], reported: dict[str, Any]) -> dict[str, int]:
        """The usage, with the request's own price (`cost_microusd`) when the provider knows it better than the
        catalog's per-token prices."""
        price = self.price(model_ref, counted, reported)
        return {**counted, "cost_microusd": price} if price is not None else counted

    def price(self, model_ref: str, counted: dict[str, int], reported: dict[str, Any]) -> int | None:
        return None


def wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation for the chat API. A system message inside the conversation (the hub's context) joins the
    user turn before it: many local chat templates take a system message only at the very start. The joining is
    deterministic, so the same history gives the same prompt and the server's prompt cache keeps working."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "system" and out:
            text = f"<context>\n{message['content']}\n</context>"
            if out[-1]["role"] == "user" and isinstance(out[-1]["content"], str):
                out[-1] = {**out[-1], "content": out[-1]["content"] + "\n\n" + text}
            else:
                out.append({"role": "user", "content": text})
            continue
        out.append(to_wire(message))
    return out


def to_wire(message: dict[str, Any]) -> dict[str, Any]:
    """One hub prompt message as the OpenAI chat API takes it."""
    if message["role"] == "tool":
        return {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
    wire: dict[str, Any] = {"role": message["role"], "content": message["content"]}
    if message.get("tool_calls"):
        wire["content"] = message["content"] or None
        wire["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(call["input"], ensure_ascii=False)},
            }
            for call in message["tool_calls"]
        ]
    return wire


def _call(index: int, call: dict[str, str]) -> dict[str, Any]:
    try:
        args = json.loads(call["args"] or "{}")
    except ValueError:
        args = {"__invalid_json__": call["args"]}
    return {"id": call["id"] or f"call_{index}", "name": call["name"], "input": args}


def _usage(usage: dict, chunks: int) -> dict[str, int]:
    details = usage.get("prompt_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens", 0),
        # OpenAI and OpenRouter say cached_tokens; DeepSeek, prompt_cache_hit_tokens
        "cached_input_tokens": details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0,
        "output_tokens": usage.get("completion_tokens") or chunks,
    }


def _stats(usage: dict, timings: dict, started: float, first_at: float | None) -> dict[str, float]:
    end = time.monotonic()
    tokens = usage.get("completion_tokens") or 0
    tok_s = timings.get("predicted_per_second")
    if not tok_s and first_at and end > first_at:
        tok_s = tokens / (end - first_at)
    return {
        "tok_s": round(tok_s or 0, 1),
        "ttft_s": round(first_at - started, 2) if first_at else 0.0,
        "total_s": round(end - started, 2),
    }
