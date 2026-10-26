"""Runs one completion against the gateway and streams it to the event bus.

A run is started by posting a user message. It works in the background and reports over the event bus:
    chat.message  (persisted)  a finished message (the user's, then the assistant's)
    chat.loading  (ephemeral)  provider still getting ready (model swap)
    chat.delta    (ephemeral)  a reasoning or content delta
    chat.done     (persisted)  the run finished: usage, stats, cancelled?
    chat.error    (persisted)  the run failed: detail
Deltas are not stored in the events table (too many); the finished answer lives in the messages table (D14).
The app follows a run by subscribing to 'chat.*' and the conversation_id in each payload.
"""

from __future__ import annotations

import asyncio
import logging

from . import store
from .db import Database
from .events import EventBus
from .gateway import Completion, Gateway, GatewayError, blocks_to_prompt_text
from .gateway.base import cost_usd

log = logging.getLogger("hub.chat")

DEFAULT_EFFORT = "medium"


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
        user_blocks = [{"type": "text", "text": text}]
        user_msg = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "user", user_blocks))
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
        await self._save(conversation_id, model, completion, client_id)
        self._runs.pop(conversation_id, None)

    async def _consume(self, conversation_id, provider, model_ref, prompt, effort, cancel) -> Completion:
        completion = Completion()
        reasoning: list[str] = []
        content: list[str] = []
        async for event in provider.stream_chat(model_ref, prompt, effort, cancel):
            if event.kind == "loading":
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
                content.append(event.text)
                await self._bus.publish(
                    "chat.delta",
                    {"conversation_id": conversation_id, "kind": "content", "text": event.text},
                    persist=False,
                )
            elif event.kind == "end":
                completion.usage = event.usage or {}
                completion.stats = event.stats or {}
        completion.reasoning = "".join(reasoning)
        completion.text = "".join(content)
        completion.cancelled = cancel.is_set()
        return completion

    async def _save(self, conversation_id: str, model: dict, completion: Completion, client_id: str | None):
        cost = cost_usd(completion.usage, model["prices"])

        def write(conn):
            message = store.add_message(conn, conversation_id, "assistant", completion.blocks(), model_id=model["id"])
            store.record_usage(
                conn,
                client_id=client_id,
                conversation_id=conversation_id,
                message_id=message.id,
                model_id=model["id"],
                usage=completion.usage,
                cost_usd=cost,
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
                "cancelled": completion.cancelled,
            },
        )


def _to_prompt(history: list[store.MessageRow]) -> list[dict[str, str]]:
    """The conversation as OpenAI-shape {role, content}: visible text only, reasoning left out (D14)."""
    prompt = []
    for message in history:
        if message.role not in ("system", "user", "assistant"):
            continue
        text = blocks_to_prompt_text(message.content)
        if text or message.role == "user":
            prompt.append({"role": message.role, "content": text})
    return prompt
