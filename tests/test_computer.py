"""Computer control (stage 9.6, D41) on the agent's own test window: it sees the window of an allowed program as its
UI Automation tree, types, ticks, clicks through the elements' own patterns (no mouse, no keys), and never touches a
password field, a payment or the terms; sending is asked every time; a program not allowed is not seen; each action
is in the journal with a snapshot; the vision model answers about a snapshot."""

import asyncio
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from hub import computer as pc
from hub import model_settings, programs, store
from hub.db import Database, migrate
from hub.events import EventBus
from hub.gateway.base import Model, StreamEvent
from tests.test_autonomous import PLAN, Scripted, ended, results, start, step, use
from tests.test_changes import wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture

WINDOW = Path(__file__).with_name("fake_app_window.py")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")


def test_keys_words_and_pictures(tmp_path):
    assert pc.parse_keys("Ctrl+Shift+S") == [0x11, 0x10, ord("S")]
    assert pc.parse_keys("enter") == [0x0D]
    for bad, said in (("win+r", "leaves the program"), ("alt+tab", "leaves the program"), ("ctrl+щ", "unknown key"),
                      ("", "a combination")):  # fmt: skip
        with pytest.raises(pc.ScreenError, match=said):
            pc.parse_keys(bad)
    for name in ("Пароль", "Номер карты", "Оплатить", "Купить сейчас", "Принимаю условия", "Перевести средства"):
        assert pc.FORBIDDEN.search(name), name
    for name in ("Сохранить", "Имя", "Запомнить", "Отправить"):
        assert not pc.FORBIDDEN.search(name), name
    assert pc.SENDS.search("Отправить") and pc.SENDS.search("Send") and not pc.SENDS.search("Сохранить")
    picture = pc.png(2, 1, bytes([0, 0, 255, 255, 255, 0, 0, 255]))  # BGRA: a red and a blue pixel
    assert picture.startswith(b"\x89PNG\r\n\x1a\n") and struct.unpack(">II", picture[16:24]) == (2, 1)
    data = picture[picture.index(b"IDAT") + 4 : picture.index(b"IEND") - 8]
    assert zlib.decompress(data) == b"\x00\xff\x00\x00\x00\x00\xff"
    old = tmp_path / "run" / "old.png"
    old.parent.mkdir()
    old.write_bytes(picture)
    os.utime(old, (time.time() - 100 * 86400,) * 2)
    (tmp_path / "run" / "new.png").write_bytes(picture)
    assert pc.prune(tmp_path) == 1 and [p.name for p in (tmp_path / "run").iterdir()] == ["new.png"]


@pytest.fixture
def test_window(tmp_path):
    """The agent's own test window, with its log; its program allowed in the catalog by its .exe."""
    log = tmp_path / "window.log"
    title = f"Тестовое окно агента {os.getpid()}-{time.monotonic_ns() % 10**6}"
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}  # test_ui's offscreen: no real window
    proc = subprocess.Popen([sys.executable, str(WINDOW), str(log), title], env=env)
    try:
        wait(lambda: log.exists() and "ready" in log.read_text("utf-8"), timeout=30)
        window = wait(lambda: next((w for w in pc.top_windows() if w["title"] == title), None), timeout=10)
        yield {"log": log, "title": title, "window": window}
    finally:
        proc.terminate()
        proc.wait(10)


def allow(conn, exe):
    found, _new = programs.add_manual(conn, exe, "Python (тест)", "2026-10-28T00:00:00Z")
    programs.decide(conn, found["id"], "allow", "app", "2026-10-28T00:00:00Z")


