"""An OpenAI-compatible adapter. Serves llama-swap now (D5); OpenAI, OpenRouter and DeepSeek reuse it.

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
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.locality = locality
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._transport = transport  # tests inject a mock; production leaves it None

    async def list_models(self) -> list[Model]:
        async with httpx.AsyncClient(timeout=5.0, transport=self._transport) as client:
            try:
                response = await client.get(f"{self.base_url}/v1/models", headers=self._headers)
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
        body: dict[str, Any] = {
            "model": model_ref,
            # only the visible text travels; other providers' raw messages are not for this API
            "messages": [to_wire(m) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "reasoning_effort": effort,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}}
                for t in tools
            ]
        calls: dict[int, dict[str, str]] = {}  # tool calls being put together, by index
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
                    "POST", f"{self.base_url}/v1/chat/completions", json=body, headers=self._headers
                ) as response,
            ):
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise ProviderError(f"{self.name}: HTTP {response.status_code}: {detail}")
                async for line in response.aiter_lines():
                    if cancel.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
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
                        yield StreamEvent("reasoning", think)
                    if text:
                        yield StreamEvent("content", text)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc
        yield StreamEvent(
            "end",
            usage=_usage(usage, chunks),
            stats=_stats(usage, timings, started, first_at),
            stop_reason=finish_reason,
            tool_calls=[_call(index, call) for index, call in sorted(calls.items()) if call["name"]],
        )


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
        "cached_input_tokens": details.get("cached_tokens", 0),
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
