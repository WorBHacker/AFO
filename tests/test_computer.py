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
    from hub import retention  # snapshots past their term go (D30); the journal's rows stay

    assert retention.prune_screens(tmp_path, days=90, max_bytes=10**9) == 1
    assert [p.name for p in (tmp_path / "run").iterdir()] == ["new.png"]


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
    assert unwatched[1] and "the AFO window is not open" in unwatched[0]
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


def answer_the_cards(hub, autonomy):  # noqa: F811
    """The person approves the plan and allows the program for the task («План»), and the one send (both)."""
    if autonomy == "plan":
        plan = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "set_plan"])[0]
        hub.post(f"/v1/approvals/{plan['id']}", json={"allow": True})
        first = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "screen_act"])[0]
        assert first["kind"] == "command" and first["preview"]["text"] == "ввести «Имя»\nтекст: Борис"
        assert first["preview"]["note"].startswith("«Python (тест)» · окно «")
        hub.post(f"/v1/approvals/{first['id']}", json={"allow": True, "scope": "chat"})
    send = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "screen_send"])[0]
    assert "отправка от вашего имени" in send["preview"]["note"]
    hub.post(f"/v1/approvals/{send['id']}", json={"allow": True})


@pytest.mark.parametrize("autonomy", ["plan", "free"])
def test_an_autonomous_run_at_the_test_window(hub, test_window, autonomy):  # noqa: F811
    """«План»: acting in the program is asked once for the task; «Сам»: not at all. Sending: asked in both."""
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
        [("screen_act", {"window": hwnd, "action": "type", "element": 2, "text": "Борис"})],  # «План»: asked
        [("screen_act", {"window": hwnd, "action": "toggle", "element": 5})],  # covered
        [("screen_act", {"window": hwnd, "action": "click", "element": 10})],  # «Отправить»: asked every time
        [("finish", {"summary": "Имя вписано, отправлено.", "goal_met": True})],
    ]))  # fmt: skip
    with hub.websocket_connect("/v1/events", headers={**dict(hub.headers), "Host": "127.0.0.1:8765"}) as events:
        events.receive_json()  # the window is open: it draws the frame and has «stop» (D41)
        run_id = start(hub, autonomy=autonomy)
        answer_the_cards(hub, autonomy)
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
    asked = db.call(lambda c: [r["tool"] for r in c.execute("SELECT tool FROM approvals WHERE conversation_id = ?",
                                                            (run_id,))])  # fmt: skip
    assert asked.count("screen_send") == 1  # sending: asked in both
    assert (asked.count("screen_act") > 0) == (autonomy == "plan")  # «Сам»: no card for acting at all