def test_the_agents_own_test_window(tmp_path, test_window):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    computer = pc.Computer(db, EventBus(db), tmp_path / "computer", watch_person=False)
    run = db.call(lambda c: store.create_conversation(c, kind="agent_run", title="Тест"))
    asked = []

    async def ask(tool, shown, cover):
        asked.append((tool, shown["element"], cover))
        return "not done: the user said no - не сейчас" if shown["element"] == "Отправить" else None

    async def go(name, **args):
        return await computer.handle(run, {"name": name, "input": args}, asyncio.Event(), ask)

    hwnd = test_window["window"]["id"]
    try:
        said, failed = asyncio.run(go("screen_windows"))
        assert not failed and test_window["title"] not in said  # not allowed yet: not seen
        assert "not done" not in said and "No window" in said
        assert asyncio.run(go("screen_look", window=hwnd)) == (
            f"no window {hwnd} of an allowed program is open now: see screen_windows", True)  # fmt: skip
        db.call(lambda c: allow(c, test_window["window"]["exe"]))
        said, failed = asyncio.run(go("screen_windows"))
        assert f"{hwnd}: «{test_window['title']}» - Python (тест)" in said
        assert asyncio.run(go("screen_act", window=hwnd, action="type", element=2, text="x"))[0].startswith(
            "look at the window first")  # fmt: skip

        async def scenario():
            tree, _ = await go("screen_look", window=hwnd)
            lines = tree.splitlines()
            number = {line.split('"')[1]: int(line.split("[")[1].split("]")[0]) for line in lines[1:] if '"' in line
                      and line.split("] ")[1].split(" ")[0] in ("edit", "checkbox", "button")}  # fmt: skip
            out = [tree]
            out.append(await go("screen_act", window=hwnd, action="type", element=number["Имя"], text="Анна"))
            out.append(await go("screen_act", window=hwnd, action="toggle", element=number["Запомнить"]))
            out.append(await go("screen_act", window=hwnd, action="type", element=number["Пароль"], text="123"))
            out.append(await go("screen_act", window=hwnd, action="click", element=number["Сохранить"]))
            out.append(await go("screen_act", window=hwnd, action="click", element=number["Отправить"]))
            out.append(await go("screen_act", window=hwnd, action="keys", keys="win+r"))
            out.append((await go("screen_look", window=hwnd, question="что написано?"))[0])
            return out

        tree, typed, ticked, password, saved, sent, keys, looked = asyncio.run(scenario())
        computer._watched = lambda: False  # the LocalChat window closed: no frame, no «stop»
        unwatched = asyncio.run(go("screen_act", window=hwnd, action="click", element=1))
    finally:
        computer.close()
    assert 'edit "Имя"' in tree and 'edit "Пароль" (password field: the user\'s, not yours)' in tree
    assert "секрет-123" not in tree and 'checkbox "Запомнить"' in tree and "[off]" in tree
    assert (
        not typed[1]
        and typed[0].startswith("typed [")
        and typed[0].endswith("«Имя». Look again to see the window now.")
    )
    assert ticked[0].startswith("toggled: on") and saved[0].startswith("clicked [")
    assert password == ("not done: a password field is the user's (D41) - ask the user to fill it in", True)
    assert sent[1] and "the user said no" in sent[0]
    assert keys[1] and "leaves the program" in keys[0]
    assert 'value="Анна"' in looked and "no model with vision is set" in looked
    assert unwatched[1] and "the LocalChat window is not open" in unwatched[0]
    log = test_window["log"].read_text("utf-8").splitlines()
    assert log[-1] == "save name=Анна remember=True note=''" and not any(line.startswith("send") for line in log)
    assert asked == [("screen_act", "Имя", True), ("screen_act", "Запомнить", True), ("screen_act", "Сохранить", True),
                     ("screen_send", "Отправить", False)]  # fmt: skip
    rows = db.call(lambda c: pc.journal(c, run))
    assert [r["action"]["action"] for r in rows] == ["type", "toggle", "click"]
    assert rows[0]["action"]["text"] == "…" and rows[0]["action"]["element_name"] == "Имя"  # typed text not kept
    assert all(Path(r["snapshot"]).read_bytes().startswith(b"\x89PNG") for r in rows)
    db.close()


