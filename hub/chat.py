"""Runs one completion against the gateway and streams it to the event bus.

A run is started by posting a user message. It works in the background and reports over the event bus:
    chat.message  (persisted)  a finished message (the user's, then the assistant's)
    chat.loading  (ephemeral)  provider still getting ready (model swap)
    chat.delta    (ephemeral)  a reasoning or content delta
    chat.done     (persisted)  the run finished: usage, stats, cost, stop reason, cancelled?
    chat.error    (persisted)  the run failed: detail. A refusal by the provider is an error too, but its
                               tokens were spent, so its usage and cost are recorded and sent along.
Deltas are not stored in the events table (too many); the finished answer lives in the messages table (D14),
together with the provider's message as received, which the next request replays (D7).
The app follows a run by subscribing to 'chat.*' and the conversation_id in each payload.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import store
from .db import Database
from .events import EventBus
from .gateway import Completion, Gateway, GatewayError, blocks_to_prompt_text
from .gateway.base import cost_usd

log = logging.getLogger("hub.chat")

DEFAULT_EFFORT = "medium"
TITLE_LENGTH = 60


class ChatService:
    def __init__(self, db: Database, bus: EventBus, gateway: Gateway):
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self._runs: dict[str, asyncio.Event] = {}  # conversation_id -> cancel flag of its active run

    @property
    def busy(self) -> list[str]:
        return list(self._runs)

    async def send(
        self,
        conversation_id: str,
        text: str,
        model_id: str,
        *,
        effort: str = DEFAULT_EFFORT,
        client_id: str | None = None,
    ) -> dict:
        """Appends the user message and starts the assistant run. Returns the stored user message."""
        if conversation_id in self._runs:
            raise GatewayError("this conversation already has a run in progress")
        model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
        if model is None:
            raise GatewayError(f"unknown model {model_id!r}")
        if not model["enabled"]:
            raise GatewayError(f"model {model_id!r} is not available now (its provider no longer lists it)")
        user_blocks = [{"type": "text", "text": text}]

        def write(conn):
            title = None
            if not store.get_title(conn, conversation_id):  # an untitled chat is named after its first message
                title = _title_from(text)
                store.set_title(conn, conversation_id, title)
            return store.add_message(conn, conversation_id, "user", user_blocks), title

        user_msg, title = await self._db.acall(write)
        if title:
            await self._bus.publish("chat.titled", {"conversation_id": conversation_id, "title": title})
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": user_msg.to_json()})
        cancel = asyncio.Event()
        self._runs[conversation_id] = cancel
        task = asyncio.create_task(self._run(conversation_id, model, effort, cancel, client_id))
        task.add_done_callback(lambda t: t.exception())  # surface a crash to the loop's handler
        return user_msg.to_json()

    def cancel(self, conversation_id: str) -> bool:
        cancel = self._runs.get(conversation_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    async def _run(self, conversation_id: str, model: dict, effort: str, cancel: asyncio.Event, client_id: str | None):
        try:
            history = await self._db.acall(lambda conn: store.get_messages(conn, conversation_id))
            prompt = _to_prompt(history)
            provider, model_ref = self._gateway.resolve(model["id"])
            await self._bus.publish(
                "chat.run_started", {"conversation_id": conversation_id, "model_id": model["id"]}, persist=False
            )
            completion = await self._consume(conversation_id, provider, model_ref, prompt, effort, cancel)
        except GatewayError as exc:
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": str(exc)})
            self._runs.pop(conversation_id, None)
            return
        except Exception:
            log.exception("chat run for %s crashed", conversation_id)
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": "internal error"})
            self._runs.pop(conversation_id, None)
            return
        if completion.refusal:
            await self._save_refusal(conversation_id, model, completion, client_id)
        else:
            await self._save(conversation_id, model, completion, client_id)
        self._runs.pop(conversation_id, None)

    async def _consume(self, conversation_id, provider, model_ref, prompt, effort, cancel) -> Completion:
        completion = Completion()
        reasoning: list[str] = []
        content: list[str] = []
        # thinking time: from the request (or the end of a model swap) to the first visible word
        clock_from = time.monotonic()
        answer_at: float | None = None
        async for event in provider.stream_chat(model_ref, prompt, effort, cancel):
            if event.kind == "loading":
                clock_from = time.monotonic()
                await self._bus.publish(
                    "chat.loading", {"conversation_id": conversation_id, "text": event.text}, persist=False
                )
            elif event.kind == "reasoning":
                reasoning.append(event.text)
                await self._bus.publish(
                    "chat.delta",
                    {"conversation_id": conversation_id, "kind": "reasoning", "text": event.text},
                    persist=False,
                )
            elif event.kind == "content":
                answer_at = answer_at or time.monotonic()
                content.append(event.text)
                await self._bus.publish(
                    "chat.delta",
                    {"conversation_id": conversation_id, "kind": "content", "text": event.text},
                    persist=False,
                )
            elif event.kind == "end":
                completion.usage = event.usage or {}
                completion.stats = dict(event.stats or {})
                completion.stop_reason = event.stop_reason
                completion.raw = event.raw
                completion.refusal = event.refusal
        completion.reasoning = "".join(reasoning)
        completion.text = "".join(content)
        completion.cancelled = cancel.is_set()
        if reasoning:
            completion.stats["think_s"] = round((answer_at or time.monotonic()) - clock_from, 1)
        return completion

    async def _save(self, conversation_id: str, model: dict, completion: Completion, client_id: str | None):
        cost = cost_usd(completion.usage, model["prices"])

        def write(conn):
            message = store.add_message(
                conn,
                conversation_id,
                "assistant",
                completion.blocks(),
                model_id=model["id"],
                provider_raw=completion.raw,
            )
            store.record_usage(
                conn,
                client_id=client_id,
                conversation_id=conversation_id,
                message_id=message.id,
                model_id=model["id"],
                usage=completion.usage,
                cost_usd=cost,
                estimated=bool(completion.usage.get("estimated")),
                meta={
                    "stats": completion.stats,
                    "stop_reason": completion.stop_reason,
                    "cancelled": completion.cancelled,
                },
            )
            return message

        message = await self._db.acall(write)
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
        await self._bus.publish(
            "chat.done",
            {
                "conversation_id": conversation_id,
                "message_id": message.id,
                "usage": completion.usage,
                "stats": completion.stats,
                "cost_usd": cost,
                "stop_reason": completion.stop_reason,
                "cancelled": completion.cancelled,
            },
        )

    async def _save_refusal(self, conversation_id: str, model: dict, completion: Completion, client_id: str | None):
        """The provider declined: no assistant message joins the history, but the spent tokens are recorded."""
        cost = cost_usd(completion.usage, model["prices"])
        await self._db.acall(
            lambda conn: store.record_usage(
                conn,
                client_id=client_id,
                conversation_id=conversation_id,
                message_id=None,
                model_id=model["id"],
                usage=completion.usage,
                cost_usd=cost,
                estimated=bool(completion.usage.get("estimated")),
                meta={"stats": completion.stats, "stop_reason": completion.stop_reason},
            )
        )
        await self._bus.publish(
            "chat.error",
            {
                "conversation_id": conversation_id,
                "detail": completion.refusal,
                "refusal": True,
                "usage": completion.usage,
                "cost_usd": cost,
            },
        )


def _title_from(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LENGTH else line[: TITLE_LENGTH - 1].rstrip() + "…"


def _to_prompt(history: list[store.MessageRow]) -> list[dict[str, Any]]:
    """The conversation as OpenAI-shape {role, content}: visible text only, reasoning left out (D14).

    An assistant turn also carries the provider's message as received, so that provider can replay its own
    blocks unchanged (Claude's thinking signatures); every other provider sends just the text.
    """
    prompt: list[dict[str, Any]] = []
    for message in history:
        if message.role not in ("system", "user", "assistant"):
            continue
        text = blocks_to_prompt_text(message.content)
        if not text and message.role != "user":
            continue
        entry: dict[str, Any] = {"role": message.role, "content": text}
        if message.provider_raw is not None and message.model_id:
            entry["provider"] = message.model_id.partition("/")[0]
            entry["provider_raw"] = message.provider_raw
        prompt.append(entry)
    return prompt