def test_what_is_in_sight_and_enter_that_sends(tmp_path, test_window, monkeypatch):
    """2026-10-28 (the user's check: the agent sent the message, then went on checking until its step limit): a look
    lists what is in sight - the rest of a long history is counted, not listed (it used to fill the look before the
    input field was reached); a part is looked into on its own; Enter in a field with text is a send, asked every
    time, as the send button is; in a search field, or shift+Enter, it is not."""
    assert pc.enter_only("enter") and pc.enter_only("Ctrl+Enter") and not pc.enter_only("shift+enter")
    assert not pc.enter_only("ctrl+s") and not pc.enter_only("")
    assert (
        pc.overlap((0, 0, 10, 10), (5, 5, 20, 20)) == (5, 5, 10, 10) and pc.overlap((0, 0, 5, 5), (5, 0, 9, 9)) is None
    )
    item = {"role": "edit", "name": "Сообщение", "password": False, "value": "", "toggled": None, "enabled": True,
            "place": (1, 2, 3, 4), "depth": 0, "focus": True}  # fmt: skip
    text = pc.tree_text({"id": 7, "title": "Чат", "program": "Telegram"}, [item], hidden=40)
    assert text == ('Window 7 «Чат» - Telegram; 1 elements; 40 more out of sight (scrolled away or outside the window) '
                    '- scroll to see them:\n[1] edit "Сообщение" (focus) at 1,2 3x4')  # fmt: skip
    pressed = []
    monkeypatch.setattr(pc, "bring_to_front", lambda hwnd: True)  # no real keys anywhere: the person's desktop
    monkeypatch.setattr(pc, "press", pressed.append)
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    db.call(lambda c: allow(c, test_window["window"]["exe"]))
    computer = pc.Computer(db, EventBus(db), tmp_path / "computer", watch_person=False)
    run = db.call(lambda c: store.create_conversation(c, kind="agent_run", title="Тест"))
    asked = []

    async def ask(tool, shown, cover):
        asked.append((tool, shown["element"], shown["text"], cover))
        return "not done: the user said no" if shown["element"].startswith("окно не впереди") else None

    async def go(name, **args):
        return await computer.handle(run, {"name": name, "input": args}, asyncio.Event(), ask)

    hwnd = test_window["window"]["id"]

    async def scenario():
        tree, _ = await go("screen_look", window=hwnd)
        lines = tree.splitlines()
        number = {line.split('"')[1]: int(line.split("[")[1].split("]")[0]) for line in lines[1:] if '"' in line}
        typed = await go("screen_act", window=hwnd, action="type", element=number["Сообщение"], text="Привет")
        uia = computer._uia_now()
        uia.focused_field = lambda _hwnd: None  # the focus in another window (the person's): unseen, so asked
        unknown = await go("screen_act", window=hwnd, action="keys", keys="enter")
        uia.focused_field = lambda _hwnd: {"text": True, "role": "edit", "name": "Сообщение", "value": "Привет"}
        sent = await go("screen_act", window=hwnd, action="keys", keys="enter")
        uia.focused_field = lambda _hwnd: {"text": True, "role": "edit", "name": "Поиск", "value": "bekh"}
        searched = await go("screen_act", window=hwnd, action="keys", keys="enter")
        newline = await go("screen_act", window=hwnd, action="keys", keys="shift+enter")
        part, _ = await go("screen_look", window=hwnd, part=number["История"])  # its numbers are the next ones
        found = [(await go("screen_look", window=hwnd, find=name))[0] for name in ("behruz bro", "Бехруз", "nobody")]
        return lines, part, typed, unknown, sent, searched, newline, found

    try:
        lines, part, typed, unknown, sent, searched, newline, found = asyncio.run(scenario())
    finally:
        computer.close()
    # a chat found by its name, not a made-up @username (2026-10-28): every word, any case, Cyrillic as Latin
    whole = {line.split('"')[1]: line.split("]")[0] + "]" for line in lines[1:] if '"' in line}
    assert "1 of " in found[0].splitlines()[0] and "fit «behruz bro»" in found[0].splitlines()[0]
    assert found[0].splitlines()[1].startswith(f'{whole["Behruz Bro"].strip()} listitem "Behruz Bro"')
    assert '"Behruz Bro"' in found[1] and '"Бехруз Каримов"' in found[1] and '"Asadullo Aka"' not in found[1]
    assert "0 of " in found[2] and "one word of the name at a time" in found[2] and "@username" in found[2]
    head = lines[0]
    assert "more out of sight (scrolled away or outside the window) - scroll to see them" in head
    assert any('text "строка 1"' in line for line in lines) and not any("строка 300" in line for line in lines)
    assert any('edit "Сообщение"' in line for line in lines)  # the field under the long history is in the look
    assert ", inside group «История»;" in part.splitlines()[0] and 'text "строка 1"' in part
    assert typed[0].startswith("typed [") and unknown == ("not done: the user said no", True)
    assert not sent[1] and not searched[1] and not newline[1]
    assert [(tool, element, text) for tool, element, text, _cover in asked[1:]] == [
        ("screen_send", "окно не впереди — куда уйдёт Enter, не видно", ""),
        ("screen_send", "Сообщение", "Привет"),
        ("screen_act", "", ""),
        ("screen_act", "", ""),
    ]
    assert pressed == [[0x0D], [0x0D], [0x10, 0x0D]]  # the refused one was not pressed
    db.close()


