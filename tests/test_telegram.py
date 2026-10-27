"""Approvals from the phone (stage 5.5b, D21, Q14): linking a chat by a one-time code, what goes to the phone for
which project, answering from it, and the message following the answer - against a stand-in Bot API."""

import asyncio
import json

import httpx
import pytest

from hub import telegram
from hub.approvals import ApprovalQueue
from hub.db import Database, migrate
from hub.events import EventBus
from hub.secrets import MemorySecretStore

CHAT = 4242


class BotAPI:
    """Records the calls; getUpdates hands out what the test queued."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.next_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path.startswith("/botTOKEN/")
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method == "getUpdates":
            result, self.updates = self.updates, []
        elif method == "sendMessage":
            self.next_id += 1
            result = {"message_id": self.next_id}
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self, method="sendMessage"):
        return [body for name, body in self.calls if name == method]


@pytest.fixture
def bot(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    db.call(lambda c: c.execute("INSERT INTO projects (id, name, path, cloud_ok) VALUES ('p1', 'shared', 'x1', 1),"
                                " ('p2', 'secret-work', 'x2', 0)"))  # fmt: skip
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind) VALUES ('c1', 'chat')"))
    bus = EventBus(db)
    store = MemorySecretStore()
    store.set(telegram.TOKEN, "TOKEN")
    api = BotAPI()
    http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    queue = ApprovalQueue(db, bus)
    yield telegram.TelegramBot(db, bus, queue, store, http=http, api="https://bot.test"), api, db, queue
    db.close()


def message(chat_id, text, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def test_a_chat_is_linked_by_a_one_time_code(bot):
    tg, api, db, _queue = bot
    code = db.call(telegram.new_link_code)

    async def scenario():
        await tg._handle(message(999, "/link 000000" if code != "000000" else "/link 111111"))  # a wrong code
        await tg._handle(message(999, "привет"))  # a stranger: no answer at all
        assert api.sent() == [] and db.call(telegram.linked_chat) is None
        await tg._handle(message(CHAT, f"/link {code}"))
        await tg._handle(message(777, f"/link {code}"))  # the code worked once

    asyncio.run(scenario())
    assert db.call(telegram.linked_chat) == CHAT
    assert [m["chat_id"] for m in api.sent()] == [CHAT]
    db.call(telegram.unlink)
    assert db.call(telegram.linked_chat) is None


def test_what_goes_to_the_phone(bot):
    tg, api, db, queue = bot
    db.call(lambda c: telegram._set(c, telegram.CHAT_KEY, CHAT))

    async def scenario():
        sub = tg._bus.subscribe(("approval.*",))
        asks = [asyncio.create_task(queue.ask("run_command", {"command": "pytest -q"}, conversation_id="c1",
                                              project_id=project)) for project in ("p1", "p2")]  # fmt: skip
        asks.append(asyncio.create_task(queue.ask("run_command", {"command": "git push"}, project_id="p1")))
        for _ in asks:
            event = await sub.queue.get()
            await tg._send(event.type, event.payload)
        for pending in await queue.pending():
            await queue.decide(pending["id"], allow=False, by="app")
        await asyncio.gather(*asks)

    asyncio.run(scenario())
    sent = api.sent()[:3]  # the three asks ran side by side: any order
    shared = next(m for m in sent if "«shared»" in m["text"] and "git push" not in m["text"])
    local = next(m for m in sent if "«secret-work»" in m["text"])
    push = next(m for m in sent if "Разрушительное" in m["text"])
    assert "в «shared»" in shared["text"] and "<pre>pytest -q</pre>" in shared["text"]
    assert shared["reply_markup"]["inline_keyboard"][0][0]["text"] == "Разрешить"
    assert "в «secret-work»" in local["text"] and "pytest" not in local["text"]  # D21: no code of a local project
    assert "Подробности — в приложении" in local["text"]
    assert "только в приложении" in push["text"] and "reply_markup" not in push  # destructive: not from the phone


def test_answering_from_the_phone(bot):
    tg, api, db, queue = bot
    db.call(lambda c: telegram._set(c, telegram.CHAT_KEY, CHAT))

    async def scenario():
        sub = tg._bus.subscribe(("approval.*",))
        asking = asyncio.create_task(queue.ask("edit_file", {"path": "a.py", "old_text": "x", "new_text": "y"},
                                               conversation_id="c1", project_id="p1"))  # fmt: skip
        requested = await sub.queue.get()
        await tg._send(requested.type, requested.payload)
        approval_id = requested.payload["approval"]["id"]
        press = {"id": "q1", "data": f"ok:{approval_id}", "message": {"chat": {"id": CHAT}}}
        await tg._handle({"update_id": 2, "callback_query": {**press, "message": {"chat": {"id": 13}}}})  # stranger
        assert not asking.done()
        await tg._handle({"update_id": 3, "callback_query": press})
        decision = await asking
        decided = await sub.queue.get()
        await tg._send(decided.type, decided.payload)
        await tg._handle({"update_id": 4, "callback_query": {**press, "id": "q2"}})  # pressed again
        return decision

    decision = asyncio.run(scenario())
    assert decision.allow is True
    row = db.call(lambda c: c.execute("SELECT status, decided_by FROM approvals").fetchone())
    assert tuple(row) == ("allowed", "telegram")
    answers = api.sent("answerCallbackQuery")
    assert [a["text"] for a in answers] == ["Готово", "На этот запрос уже ответили"]
    assert api.sent("editMessageReplyMarkup")[0]["reply_markup"] == {"inline_keyboard": []}
    assert api.sent()[-1]["text"] == "✅ Разрешено (с телефона)"


def test_polling_and_a_finished_run(bot):
    tg, api, db, _queue = bot
    code = db.call(telegram.new_link_code)
    api.updates = [message(CHAT, f"/link {code}", update_id=7)]

    async def scenario():
        tg.start()
        for _ in range(100):
            if db.call(telegram.linked_chat) == CHAT:
                break
            await asyncio.sleep(0.02)
        await tg._send("run.finished", {"run": {"profile_name": "Ревьюер", "status": "done"}})
        await tg.stop()

    asyncio.run(scenario())
    assert db.call(telegram.linked_chat) == CHAT
    polls = [body for name, body in api.calls if name == "getUpdates"]
    assert polls[0]["offset"] == 0 and any(p["offset"] == 8 for p in polls[1:])  # the update is not read twice
    assert api.sent()[-1]["text"] == "Агент «Ревьюер»: готово"


def test_without_a_token_nothing_is_sent(bot):
    tg, api, db, _queue = bot
    db.call(lambda c: telegram._set(c, telegram.CHAT_KEY, CHAT))
    tg._store.delete(telegram.TOKEN)
    asyncio.run(tg._send("run.finished", {"run": {"profile_name": "x", "status": "done"}}))
    assert api.calls == []
