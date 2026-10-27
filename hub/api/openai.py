"""/v1/openai - the hub as an OpenAI-compatible server (stage 6, D33): base URL http://127.0.0.1:8765/v1/openai, the
client's token as the API key. See hub/openai_proxy.py."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import budgets, model_settings, store
from ..auth import Client
from ..claude_code import PROVIDER as CLAUDE_CODE
from ..gateway import GatewayError
from ..gateway.base import cost_usd
from ..openai_proxy import (
    EFFORTS,
    Chunks,
    ProxyError,
    client_params,
    finish_reason,
    model_list,
    to_hub_messages,
    to_hub_tools,
)
from ..openai_proxy import tool_calls_of as openai_calls
from ..openai_proxy import usage_of as openai_usage
from .deps import require_client

log = logging.getLogger("hub.openai")
router = APIRouter(prefix="/v1/openai")


@router.get("/models")
async def models(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return model_list(await request.app.state.db.acall(store.list_models))


@router.post("/chat/completions", response_model=None)
async def completions(
    request: Request, client: Annotated[Client, Depends(require_client)]
) -> JSONResponse | StreamingResponse:
    try:
        body = await request.json()
        return await _complete(request, client, body if isinstance(body, dict) else {})
    except ProxyError as exc:
        return JSONResponse(exc.body(), status_code=exc.status)
    except ValueError:
        return JSONResponse(ProxyError(400, "invalid_request_error", "the body is not JSON").body(), status_code=400)


async def _complete(request: Request, client: Client, body: dict[str, Any]) -> JSONResponse | StreamingResponse:
    state = request.app.state
    model_id = str(body.get("model") or "")
    model = await state.db.acall(lambda conn: store.get_model(conn, model_id))
    if model is None or not model["enabled"] or model["provider"] == CLAUDE_CODE:
        raise ProxyError(404, "model_not_found", f"no model {model_id!r} here; GET /v1/openai/models lists them")
    if model["prices"] and await state.db.acall(lambda conn: budgets.reached(conn, model_id)):
        raise ProxyError(402, "insufficient_quota", "a budget for this model is used up (LocalChat budgets, D12)")
    messages = to_hub_messages(body.get("messages"))
    tools = to_hub_tools(body.get("tools"))
    number, own = await state.db.acall(lambda conn: model_settings.current(conn, model_id))
    effort = EFFORTS.get(str(body.get("reasoning_effort") or ""), own.get("effort") or "medium")
    try:
        provider, ref = state.gateway.resolve(model_id)
    except GatewayError as exc:
        raise ProxyError(404, "model_not_found", str(exc)) from exc
    # the model's settings, and over them what the client asked for; only what the provider takes goes out
    params = model_settings.clean_params({**(own.get("params") or {}), **client_params(body)},
                                         state.gateway.accepts(model_id))  # fmt: skip
    cancel = asyncio.Event()
    chunks = Chunks(model_id)
    extra: dict[str, Any] = {"tools": tools} if tools else {}
    if params:
        extra["params"] = params
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

    async def record(event: Any) -> None:
        usage = event.usage or {}
        cost = cost_usd(usage, model["prices"])

        def write(conn):
            store.record_usage(conn, client_id=client.id, conversation_id=None, message_id=None, model_id=model_id,
                               usage=usage, cost_usd=cost, estimated=bool(usage.get("estimated")),
                               meta={"proxy": True, "stats": event.stats or {}, "stop_reason": event.stop_reason,
                                     "cancelled": cancel.is_set(), **({"params": params} if params else {}),
                                     **({"settings_version": number} if number else {})})  # fmt: skip
            return budgets.crossed(conn, model_id, cost)

        for kind, budget in await state.db.acall(write):
            await state.bus.publish(f"budget.{kind}", {"model_id": model_id, "budget": budget})

    if not body.get("stream"):
        text, reasoning, end = [], [], None
        try:
            async for event in provider.stream_chat(ref, messages, effort, cancel, **extra):
                if event.kind == "content":
                    text.append(event.text)
                elif event.kind == "reasoning":
                    reasoning.append(event.text)
                elif event.kind == "end":
                    end = event
        except GatewayError as exc:
            raise ProxyError(502, "upstream_error", str(exc)) from exc
        if end is None:
            raise ProxyError(502, "upstream_error", "the model ended without an answer")
        await record(end)
        if end.refusal:
            raise ProxyError(400, "refusal", end.refusal)
        calls = list(end.tool_calls or [])
        finish = finish_reason(end.stop_reason, calls, False)
        return JSONResponse(chunks.whole("".join(text), "".join(reasoning), calls, finish,
                                         openai_usage(end.usage or {})))  # fmt: skip

    async def stream() -> AsyncIterator[bytes]:
        events = provider.stream_chat(ref, messages, effort, cancel, **extra)
        end = None
        try:
            yield chunks.line({"role": "assistant", "content": ""})
            async for event in events:
                if event.kind == "content":
                    yield chunks.line({"content": event.text})
                elif event.kind == "reasoning":
                    yield chunks.line({"reasoning_content": event.text})
                elif event.kind == "end":
                    end = event
            calls = list(end.tool_calls or []) if end else []
            if calls:
                yield chunks.line({"tool_calls": openai_calls(calls)})
            if end is not None and end.refusal:
                yield chunks.line({"content": end.refusal}, "content_filter")
            else:
                yield chunks.line({}, finish_reason(end.stop_reason if end else None, calls, False))
            if include_usage and end is not None:
                yield chunks.usage_line(openai_usage(end.usage or {}))
            yield b"data: [DONE]\n\n"
        except GatewayError as exc:
            yield chunks.error_line(str(exc))
        finally:
            if end is None:
                cancel.set()  # the client went away, or the model failed: stop it
                with contextlib.suppress(Exception):
                    await events.aclose()
            else:
                await record(end)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