def test_a_name_as_the_agent_looks_for_it():
    """2026-10-28: «напиши behruz bro» became a made-up @behruz_bro that Telegram did not know. A name is found in
    the list by its words - any case, Cyrillic as Latin, х and x as h - and the guide says not to make up usernames."""
    assert pc.name_key("Ulug'bek  Aka") == "ulugbek aka" and pc.name_key("@behruz_bro") == "behruz bro"
    assert pc.name_key("Хуршид") == pc.name_key("Xurshid") == pc.name_key("Khurshid") == "hurshid"
    assert pc.name_fits("behruz bro", "Behruz Bro") and pc.name_fits("БЕХРУЗ БРО", "Behruz Bro")
    assert pc.name_fits("behr", "Behruz Bro") and pc.name_fits("bro behruz", "Behruz Bro")  # words, any order
    assert not pc.name_fits("behruz bro", "Behruz Karimov") and not pc.name_fits("", "Behruz Bro")
    items = [{"role": "listitem", "name": n, "password": False, "value": None, "toggled": None, "enabled": True,
              "place": (0, 20 * i, 200, 20), "depth": 3, "focus": False}
             for i, n in enumerate(["Избранное", "Behruz Bro", "Ulug'bek Aka"])]  # fmt: skip
    text = pc.tree_text({"id": 7, "title": "Telegram", "program": "Telegram"}, items, find="behruz bro")
    assert text == (
        'Window 7 «Telegram» - Telegram; 1 of 3 elements fit «behruz bro»:\n[2] listitem "Behruz Bro" at 0,20 200x20'
    )  # its number in the whole look
    guide = __import__("hub.autonomous", fromlist=["GUIDE"]).GUIDE
    assert "never make up a @username from a name" in guide and 'find="the name"' in guide


def test_windows_windows_keeps_out_of_sight_are_not_offered(monkeypatch):
    """A cloaked window (the input host's full-screen layer, a suspended Store app) is "visible" to Windows but not
    to the person: the agent does not get it."""
    from hub import computer

    assert computer.top_windows()  # this desktop has some windows
    monkeypatch.setattr(computer, "cloaked", lambda hwnd: True)
    assert computer.top_windows() == []


def test_localchat_itself_is_never_the_agents_to_control(monkeypatch):
    """Whatever is allowed - IDLE runs in pythonw.exe as AFO's window does - AFO's own windows stay out, an installed
    AFO.exe too, and LocalChat.exe of before the name AFO (10.4b)."""
    import os

    from hub import computer

    assert computer.is_localchat(os.getpid())  # the hub
    lines = {1: ["C:\\Python314\\pythonw.exe", "-m", "app"], 2: ["C:\\Program Files\\LocalChat\\LocalChat.exe"],
             3: ["C:\\Python314\\pythonw.exe", "C:\\Python314\\Lib\\idlelib\\idle.pyw"],
             4: ["notepad.exe"], 5: ["C:\\Users\\u\\AppData\\Local\\Programs\\AFO\\AFO.exe"]}  # fmt: skip
    monkeypatch.setattr(computer, "_cmdline", lambda pid: lines.get(pid, []))
    assert [pid for pid in lines if computer.is_localchat(pid)] == [1, 2, 5]


