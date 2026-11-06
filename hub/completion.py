"""Code completion for the window's editor, like Copilot (D47, step 14.6): grey text after the cursor.

The model is the role «Дописывание кода» (`complete`). One of llama.cpp's (llama-swap) gets the code around the
cursor in its `/infill` - fill in the middle, through llama-swap's `/upstream/<model>/infill`, `cache_prompt` so the
file's beginning is reused; another model, or one llama.cpp says has no fill-in-the-middle tokens, is asked in words
to go on from the cursor with code only. Short: N_PREDICT tokens, DEADLINE_S at most, and no further than the block
the cursor is in, with the indent the person typed as its only one (Shape, stage 28).

The graphics card is one (review of stage 13): the completion model never pushes the chat's model out of it. A local
one that is not loaded - llama-swap's /running - pauses completion; it starts only when the person asks for it
(`load`). One request at a time per window: the next one cancels the one before, and the cancelling reaches
llama.cpp - its connection closed, its slot free for the next.

Where the code goes (D10, D12): a cloud model gets code only of a project that allows the cloud, through the secret
filter; a secret file is not completed at all. Each request is in the usage table - its time and its tokens in the
models' statistics.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from common.i18n import N_, tr

from . import model_settings, store
from .db import Database
from .gateway.base import ProviderError, cost_usd
from .gateway.registry import Gateway
from .projects import Project
from .secret_filter import clean, is_secret_file
from .system_status import llama_swap_sample

log = logging.getLogger("hub.completion")

ROLE = "complete"
LLAMA_SWAP = "llama-swap"
BEFORE, AFTER = 3000, 1000  # characters of code around the cursor that go to the model
N_PREDICT = 96
DEADLINE_S = 1.5
PROMPT = (
    "You complete code at the cursor. Reply with only the code that goes at <CURSOR> - no explanation, "
    "no Markdown fence, nothing repeated from before or after it. Reply with nothing when nothing fits."
)
PAUSES = {
    "no_model": N_("Дописывание: модель не выбрана — роль «Дописывание кода» в настройках моделей"),
    "not_loaded": N_("Дописывание на паузе: модель не загружена рядом с моделью чата"),
    "unreachable": N_("Дописывание на паузе: llama-swap не отвечает"),
    "local_only": N_("Дописывание: облачная модель, а проект — только локально (D10)"),
    "secret": N_("Дописывание: в файлах с секретами не работает"),
    "off": N_("Дописывание: модель недоступна"),
}


class Paused(Exception):
    def __init__(self, reason: str):
        super().__init__(tr(PAUSES[reason]))
        self.reason = reason


def chosen(conn, role: str = ROLE) -> str | None:
    roles = model_settings.current(conn, model_settings.ALL)[1].get("roles") or {}
    return roles.get(role) or None


class Completions:
    def __init__(self, db: Database, gateway: Gateway, llama_swap_url: str, transport=None):
        self._db = db
        self._gateway = gateway
        self._url = llama_swap_url.rstrip("/")
        self._transport = transport
        self._current: asyncio.Task | None = None
        self._no_fim: set[str] = set()  # models llama.cpp said have no fill-in-the-middle tokens

    async def start(self, project: Project, path: str, before: str, after: str, *, load: bool = False
                    ) -> tuple[dict[str, Any], AsyncIterator[str]]:  # fmt: skip
        """What will answer ({model, how}) and its pieces of text; Paused when it will not. Starting cancels the one
        before (the window's earlier keystroke) and waits till its connection to llama.cpp is closed: llama.cpp stops
        at its next token and its slot is free when this one comes."""
        if self._current is not None and not self._current.done():
            self._current.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._current
        if is_secret_file(path):
            raise Paused("secret")
        model_id = await self._db.acall(chosen)
        if not model_id:
            raise Paused("no_model")
        model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
        if model is None or not model["enabled"]:
            raise Paused("off")
        if model["locality"] == "cloud":
            if not project.cloud_ok:
                raise Paused("local_only")
            before, after = clean(before), clean(after)
        provider, ref = self._gateway.resolve(model_id)
        llama = provider.name == LLAMA_SWAP
        if llama and not load:
            local = await llama_swap_sample(self._url, self._transport)
            if not local["reachable"]:
                raise Paused("unreachable")
            if not any(r["model"] == ref and r["state"] in ("ready", "") for r in local["running"]):
                raise Paused("not_loaded")
        before, after = before[-BEFORE:], after[:AFTER]
        how = "infill" if llama and model_id not in self._no_fim else "chat"
        return {"model": model_id, "how": how}, self._run(model, provider, ref, how, path, before, after)

    async def _run(self, model, provider, ref, how, path, before, after) -> AsyncIterator[str]:
        """The pieces, until N_PREDICT, DEADLINE_S, or the next request; the usage recorded either way."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        usage: dict[str, int] = {}
        began = time.monotonic()
        first: list[float] = []

        async def produce() -> None:
            try:
                pieces = (self._infill(ref, path, before, after, usage) if how == "infill"
                          else self._chat(provider, ref, path, before, after, usage))  # fmt: skip
                async for piece in pieces:
                    if not first:
                        first.append(time.monotonic() - began)
                    await queue.put(piece)
            except _NoFim:
                self._no_fim.add(model["id"])
                async for piece in self._chat(provider, ref, path, before, after, usage):
                    await queue.put(piece)
            except (ProviderError, httpx.HTTPError) as exc:  # no grey text this time; the next keystroke asks again
                log.info("completion by %s failed: %s", model["id"], exc)
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        self._current = task
        shape = Shape(before)
        cancelled = False
        try:
            while True:
                left = DEADLINE_S - (time.monotonic() - began)
                if left <= 0:
                    break
                try:
                    piece = await asyncio.wait_for(queue.get(), timeout=left)
                except TimeoutError:
                    break
                if piece is None:
                    break
                if shown := shape.feed(piece):
                    yield shown
                if shape.done:  # the block ended: llama.cpp is let go below
                    break
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not task.done():
                task.cancel()
                cancelled = True
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            cancelled = cancelled or (task.cancelled() if task.done() else False)
            stats = {"total_s": round(time.monotonic() - began, 3), **({"ttft_s": round(first[0], 3)} if first else {})}
            meta = {"kind": "complete", "how": how, "stats": stats, "cancelled": cancelled and not shape.done,
                    **({"cut": True} if shape.done else {})}  # fmt: skip
            with contextlib.suppress(Exception):
                await self._db.acall(lambda conn: store.record_usage(
                    conn, client_id=None, conversation_id=None, message_id=None, model_id=model["id"], usage=usage,
                    cost_usd=cost_usd(usage, model["prices"]), meta=meta))  # fmt: skip

    async def _infill(self, ref, path, before, after, usage) -> AsyncIterator[str]:
        body = {"input_prefix": before, "input_suffix": after, "input_extra": [{"filename": path, "text": ""}],
                "n_predict": N_PREDICT, "cache_prompt": True, "stream": True, "temperature": 0.2}  # fmt: skip
        timeout = httpx.Timeout(DEADLINE_S + 5, connect=2.0)
        async with (httpx.AsyncClient(timeout=timeout, transport=self._transport) as client,
                    client.stream("POST", f"{self._url}/upstream/{ref}/infill", json=body) as response):  # fmt: skip
            if response.status_code in (400, 404, 501):  # llama.cpp: this model has no fill-in-the-middle tokens
                raise _NoFim
            if response.status_code >= 400:
                raise ProviderError(f"llama.cpp /infill: HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                with contextlib.suppress(ValueError):
                    data = json.loads(line[5:])
                    if data.get("content"):
                        yield data["content"]
                    if data.get("stop"):
                        usage.update(input_tokens=int(data.get("tokens_evaluated") or 0),
                                     output_tokens=int(data.get("tokens_predicted") or 0))  # fmt: skip
                        return

    async def _chat(self, provider, ref, path, before, after, usage) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": PROMPT},
                    {"role": "user", "content": f"File: {path}\n\n{before}<CURSOR>{after}"}]  # fmt: skip
        accepts = self._gateway.accepts(f"{provider.name}/{ref}")
        params = {k: v for k, v in {"max_tokens": N_PREDICT, "temperature": 0.2}.items() if k in accepts}
        cancel = asyncio.Event()
        try:
            extra = {"params": params} if params else {}  # as the chat does: a provider without them is called so
            async for event in provider.stream_chat(ref, messages, "low", cancel, **extra):
                if event.kind == "content" and event.text:
                    yield event.text
                elif event.kind == "end" and event.usage:
                    usage.update(event.usage)
        finally:
            cancel.set()


class _NoFim(Exception):
    pass


TAB = 4  # a tab's width, for comparing indents


def _width(space: str) -> int:
    return len(space.replace("\r", "").expandtabs(TAB))


class Shape:
    """The grey text as Tab can take it (stage 28, live on qwen27b): a model's fill-in-the-middle indents again, now
    and then, the line the person already indented, and goes on past the block into the next function. The indent
    typed stays the line's only one; the text ends at the first line left of its block - the cursor's line's, or,
    from the end of a line, the first line under it; the blank lines and the line break at its end are left out.
    `feed` gives what may be shown of a piece; `done` - the block ended, the rest is not wanted."""

    def __init__(self, before: str) -> None:
        line = before[before.rfind("\n") + 1 :]
        self._bare = not line.strip()  # the cursor stands in the line's indent
        self._own = _width(line[: len(line) - len(line.lstrip())])  # the cursor line's indent
        self._block: int | None = _width(line) if self._bare else None
        self._line = 0  # the completion's line: 0 is the cursor's
        self._wrote = False  # something shown on the cursor's line
        self._start = True  # at a line's start: its indent and the blank lines before it held
        self._held = ""
        self.done = False

    def feed(self, piece: str) -> str:
        out: list[str] = []
        for char in piece:
            if self.done:
                break
            if not self._start:
                if char == "\n":  # held: shown only with the line after it
                    self._start, self._held, self._line = True, "\n", self._line + 1
                else:
                    out.append(char)
                continue
            if char in " \t\r\n":
                self._held += char
                self._line += char == "\n"
                continue
            if self._line == 0:
                out.append(("" if self._bare else self._held) + char)  # the typed indent is the only one
                self._wrote = True
            else:
                width = _width(self._held[self._held.rfind("\n") + 1 :])
                if self._block is None:
                    self._block = self._own if self._wrote else width
                if width < self._block:
                    self.done = True
                    break
                out.append(self._held + char)
            self._start, self._held = False, ""
        return "".join(out)