class Eyes:
    """A local model with vision: what it was shown, and what it says."""

    name = "eye"

    def __init__(self):
        self.models = [Model("eye/m", "eye", "m", "Глаз", "local", {"vision": True}, {})]
        self.shown = []

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.shown.append(messages)
        yield StreamEvent("content", "На кнопке написано «Сохранить».")
        yield StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 2}, stop_reason="end_turn")


def answer_the_cards(hub):  # noqa: F811
    """The person allows the program for the task, and the one send."""
    first = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "screen_act"])[0]
    assert first["kind"] == "command" and first["preview"]["text"] == "ввести «Имя»\nтекст: Борис"
    assert first["preview"]["note"].startswith("«Python (тест)» · окно «")
    hub.post(f"/v1/approvals/{first['id']}", json={"allow": True, "scope": "chat"})
    send = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "screen_send"])[0]
    assert "отправка от вашего имени" in send["preview"]["note"]
    hub.post(f"/v1/approvals/{send['id']}", json={"allow": True})


def test_an_autonomous_run_at_the_test_window(hub, test_window):  # noqa: F811
    hub.app.state.computer._watch = False  # the test's own input is nobody's
    db = hub.app.state.db
    db.call(lambda c: allow(c, test_window["window"]["exe"]))
    eyes = use(hub, Eyes())
    db.call(lambda c: model_settings.save(c, model_settings.ALL, {"roles": {"vision": "eye/m"}}, by="app"))
    hwnd = test_window["window"]["id"]
    agent = use(hub, Scripted("agent", [
        [("screen_windows", {})],
        [("screen_look", {"window": hwnd, "question": "что на кнопке?"})],  # looking: before the plan too
        [("screen_act", {"window": hwnd, "action": "type", "element": 2, "text": "Борис"})],  # acting: refused
        [PLAN], [step(1, "running")],
        [("screen_act", {"window": hwnd, "action": "type", "element": 2, "text": "Борис"})],  # asked: for the task
        [("screen_act", {"window": hwnd, "action": "toggle", "element": 5})],  # covered
        [("screen_act", {"window": hwnd, "action": "click", "element": 10})],  # «Отправить»: asked every time
        [("finish", {"summary": "Имя вписано, отправлено.", "goal_met": True})],
    ]))  # fmt: skip
    with hub.websocket_connect("/v1/events", headers={**dict(hub.headers), "Host": "127.0.0.1:8765"}) as events:
        events.receive_json()  # the window is open: it draws the frame and has «stop» (D41)
        run_id = start(hub)
        answer_the_cards(hub)
        run = ended(hub, run_id)
        frames = []
        while (event := events.receive_json())["type"] != "computer.released":
            frames.append(event)
    assert [e["payload"]["action"] for e in frames if e["type"] == "computer.control"] == ["type", "toggle", "click"]
    assert next(e for e in frames if e["type"] == "computer.control")["payload"]["window"]["id"] == hwnd
    said = results(agent)
    assert run["status"] == "done" and f"{hwnd}: «{test_window['title']}»" in said[0]
    assert "На кнопке написано «Сохранить»." in said[1] and "make the plan first" in said[2]
    image = eyes.shown[0][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")
    assert said[5].startswith("typed [2]") and said[6].startswith("toggled: on") and said[7].startswith("clicked [10]")
    log = test_window["log"].read_text("utf-8").splitlines()
    assert log[-1] == "send name=Борис"
    tools = json.loads(db.call(lambda c: c.execute("SELECT tools FROM conversations WHERE id = ?",
                                                   (run_id,)).fetchone())["tools"])  # fmt: skip
    assert {"screen_windows", "screen_look", "screen_act", "open_program"} <= {t["name"] for t in tools}
    assert len(db.call(lambda c: pc.journal(c, run_id))) == 3
    assert hub.app.state.computer._holder is None  # the run ended: its hold went