def test_a_program_opens_a_link_of_its_own_kind(tmp_path, monkeypatch):
    """Telegram opens tg://resolve?domain=NAME as the chat of exactly @NAME; a link goes only to the program Windows
    opens it with, and never a web page, a file or the shell."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    telegram = "C:\\Users\\u\\AppData\\Roaming\\Telegram Desktop\\Telegram.exe"

    def add(conn):
        found, _new = programs.add_manual(conn, telegram, "Telegram", "2026-10-28T00:00:00Z")
        programs.decide(conn, found["id"], "allow", "app", "2026-10-28T00:00:00Z")

    db.call(add)
    computer = pc.Computer(db, EventBus(db), tmp_path / "computer", watch_person=False)
    run = db.call(lambda c: store.create_conversation(c, kind="agent_run", title="Тест"))
    opened, asked = [], []
    monkeypatch.setattr(pc.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(pc, "link_handler", lambda scheme: telegram.lower() if scheme == "tg" else "C:\\Other\\x.exe")

    async def ask(tool, shown, cover):
        asked.append(shown["text"])
        return None

    def go(**args):
        return asyncio.run(computer.handle(run, {"name": "open_program", "input": args}, asyncio.Event(), ask))

    said, failed = go(program="Telegram", link="tg://resolve?domain=bekh_jr")
    assert not failed and "opens tg://resolve?domain=bekh_jr" in said
    assert opened == ["tg://resolve?domain=bekh_jr"] and asked == ["tg://resolve?domain=bekh_jr"]  # on the card
    for link, why in (("https://t.me/bekh_jr", "web pages"), ("file:///C:/x.exe", "web pages"),
                      ("viber://chat?number=1", "does not open viber"), ("not a link", "web pages")):  # fmt: skip
        said, failed = go(program="Telegram", link=link)
        assert failed and why in said, (link, said)
    assert len(opened) == 1  # nothing else was opened
    assert pc.link_handler("tg") == telegram.lower()  # the stand-in; the real one reads the registry
    assert any(t["name"] == "open_program" and "link" in t["parameters"]["properties"] for t in pc.TOOLS)


def test_a_steam_game_starts_through_steam(tmp_path, monkeypatch):
    """25.2: a game of Steam's libraries has no .exe of its own in the catalog - Steam starts it by its id."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    game = programs.Found("steam:292030", "Witcher", "game", folder="D:\\Steam\\steamapps\\common\\The Witcher 3")

    def add(conn):
        programs.apply_scan(conn, [game], "2026-11-06T00:00:00Z")
        programs.decide(conn, programs.program_id(game.key), "allow", "app", "2026-11-06T00:00:00Z")

    db.call(add)
    computer = pc.Computer(db, EventBus(db), tmp_path / "computer", watch_person=False)
    run = db.call(lambda c: store.create_conversation(c, kind="agent_run", title="Тест"))
    opened = []
    monkeypatch.setattr(pc.os, "startfile", opened.append, raising=False)

    async def ask(tool, shown, cover):
        return None

    said, failed = asyncio.run(computer.handle(run, {"name": "open_program", "input": {"program": "Witcher"}},
                                               asyncio.Event(), ask))  # fmt: skip
    assert not failed and said.startswith("started «Witcher»") and opened == ["steam://rungameid/292030"]


def test_what_the_screen_shows_is_secret_filtered(tmp_path, monkeypatch):
    """The audit (D46): another program's window may show a key or a password - it goes to the model masked, as a
    file's text does (D10); a cloud model gets the look too."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    computer = pc.Computer(db, EventBus(db), tmp_path / "computer", watch_person=False)
    key = "sk-ant-api03-" + "s" * 40

    async def look(run_id, args):
        return f"Window 7 «Настройки» - Notes; [3] edit «API key» value «{key}»"

    monkeypatch.setattr(computer, "_look", look)
    output, error = asyncio.run(computer.handle("r1", {"name": "screen_look", "input": {"window": 7}}, None, None))
    assert not error and key not in output and "[REDACTED]" in output
    # a cloud model sees other programs' windows only in a task that lets it (Q20, D46, 22.3: tests/test_autonomous.py)
    looked = {"name": "screen_look", "input": {"window": 7}}
    output, error = asyncio.run(computer.handle("r1", looked, None, None, cloud=True))
    assert error and "did not let the cloud see other programs' windows in this task" in output
    monkeypatch.setattr(pc, "screen_cloud", lambda conn, run_id: run_id == "r1")
    output, error = asyncio.run(computer.handle("r1", looked, None, None, cloud=True))
    assert not error and "[REDACTED]" in output
