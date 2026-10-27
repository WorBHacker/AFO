"""Approvals from the phone (stage 5, D21, Q14): the user's own Telegram bot.

The hub talks to the Bot API only when the user has put their bot's token into the Windows Credential Manager
(`python -m hub key set telegram_bot_token`), and only with the one chat they linked: `python -m hub telegram link`
prints a one-time code, valid 10 minutes, and "/link <code>" sent to the bot from the user's chat binds it. The bot
answers nobody else.

What goes to the phone (Telegram is a cloud service, D21): for a project that allows the cloud, the request with its
command or diff; for any other, a short notice without code ("an agent waits for an answer in «proj»: file edit").
"Разрешить" / "Отклонить" answer from the phone; a destructive command is not answered from the phone at all - only
in the app. The message is updated when the request is answered anywhere (or expires). Finished agent runs are
reported too. Long polling (getUpdates); network trouble is waited out.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import logging
import secrets
import sqlite3
import time
from typing import Any

import httpx

from .approvals import ApprovalError, ApprovalQueue
from .db import Database, utc_now
from .events import EventBus
from .secrets import SecretStore

log = logging.getLogger("hub.telegram")

TOKEN = "telegram_bot_token"
API = "https://api.telegram.org"
CHAT_KEY = "telegram.chat_id"
LINK_KEY = "telegram.link"
LINK_TTL_S = 600
POLL_S = 25  # getUpdates long poll
IDLE_S = 30.0  # without a token: look again this often
BACKOFF_S = (2, 5, 15, 60)
PREVIEW_LINES = 30
DECIDED = {"allowed": "✅ Разрешено", "denied": "⛔ Отклонено", "expired": "⌛ Не дождался ответа"}


# --- linking a chat ---


def _setting(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), utc_now()),
    )


def new_link_code(conn: sqlite3.Connection) -> str:
    """A one-time code for "/link <code>"; only its hash is kept."""
    code = f"{secrets.randbelow(10**6):06d}"
    _set(conn, LINK_KEY, {"hash": hashlib.sha256(code.encode()).hexdigest(), "until": time.time() + LINK_TTL_S})
    return code


def linked_chat(conn: sqlite3.Connection) -> int | None:
    return _setting(conn, CHAT_KEY)


def try_link(conn: sqlite3.Connection, chat_id: int, code: str) -> bool:
    pending = _setting(conn, LINK_KEY)
    if not pending or time.time() > pending["until"]:
        return False
    if hashlib.sha256(code.strip().encode()).hexdigest() != pending["hash"]:
        return False
    _set(conn, CHAT_KEY, chat_id)
    conn.execute("DELETE FROM settings WHERE key = ?", (LINK_KEY,))
    return True


def unlink(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (CHAT_KEY, LINK_KEY))


# --- what goes to the phone ---


def request_text(approval: dict[str, Any], project: dict[str, Any] | None) -> str:
    """The message for a request (HTML): with the command or diff for a cloud-allowed project, a notice otherwise."""
    who = "Claude Code" if approval.get("client") == "hub-claude-code" else "Агент"
    what = {"command": "команда", "write": "правка файла", "network": "запрос в сеть"}.get(approval["kind"], "действие")
    where = f" в «{html.escape(project['name'])}»" if project else ""
    head = f"<b>{who} ждёт ответа</b>{where}: {what} ({html.escape(approval['tool'])})"
    if approval.get("destructive"):
        head += "\n⚠️ Разрушительное действие — ответить можно только в приложении."
    if not project or not project.get("cloud_ok"):  # D21: a local-only project's code does not go to the cloud
        return head + "\nПодробности — в приложении LocalChat."
    preview = approval.get("preview") or {}
    lines = str(preview.get("text") or "").splitlines()
    shown = "\n".join(lines[:PREVIEW_LINES]) + (f"\n… ещё {len(lines) - PREVIEW_LINES} строк" if len(lines) >
                                                PREVIEW_LINES else "")  # fmt: skip
    return f"{head}\n<pre>{html.escape(shown)}</pre>" if shown else head


def buttons(approval: dict[str, Any]) -> dict[str, Any] | None:
    if approval.get("destructive"):
        return None
    return {"inline_keyboard": [[{"text": "Разрешить", "callback_data": f"ok:{approval['id']}"},
                                 {"text": "Отклонить", "callback_data": f"no:{approval['id']}"}]]}  # fmt: skip


def _project(conn: sqlite3.Connection, project_id: str | None) -> dict[str, Any] | None:
    found = project_id and conn.execute("SELECT name, cloud_ok FROM projects WHERE id = ?", (project_id,)).fetchone()
    return {"name": found["name"], "cloud_ok": bool(found["cloud_ok"])} if found else None


class TelegramBot:
    def __init__(self, db: Database, bus: EventBus, approvals: ApprovalQueue, store: SecretStore,
                 http: httpx.AsyncClient | None = None, api: str = API, quiet=None):  # fmt: skip
        self._db = db
        self._bus = bus
        self._approvals = approvals
        self._store = store
        self._http = http
        self._api = api
        self._tasks: list[asyncio.Task] = []
        self._messages: dict[str, int] = {}  # approval id -> the message that asks about it
        self._offset = 0
        self._quiet = quiet  # the quiet hours now: their end, or None (9.7, Q23)
        self._held: list[tuple[str, dict[str, Any]]] = []  # what waits for the morning

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._follow())]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # --- the Bot API ---

    async def _call(self, method: str, **params: Any) -> Any:
        token = self._store.get(TOKEN)
        if not token:
            raise LookupError("no bot token")
        http = self._http or httpx.AsyncClient(timeout=POLL_S + 10)
        try:
            response = await http.post(f"{self._api}/bot{token}/{method}", json=params, timeout=POLL_S + 10)
        finally:
            if self._http is None:
                await http.aclose()
        body = response.json()
        if not body.get("ok"):
            raise httpx.HTTPError(f"{method}: {body.get('description', response.status_code)}")
        return body["result"]

    async def me(self) -> dict[str, Any]:
        """The bot itself (getMe): a check of the token. LookupError without one."""
        return await self._call("getMe")

    # --- incoming: /link, button presses ---

    async def _poll(self) -> None:
        trouble = 0
        while True:
            await asyncio.sleep(0)  # a poll that answers at once must not hold the loop
            if not self._store.get(TOKEN):
                await asyncio.sleep(IDLE_S)
                continue
            try:
                updates = await self._call("getUpdates", offset=self._offset, timeout=POLL_S,
                                           allowed_updates=["message", "callback_query"])  # fmt: skip
                trouble = 0
            except (httpx.HTTPError, ValueError, LookupError) as exc:
                wait = BACKOFF_S[min(trouble, len(BACKOFF_S) - 1)]
                trouble += 1
                log.warning("telegram: %s; again in %s s", exc, wait)
                await asyncio.sleep(wait)
                continue
            for update in updates:
                self._offset = max(self._offset, update["update_id"] + 1)
                try:
                    await self._handle(update)
                except Exception:
                    log.exception("telegram update %s failed", update.get("update_id"))

    async def _handle(self, update: dict[str, Any]) -> None:
        chat = await self._db.acall(linked_chat)
        if "message" in update:
            message = update["message"]
            chat_id, text = message["chat"]["id"], str(message.get("text") or "")
            code = text.removeprefix("/link ") if text.startswith("/link ") else None
            if code is not None and await self._db.acall(lambda conn: try_link(conn, chat_id, code)):
                await self._call("sendMessage", chat_id=chat_id,
                                 text="Готово: этот чат получает запросы подтверждения LocalChat.")  # fmt: skip
                await self._bus.publish("telegram.linked", {"chat_id": chat_id})
            if chat is not None and chat_id == chat and text and not text.startswith("/"):  # the user's own word
                await self._bus.publish("telegram.message", {"text": text[:2000]})  # schedules wait for it (9.7)
            return  # nothing else is answered: the bot talks to its user only
        query = update.get("callback_query")
        if not query:
            return
        from_chat = (query.get("message") or {}).get("chat", {}).get("id")
        if chat is None or from_chat != chat:
            return
        verb, _, approval_id = str(query.get("data") or "").partition(":")
        note = "Готово"
        try:
            if verb in ("ok", "no"):
                await self._approvals.decide(approval_id, allow=verb == "ok", by="telegram")
        except ApprovalError:
            note = "На этот запрос уже ответили"
        await self._call("answerCallbackQuery", callback_query_id=query["id"], text=note)

    # --- outgoing: requests, answers, finished runs ---

    async def _follow(self) -> None:
        subscription = self._bus.subscribe(("approval.requested", "approval.decided", "run.finished",
                                            "run.question", "run.notice"))  # fmt: skip
        while True:
            try:
                event = await asyncio.wait_for(subscription.queue.get(), 60)
            except TimeoutError:
                event = None
            try:
                if await self._hold(event):
                    continue
                if event is not None:
                    await self._send(event.type, event.payload)
            except (httpx.HTTPError, ValueError, LookupError) as exc:
                log.warning("telegram: %s not sent: %s", getattr(event, "type", "the night's"), exc)
            except Exception:
                log.exception("telegram: %s failed", getattr(event, "type", "the night's"))

    async def _hold(self, event: Any) -> bool:
        """In the quiet hours requests and the agent's words wait for the morning (Q23); then they go, in order -
        a request answered in the window meanwhile is not sent at all. True when this event waits."""
        quiet = await self._quiet() if self._quiet is not None else None
        if quiet is not None:
            if event is None:
                return True
            if event.type == "approval.decided":
                gone = (event.payload.get("approval") or {}).get("id")
                kept = [h for h in self._held if (h[1].get("approval") or {}).get("id") != gone]
                answered_overnight = len(kept) != len(self._held)
                self._held = kept
                return answered_overnight  # its request never went out: nothing to edit
            self._held.append((event.type, event.payload))
            return True
        held, self._held = self._held, []
        for kind, payload in held:
            await self._send(kind, payload)
        return False

    async def _send(self, kind: str, payload: dict[str, Any]) -> None:
        chat = await self._db.acall(linked_chat)
        if chat is None or not self._store.get(TOKEN):
            return
        if kind in ("run.question", "run.notice"):  # the autonomous agent asks, or says (9.4): answered in the window
            head = "Агент спрашивает (ответ — в окне LocalChat)" if kind == "run.question" else "Агент сообщает"
            body = html.escape(str(payload.get("question") or payload.get("text") or ""))
            await self._call("sendMessage", chat_id=chat, text=f"<b>{head}</b>\n{body}", parse_mode="HTML")
            return
        if kind == "run.finished":
            run = payload.get("run") or {}
            words = {"done": "готово", "error": "ошибка", "cancelled": "отменён"}
            text = (
                f"Агент «{html.escape(run.get('profile_name', ''))}»: {words.get(run.get('status'), run.get('status'))}"
            )
            await self._call("sendMessage", chat_id=chat, text=text, parse_mode="HTML")
            return
        approval = payload.get("approval") or {}
        if kind == "approval.requested" and approval.get("status") == "pending":
            project = await self._db.acall(lambda conn: _project(conn, approval.get("project_id")))
            extra = {"reply_markup": buttons(approval)} if buttons(approval) else {}
            text = request_text(approval, project)
            sent = await self._call("sendMessage", chat_id=chat, text=text, parse_mode="HTML", **extra)
            self._messages[approval["id"]] = sent["message_id"]
        elif kind == "approval.decided" and approval.get("id") in self._messages:
            message_id = self._messages.pop(approval["id"])
            by = " (с телефона)" if approval.get("decided_by") == "telegram" else " (в приложении)"
            said = DECIDED.get(approval.get("status", ""), approval.get("status", "")) + by
            if approval.get("comment") and approval.get("status") == "denied":
                said += f": {html.escape(approval['comment'])}"
            await self._call("editMessageReplyMarkup", chat_id=chat, message_id=message_id,
                             reply_markup={"inline_keyboard": []})  # fmt: skip
            await self._call("sendMessage", chat_id=chat, text=said, parse_mode="HTML",
                             reply_parameters={"message_id": message_id})  # fmt: skip
