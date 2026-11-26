"""Stages 11, 17 and 18 together (2026-11-06, the user's word «проводи общий тест»): what each does when it meets the
others - a goal in «Песочница» with a part applied meanwhile and a dead end after it; a goal's stages with the loop
guard and with two models; a rollback card over the editor's unsaved edits; Claude Code's goal with its edit allowed
in part; the symbol index of a sandbox and of its project.

One passed at first, six showed one part breaking another: each became a step of stage 19 (D53), and each passes
since its step."""

import asyncio
import hashlib
import json
import logging
import sqlite3
import struct
import threading
import time
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from app.events import TOPICS, EventThread
from common.paths import data_paths
from hub import claude_code, completion, engines, goals, gpu_card, gpu_prefs, tuning
from hub import llama_config as lc
from hub import model_install as mi
from hub.api_reference import event_types
from hub.auth import create_client
from hub.chat import Limits
from hub.claude_code import ClaudeCodeProvider
from hub.events import matches
from hub.gateway import GatewayError
from hub.gateway.openai_compat import OpenAICompatProvider
from hub.projects import get_project
from tests import test_changes as ch
from tests import test_claude_code as cc
from tests import test_commands as tc
from tests.test_agents import delegate, ended, profile, register, run_of
from tests.test_attachments import b64
from tests.test_autonomous import PLAN, Scripted, use
from tests.test_changes import edit, wait
from tests.test_gateway import Stalled, stalled  # noqa: F401 - the stalled fixture
from tests.test_goals import DEAD_END, DONE, WRITE, Cloudy, Gated, chat_with_project, set_goal, settled
from tests.test_llama_config import FakeLlamaSwap
from tests.test_model_install import CLIP, DISTILLED, DRAW, FILM, MACHINE, FakeHub, fake_hub, tiny_gguf, wait_for  # noqa: F401 - the fake_hub fixture
from tests.test_model_install import hub as installs  # noqa: F401 - the installer's hub, as a fixture of this name
from tests.test_tools import ToolProvider, hub  # noqa: F401 - the hub fixture
from tests.test_tuning import setup, spec_server  # noqa: F401 - the tuning's fixtures
from tests.test_ui import click, item_named, item_saying, qapp, wait_until  # noqa: F401 - the qapp fixture
from tests.test_web import fetch_call, site  # noqa: F401 - the site fixture

A_TXT = "if (Test-Path a.txt) { exit 0 } else { exit 1 }"


def written(path, content):
    return [("write_file", {"project": "proj", "path": path, "content": content})]


def test_a_dead_end_after_a_part_was_applied_does_not_take_it_back(hub, repo):  # noqa: F811
    """17.1 × 11.6: the user pauses a goal in «Песочница», applies one of the files the model made, resumes; a dead
    end later goes back to the goal's start - the file the user took must not come back as a change that deletes it."""
    two = [*written("a.txt", "нужное\n"), *written("b.txt", "черновик\n")]
    worker = use(hub, Gated("agent", [two, "Сделал a.txt и b.txt.", *DEAD_END, WRITE, "Готово."]))
    worker.gate.clear()
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    set_goal(hub, conv)
    wait(worker.waiting.is_set)
    assert hub.post(f"/v1/conversations/{conv}/goal/pause").status_code < 300
    worker.gate.set()
    settled(hub, conv, "paused")
    applied = hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply", json={"files": ["a.txt"]})
    assert applied.status_code == 200 and applied.json()["applied"] == ["a.txt"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "нужное\n"
    assert hub.post(f"/v1/conversations/{conv}/goal/resume", json={}).status_code < 300
    done = settled(hub, conv, "achieved", "blocked")
    assert [c["route"] for c in done["checks"]] == ["continue", "investigate", "investigated", "rolled_back",
                                                    "achieved"]  # fmt: skip
    files = hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"][0]["files"]
    shown = {f["path"]: f["status"] for f in files}
    assert "a.txt" not in shown, f"after the rollback the sandbox offers to undo what the user applied: {shown}"


def test_a_goal_goes_on_with_what_the_user_applied(hub, repo):  # noqa: F811
    """17.1 × 11: the user pauses a goal in «Песочница» and applies all it made - the sandbox is taken away; resumed,
    the goal works in a new one. What the user took is the project's now: the goal must not lose it."""
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "Сделал a.txt.", WRITE, "Готово."]))
    worker.gate.clear()
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    set_goal(hub, conv, criteria=[{"command": A_TXT}, {"command": DONE}])
    wait(worker.waiting.is_set)
    assert hub.post(f"/v1/conversations/{conv}/goal/pause").status_code < 300
    worker.gate.set()
    settled(hub, conv, "paused")
    assert hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply").json()["applied"] == ["a.txt"]
    assert (repo / "a.txt").exists()
    assert hub.post(f"/v1/conversations/{conv}/goal/resume", json={}).status_code < 300
    done = settled(hub, conv, "achieved", "blocked", "budget")
    first = done["checks"][0]
    assert done["goal"]["status"] == "achieved", (
        f"{done['goal']['detail']!r}; after it: {[(c['route'], c['missing']) for c in done['checks']][:2]}"
    )
    assert first["route"] == "achieved"


def test_stages_that_go_on_are_not_going_round_in_circles(hub, repo):  # noqa: F811
    """11.5 × 11.4: a goal in three stages whose only criterion is met at the end - each turn closes a stage, the
    criterion's failure stays the same until the last one. That is progress, not a loop."""
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "STAGE DONE: создан a.txt", written("b.txt", "b\n"),
                                      "STAGE DONE: создан b.txt", WRITE, "STAGE DONE: создан done.txt"]))  # fmt: skip
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, milestones=["Создать a.txt", "Создать b.txt", "Создать done.txt"])
    done = settled(hub, conv, "achieved", "blocked")
    routes = [c["route"] for c in done["checks"]]
    assert routes == ["continue", "continue", "achieved"], f"routes {routes}, the goal {done['goal']['detail']!r}"


def test_the_strong_models_plan_outlives_the_first_stage(hub, repo):  # noqa: F811
    """11.7 × 11.5: the strong model writes the plan in its first turn «for the lighter model»; once the first stage
    is done its turns go to the model as one line - the plan must still reach the next turns."""
    use(hub, Gated("strong", ["ПЛАН: сначала a.txt, потом done.txt в корне проекта."]))
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "STAGE DONE: создан a.txt", WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, criteria=[{"command": A_TXT}, {"command": DONE}], strong_model_id="strong/m",
             milestones=["Создать a.txt", "Создать done.txt"])  # fmt: skip
    done = settled(hub, conv, "achieved", "blocked")
    assert done["goal"]["milestones"][0]["status"] == "done"
    later = worker.prompts[2]  # the first request of the turn after stage 1
    assert "ПЛАН" in str(later), "the plan was folded away with stage 1"


def test_a_rollback_card_over_the_editors_unsaved_edits_says_so(hub, repo):  # noqa: F811
    """11.6 × D47 (14.2c): in the project a dead end asks to go back; a file it would remove or put back is open in
    the window's editor with unsaved edits - the card must say so, as a model's write into it does."""
    junk = written("junk.txt", "мусор\n")
    worker = use(hub, Gated("agent", [junk, *DEAD_END, WRITE, "Готово."]))
    pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv)
    settled(hub, conv, "blocked")
    assert hub.put("/v1/editor/dirty", json={"files": [{"project_id": pid, "path": "junk.txt"}]}).status_code == 200
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "goal_rollback" and "junk.txt" in asked["preview"]["text"]
    assert asked["unsaved"] is True, "the rollback would remove a file the editor holds unsaved, and the card is silent"


def test_a_sandboxs_definitions_stay_out_of_its_projects_index(hub, repo):  # noqa: F811
    """«Песочница» × the symbol index (18.3 reads it by meaning): the sandbox's copy and the project share the
    project's id - a definition only the sandbox has must not turn up in the project's own chat."""
    worker = use(hub, Gated("agent", []))
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    (copy,) = hub.portal.call(hub.app.state.chat.tool_projects, conv)
    real = hub.app.state.db.call(lambda c: get_project(c, pid))
    assert copy.root != real.root
    (copy.root / "src" / "helper.py").write_text('def sandbox_only_helper():\n    """Helps in the sandbox only."""\n',
                                                encoding="utf-8")  # fmt: skip
    symbols = hub.app.state.chat.symbols
    assert symbols.find(copy, "sandbox_only_helper")["total"] == 1  # the sandbox's chat finds it
    by_name = symbols.find(real, "sandbox_only_helper")["total"]
    by_meaning = [s["name"] for s in symbols.find(real, "helps sandbox")["symbols"]]
    assert (by_name, by_meaning) == (0, []), "the project's chat finds what only the sandbox has"


def test_the_time_a_turn_waits_for_the_human_is_not_the_goals(hub, repo):  # noqa: F811
    """19.7 (20.4): a goal's turn asks a card in a chat that asks; the user answers a while later - the goal's time
    counts the model's work, not the user's absence."""
    worker = use(hub, Gated("agent", [WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker, mode="ask")
    set_goal(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    time.sleep(4)  # the user is away
    assert hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True}).status_code == 200
    done = settled(hub, conv, "achieved", "blocked")
    assert done["goal"]["status"] == "achieved" and done["goal"]["seconds_used"] < 2.5, done["goal"]


def test_applying_while_the_goal_writes_goes_after_its_turn(hub, repo):  # noqa: F811
    """19.7 (20.4): «Применить» in the sandbox while a goal's turn runs is not refused: it waits for the turn to end
    and goes before the next one - which works on with it applied."""
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "Сделал a.txt.", WRITE, "Готово."]))
    worker.gate.clear()
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    set_goal(hub, conv, criteria=[{"command": A_TXT}, {"command": DONE}])
    wait(worker.waiting.is_set)
    said = hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply")
    assert said.status_code == 200 and said.json()["deferred"] is True
    assert not (repo / "a.txt").exists()  # not under the running turn's feet
    worker.gate.set()
    done = settled(hub, conv, "achieved", "blocked", "budget")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a\n"  # it went when the turn ended
    assert done["goal"]["status"] == "achieved", done["goal"]["detail"]
    assert [c["route"] for c in done["checks"]] == ["continue", "achieved"]


def test_the_seconds_cards_waited_count_once():
    """19.7: the time a turn's cards waited - each from asked to answered, an open one to the turn's end, two at once
    counted once, nothing outside the turn."""
    since = datetime(2026, 11, 6, 10, 0, 0, tzinfo=UTC)
    until = since + timedelta(seconds=100)

    def at(s):
        return (since + timedelta(seconds=s)).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    assert goals.waiting([], since, until) == 0
    assert goals.waiting([(at(10), at(30))], since, until) == 20
    assert goals.waiting([(at(10), at(30)), (at(20), at(40)), (at(50), at(55))], since, until) == 35
    assert goals.waiting([(at(90), None)], since, until) == 10  # still open at the turn's end
    assert goals.waiting([(at(-50), at(5)), (at(95), at(130))], since, until) == 10  # only within the turn


# --- Claude Code (a live hub, the stand-in CLI) ---


@pytest.fixture
def live(repo, tmp_path, monkeypatch):
    log = tmp_path / "fake-claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    server = cc.LiveHub()
    try:
        server.app.state.claude_code._find = lambda: cc.FAKE
        server.app.state.gateway.register(ClaudeCodeProvider(lambda: cc.FAKE))
        assert server.http.post("/v1/models/sync").status_code == 200
        server.repo, server.log = repo, log
        server.pid = server.http.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        server.http.patch(f"/v1/projects/{server.pid}", json={"cloud_ok": True})
        yield server
    finally:
        server.close()


def test_claude_codes_goal_with_its_edit_allowed_in_part(live):
    """11.7 × 17.3: Claude Code works on a goal in a chat that asks; the user allows two of its edit's three hunks -
    the hub writes them, Claude Code hears «no» with what went, and the goal's check finds the criterion met."""
    lines = "".join("x = 1\n" if n in (1, 20, 40) else f"# {n}\n" for n in range(1, 41))
    (live.repo / "conf.py").write_text(lines, encoding="utf-8", newline="\n")
    cid = cc.chat_with_project(live)
    cc.set_goal(live, cid)
    asked = live.wait_approval()
    assert asked["tool"] == "Edit" and len(asked["preview"]["hunks"]) == 3
    assert live.http.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "hunks": [0, 2]}).status_code == 200
    done = cc.goal_settled(live, cid, "achieved", "blocked", "budget")
    assert done["goal"]["status"] == "achieved", done["goal"]["detail"]
    now = (live.repo / "conf.py").read_text(encoding="utf-8").splitlines()
    assert (now[0], now[19], now[39]) == ("x = 2", "x = 1", "x = 2")


# --- the hub's events on their way to the window: a real socket, the window's own thread (2026-11-10, the user's word)


@pytest.fixture
def served():
    server = cc.LiveHub()
    try:
        yield server
    finally:
        server.close()


def say(server, kind, payload, **how):
    """An event published on the served hub's own loop, as its modules do it."""
    loop = server.server.servers[0].get_loop()
    asyncio.run_coroutine_threadsafe(server.app.state.bus.publish(kind, payload, **how), loop).result(10)


def of_kind(events, kind):
    return [e for e in events if e["type"] == kind]


@contextmanager
def window_listening(server, token):
    """The window's EventThread, connected and subscribed: what it hands over, and its connection's ups and downs."""
    events, links = [], []
    thread = EventThread(f"ws://127.0.0.1:{server.server.config.port}/v1/events", token)
    thread.event.connect(events.append, Qt.ConnectionType.DirectConnection)
    thread.connected.connect(links.append, Qt.ConnectionType.DirectConnection)
    thread.start()
    try:
        wait(lambda: links)
        say(server, "hub.listening", {})  # stored: once it has come, replayed or live, the subscription is in place
        wait(lambda: of_kind(events, "hub.listening"))
        yield events, links
    finally:
        thread.stop()
        assert thread.wait(10_000), "the window's event thread did not end"


IN_A_CIRCLE: dict = {}
IN_A_CIRCLE["self"] = IN_A_CIRCLE


def errors_about(caplog, kind):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and kind in r.getMessage()]


class Unreadable(dict):
    """A value that breaks when it is read, with an error JSON's own checks do not raise."""

    def items(self):
        raise OverflowError("too big to read")


def nested(levels, inside):
    for _ in range(levels):
        inside = {"in": inside}
    return inside


# each one as the window gets it, stored or live: an object stays an object and a set an array; what holds itself,
# goes too deep or breaks when read is None - the window's `payload.get(...) or {}` takes that, not a text
SAID_AS = [pytest.param({1, 2}, [1, 2], id="a set"), pytest.param(object(), str, id="object()"),
             pytest.param("\ud83d", "?", id="half an emoji"),
             pytest.param(IN_A_CIRCLE, {"self": None}, id="in a circle"),
             pytest.param(nested(150, {1}), nested(99, None), id="too deep"),
             pytest.param(Unreadable(a=1), None, id="breaks when read"),
             pytest.param(10**5000, None, id="an int with no text")]  # fmt: skip


@pytest.mark.parametrize(("unsayable", "said"), SAID_AS)
def test_a_stored_event_that_cannot_be_said_goes_out_in_plain_words(served, caplog, unsayable, said):
    """Such a payload raised in the publisher, whatever it was doing. The event is what matters - `chat.done` is what
    ends an answer in the window - so it is stored and sent: the rest of its payload as it was, the odd part in plain
    words."""
    token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
    with window_listening(served, token) as (events, links):
        say(served, "chat.done", {"conversation_id": "c", "message_id": 7, "odd": unsayable})
        wait(lambda: of_kind(events, "chat.done"), timeout=5)
        assert links == [True], "one event is no reason to drop the connection"
    payload = of_kind(events, "chat.done")[0]["payload"]
    assert (payload["conversation_id"], payload["message_id"]) == ("c", 7)
    assert isinstance(payload["odd"], said) if isinstance(said, type) else payload["odd"] == said
    stored = served.app.state.db.call(
        lambda conn: conn.execute("SELECT payload FROM events WHERE type = 'chat.done'").fetchone()[0]
    )
    assert json.loads(stored) == payload, "a window that reconnects must read what the live one got"
    assert errors_about(caplog, "chat.done"), "an error in the log must say which event it was"


@pytest.mark.parametrize("persist", [True, False], ids=["stored", "live"])
def test_a_payload_that_breaks_when_read_as_a_whole_still_goes_out(served, caplog, persist):
    """Nothing of it can be read, not even which chat it is about: the event still goes - as {"unsayable": true}, and
    stored when it is a stored one - rather than being lost without a trace, and its publisher goes on."""
    token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
    with window_listening(served, token) as (events, links):
        say(served, "chat.done", Unreadable(conversation_id="c"), persist=persist)  # raises if publish does
        wait(lambda: of_kind(events, "chat.done"), timeout=5)
        assert links == [True]
    assert of_kind(events, "chat.done")[0]["payload"] == {"unsayable": True}
    if persist:
        stored = served.app.state.db.call(
            lambda conn: conn.execute("SELECT payload FROM events WHERE type = 'chat.done'").fetchone()[0]
        )
        assert json.loads(stored) == {"unsayable": True}
    assert any("too big to read" in line for line in errors_about(caplog, "chat.done")), caplog.text[:2000]


@pytest.mark.parametrize(("unsayable", "said"), SAID_AS)
def test_a_live_event_goes_out_in_plain_words_and_the_stream_goes_on(served, caplog, unsayable, said):
    """One such event took the sending task with it: the connection stayed up and answered pings, the window said
    «подключён» - and chats, agents and projects heard nothing more until a restart (the audit of 2026-11-09). Left
    out instead, it is lost for good: a live event is not stored, and an agent's frame over a window, a process list or
    a file's history would stand as they were. So it goes as a stored one does, its other fields as they were."""
    token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
    with window_listening(served, token) as (events, links):
        say(served, "chat.delta", {"conversation_id": "c", "kind": "content", "text": unsayable}, persist=False)
        say(served, "chat.delta", {"conversation_id": "c", "kind": "content", "text": "дальше"}, persist=False)
        wait(lambda: len(of_kind(events, "chat.delta")) == 2, timeout=5)
        assert links == [True], "one event is no reason to drop the connection"
    first, then = (e["payload"] for e in of_kind(events, "chat.delta"))
    assert (first["conversation_id"], first["kind"], then["text"]) == ("c", "content", "дальше")
    assert isinstance(first["text"], said) if isinstance(said, type) else first["text"] == said
    assert errors_about(caplog, "chat.delta"), "an error in the log must say which event it was"


def test_a_stream_that_breaks_tells_the_window_to_come_back_for_what_it_missed(served, caplog):
    """Not one event's fault: the hub closes with 1013 instead of going silent, and the window's thread reconnects with
    `since` and reads the stored event. The breakage is real: the table the check of who may see an event reads is
    away for a moment - only an outside client's events go through that check."""
    db = served.app.state.db
    token = db.call(lambda conn: create_client(conn, "outside", cloud=False))
    made = served.http.post("/v1/conversations", headers={"Authorization": f"Bearer {token}"}, json={"title": ""})
    assert made.status_code == 201
    with caplog.at_level(logging.INFO, logger="app.events"), window_listening(served, token) as (events, links):
        db.call(lambda conn: conn.execute("ALTER TABLE conversations RENAME TO conversations_away"))
        say(served, "chat.done", {"conversation_id": made.json()["id"], "message_id": 1})
        wait(lambda: False in links, timeout=5)
        db.call(lambda conn: conn.execute("ALTER TABLE conversations_away RENAME TO conversations"))
        wait(lambda: of_kind(events, "chat.done"))
        assert links[:2] == [True, False] and links[-1] is True
    assert len(of_kind(events, "chat.done")) == 1
    assert "1013" in caplog.text, "the window must have been told to try again"


def test_the_window_subscribes_to_every_event_the_hub_sends_for_it():
    """TOPICS is kept by hand, and what it misses never arrives, without a word: `history.changed` did not, and
    «История файла» stood still. A new event: a topic for it in app/events.py, or a place in this list, with why."""
    not_for_the_window = {
        "editor.focus",  # an outside editor's place in a file, read by GET /v1/editor
        "settings.imported",  # the window that asked for the import has the result in the answer
    }
    assert {kind for kind in event_types() if not matches(kind, tuple(TOPICS))} == not_for_the_window


# --- a background task that breaks: its error in the log, not lost (2026-11-10, the user's word)


def test_a_background_task_that_breaks_leaves_its_error_in_the_log(served, caplog):
    """A chat run's task raised past its own `except`s - from its `on_end`, where an agent's `_finish` comes in. The
    done-callback meant to surface it read `task.exception()` and so marked the error as seen: asyncio said nothing,
    and the hub's log had no line of it. Now the crash is in the log, with its traceback and the task's name."""
    served.app.state.gateway.register(Scripted("agent", ["Готово."]))
    assert served.http.post("/v1/models/sync").status_code == 200
    cid = served.http.post("/v1/conversations", json={}).json()["id"]

    async def broken(_outcome):
        raise RuntimeError("the run's end broke")

    loop = served.server.servers[0].get_loop()
    start = served.app.state.chat.start(cid, "agent/m", "Привет", limits=Limits(), on_end=broken)
    asyncio.run_coroutine_threadsafe(start, loop).result(10)

    def logged():
        found = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
        return next((r for r in found if str(r.exc_info[1]) == "the run's end broke"), None)

    crash = wait(logged)
    assert "ChatService._run" in crash.getMessage()


# --- «Стоп» while the model's socket says nothing (2026-11-10, the user's word)


def test_stop_ends_an_answer_whose_stream_went_silent(served, stalled):  # noqa: F811
    """A llama-server that hung, a half-open connection to the cloud: the answer stopped midway and nothing more came.
    The provider looked at the stop only when a piece came, so «Стоп» did nothing - the chat stayed busy for ever and
    a new message was refused. Now the stop ends it: what came is kept, the window hears `chat.done`, the connection
    is let go."""
    served.app.state.gateway.register(OpenAICompatProvider("stalled", stalled.url))
    assert served.http.post("/v1/models/sync").status_code == 200
    cid = served.http.post("/v1/conversations", json={}).json()["id"]
    token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
    with window_listening(served, token) as (events, _links):
        body = {"content": "go", "model_id": "stalled/coder"}
        assert served.http.post(f"/v1/conversations/{cid}/messages", json=body).status_code == 202
        wait(lambda: of_kind(events, "chat.delta"))
        assert served.http.post(f"/v1/conversations/{cid}/cancel").json()["cancelled"] is True
        done = wait(lambda: of_kind(events, "chat.done"), timeout=10)
    assert done[-1]["payload"]["cancelled"] is True
    assert stalled.closed.wait(5), "the hub kept the silent connection open"
    saved = served.wait_idle(cid, timeout=5)["messages"][-1]
    assert [b["text"] for b in saved["content"] if b["type"] == "text"] == [Stalled.PIECE]


# --- an agent's chat deleted while it waits or works (2026-11-10, the user's word)


def test_deleting_a_queued_runs_chat_lets_the_gpu_go_on(hub, repo):  # noqa: F811
    """A local run waits for the graphics card, and the user deletes its chat. Deleting stopped only a chat's answer,
    and this run had none yet: its row went with the chat, and once the card was free the run took it, could not
    read itself back and fell holding it - every local run after it stayed «queued» until the hub restarted. Now
    deleting the chat stops the run first, and the card goes on to the next one."""
    register(hub, ToolProvider([[{"id": "w1", "name": "edit_file", "input": edit()}]]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid])
    first = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "первая"}).json()["id"]
    wait(lambda: hub.get("/v1/approvals").json()["approvals"])  # the first holds the card, waiting for the user
    second = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "вторая"}).json()["id"]
    wait(lambda: run_of(hub, second)["status"] == "queued")
    assert hub.delete(f"/v1/conversations/{second}").status_code == 204
    assert hub.post(f"/v1/runs/{first}/cancel").json() == {"cancelled": True}
    assert ended(hub, first)["status"] == "cancelled"
    third = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "третья"}).json()["id"]
    assert ended(hub, third, timeout=10)["status"] == "done"


def test_a_coordinator_goes_on_when_its_sub_runs_chat_is_deleted(hub, repo):  # noqa: F811
    """A coordinator waits for its sub-run, and the user deletes the sub-run's chat. The sub-run's end found no row
    to write into and fell before it told the coordinator, who waited for ever - a stop could not end it. Now the
    coordinator hears that its sub-run is gone, and goes on."""
    register(
        hub, ToolProvider([[delegate("Правщик", "Поправь")], [{"id": "w1", "name": "edit_file", "input": edit()}]])
    )
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    profile(hub, name="Правщик", tools=["edit_file"], project_ids=[pid])
    boss = profile(hub, name="Координатор", tools=["delegate_task"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]  # the sub-run waits for the user
    assert hub.delete(f"/v1/conversations/{asked['conversation_id']}").status_code == 204
    assert ended(hub, run_id, timeout=10)["status"] == "done"
    messages = hub.get(f"/v1/conversations/{run_id}").json()["messages"]
    said = [b for m in messages if m["role"] == "tool" for b in m["content"]]
    assert said[0]["error"], said[0]["output"]  # the coordinator heard its sub-run did not do it


# --- a message into a chat: taken at once; its files under names Windows takes (2026-11-10, the user's word)


def test_two_messages_at_once_into_one_chat_start_one_answer(served):
    """Two messages into one chat at the same moment - a second Enter while the first one's files go up, the window
    and VS Code together. `send` asked whether the chat was busy and took it a dozen awaits later: both passed, two
    answers ran in one chat, and the second's stop took the first's place - the first could not be stopped. Now the
    chat is taken at once, and the second is refused as busy."""
    served.app.state.gateway.register(Scripted("agent", ["Раз."]))
    assert served.http.post("/v1/models/sync").status_code == 200
    cid = served.http.post("/v1/conversations", json={}).json()["id"]
    chat = served.app.state.chat

    async def both():
        sending = (chat.send(cid, "раз", "agent/m"), chat.send(cid, "два", "agent/m"))
        return await asyncio.gather(*sending, return_exceptions=True)

    first, second = asyncio.run_coroutine_threadsafe(both(), served.server.servers[0].get_loop()).result(10)
    assert first["role"] == "user", first
    assert isinstance(second, GatewayError) and "already has a run" in str(second), second
    assert [m["role"] for m in served.wait_idle(cid)["messages"]] == ["user", "assistant"]


def test_a_message_whose_files_were_not_put_leaves_the_chat_free(live):
    """Claude Code reads a message's files from a folder of the chat. Putting them there failed - here a file stands
    where the folder goes; a full disk, a folder another program holds - after the chat was taken, and the chat stayed
    busy until the hub restarted: every next message was refused. Now the failure is said, and the chat is free."""
    cid = cc.chat_with_project(live)
    blocker = data_paths().root / "attachments" / cid
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a folder", encoding="utf-8")
    url = f"/v1/conversations/{cid}/messages"
    files = [{"name": "plan.txt", "data": b64(b"plan")}]
    failed = live.http.post(url, json={"content": "Посмотри", "model_id": "claude-code/default", "files": files})
    blocker.unlink()
    again = live.http.post(url, json={"content": "Ещё раз", "model_id": "claude-code/default"})
    assert again.status_code == 202, again.text  # the chat was not left busy
    assert failed.status_code == 409 and "Claude Code reads them" in failed.json()["detail"], failed.text
    live.wait_idle(cid)


def test_claude_code_gets_files_whose_names_windows_refuses(live):
    """A file sent from outside the window (VS Code, the API) may be named as Windows names no file - «a<b>?.txt»,
    «con.txt» - or two alike. Writing it for Claude Code failed, and the chat stayed busy; now each is written under a
    name Windows takes, one file each."""
    cid = cc.chat_with_project(live)
    names = ["a<b>?.txt", "con.txt", "отчёт.txt", "отчёт.txt"]
    files = [{"name": name, "data": b64(f"файл {n}".encode())} for n, name in enumerate(names)]
    sent = live.http.post(f"/v1/conversations/{cid}/messages",
                          json={"content": "Посмотри", "model_id": "claude-code/default", "files": files})  # fmt: skip
    assert sent.status_code == 202, sent.text
    live.wait_idle(cid)
    folder = data_paths().root / "attachments" / cid / str(sent.json()["user_message"]["id"])
    assert {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()} == {
        "a_b__.txt": "файл 0", "_con.txt": "файл 1", "отчёт.txt": "файл 2", "отчёт (2).txt": "файл 3"}  # fmt: skip


# --- an autonomous run stopped while paused (2026-11-11, the user's word)


def of_run(events, kind, run_id):
    return [e for e in of_kind(events, kind) if e["payload"].get("conversation_id") == run_id]


def test_an_autonomous_run_stopped_while_paused_tells_the_window_it_ended(served):
    """The user pauses an autonomous run, then stops it. The run heard the stop between its steps and left without
    `chat.done` or `chat.error` - the only events that end an answer for the window: the chat stayed «busy» there
    until the window reconnected, and Enter in it was a «Стоп» to nothing. Now the window hears that it ended."""
    agent = Scripted("agent", [[PLAN]])
    served.app.state.gateway.register(agent)
    assert served.http.post("/v1/models/sync").status_code == 200
    body = {"goal": "Посчитать строки в README", "done_when": "число строк названо", "model_id": "agent/m",
            "autonomy": "plan", "project_ids": []}  # fmt: skip
    token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
    with window_listening(served, token) as (events, _links):
        holder = served.http.post("/v1/runs/autonomous", json=body).json()["id"]
        served.wait_approval()  # its plan waits for the user: it holds the graphics card
        paused = served.http.post("/v1/runs/autonomous", json=body).json()["id"]
        wait(lambda: served.http.get(f"/v1/runs/{paused}").json()["status"] == "queued")
        assert served.http.post(f"/v1/runs/{paused}/pause").status_code == 202  # paused before its first step
        assert served.http.post(f"/v1/runs/{holder}/cancel").json() == {"cancelled": True}
        wait(lambda: of_run(events, "chat.run_started", paused))  # the card is its now: it waits, paused
        assert served.http.post(f"/v1/runs/{paused}/cancel").json() == {"cancelled": True}
        done = wait(lambda: of_run(events, "chat.done", paused), timeout=10)
    assert len(done) == 1 and done[0]["payload"]["cancelled"] is True, done
    wait(lambda: served.http.get(f"/v1/runs/{paused}").json()["status"] == "cancelled")
    assert len(agent.prompts) == 1  # only the holder asked the model: the paused run was stopped before it did


# --- the hub stops while answers go on (2026-11-11, the user's word)


def errors_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_claude_code_goal_cut_off_by_the_hub_stopping_goes_on_after_the_restart(live, caplog, monkeypatch):
    """The hub stops - closed from the window, or for an update - while Claude Code works a goal's turn. It killed
    the CLI and did not stop the turn: the turn failed («ended without an answer») and the goal went to «blocked»,
    or what it wrote came after the database had closed. Now the hub stops the turn as «Стоп» does, before the
    rest; the goal does not hear of it, and after the restart it goes on."""
    monkeypatch.setattr(claude_code, "_runnable", lambda path: cc.FAKE)  # the next hub finds the stand-in CLI too
    cid = cc.chat_with_project(live)
    cc.set_goal(live, cid, objective="hang on until the hub stops")
    wait(lambda: live.log.is_file() and len(live.log.read_text(encoding="utf-8").splitlines()) == 1)  # its turn
    with caplog.at_level(logging.ERROR):
        live.close()
    assert errors_in(caplog) == []
    after = cc.LiveHub()
    try:
        wait(lambda: len(live.log.read_text(encoding="utf-8").splitlines()) == 2, timeout=30)  # its next turn
        goal = after.http.get(f"/v1/conversations/{cid}/goal").json()["goal"]
        assert goal["status"] == "active", goal
        assert "The hub restarted" in json.loads(live.log.read_text(encoding="utf-8").splitlines()[1])["text"]
    finally:
        after.close()


def test_an_agents_run_cut_off_by_the_hub_stopping_is_left_to_go_on(served, caplog):
    """A run the hub was running when it stopped goes on after the restart (AgentRunner.start), so the hub's stop
    must not end it as the user's «Стоп» does. It never ended it only because its end came after the database had
    closed - with errors in the log. Now the hub stops its answer first and leaves the run as it was."""
    worker = Gated("agent", ["Готово."])
    worker.gate.clear()  # its answer waits
    served.app.state.gateway.register(worker)
    assert served.http.post("/v1/models/sync").status_code == 200
    body = {"name": "Ждун", "instructions": "Работаешь.", "model_id": "agent/m", "mode": "ask",
            "tools": ["read_file"], "project_ids": [], "max_steps": 10}  # fmt: skip
    made = served.http.post("/v1/agents", json=body).json()
    run_id = served.http.post(f"/v1/agents/{made['id']}/runs", json={"task": "долгая задача"}).json()["id"]
    assert worker.waiting.wait(10)  # its model is answering
    with caplog.at_level(logging.ERROR):
        served.close()
    assert errors_in(caplog) == []
    with closing(sqlite3.connect(data_paths().db)) as conn:
        row = conn.execute("SELECT status, ended_at FROM agent_runs WHERE conversation_id = ?", (run_id,)).fetchone()
    assert row == ("running", None)


def test_a_goal_checking_its_turn_while_the_hub_stops_is_not_blocked(served, repo):
    """A turn ends just before the hub stops, and its goal checks it meanwhile. The check found work left and
    started the next turn, which the stopping hub refused - and the goal went to «blocked», waiting for the human
    after the restart. Now a turn the stopping hub refuses leaves the goal at work: it goes on after the restart."""
    served.app.state.gateway.register(Scripted("agent", ["Ещё не готово."]))
    assert served.http.post("/v1/models/sync").status_code == 200
    pid = served.http.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    cid = served.http.post("/v1/conversations", json={}).json()["id"]
    assert served.http.put(f"/v1/conversations/{cid}/projects", json={"project_ids": [pid]}).status_code == 200
    body = {"objective": "Дождаться проверки", "model_id": "agent/m",
            "criteria": [{"command": "Start-Sleep -Seconds 5; exit 1"}]}  # fmt: skip
    assert served.http.put(f"/v1/conversations/{cid}/goal", json=body).status_code == 201
    wait(lambda: served.http.get(f"/v1/conversations/{cid}/goal").json()["checking"])  # its first turn is checked
    served.close()
    with closing(sqlite3.connect(data_paths().db)) as conn:
        status, detail = conn.execute("SELECT status, detail FROM goals WHERE conversation_id = ?", (cid,)).fetchone()
    assert status == "active", detail


# --- tuning by hand meets a chat (42.4) ---


def test_a_chat_that_begins_stops_tuning_by_hand_and_it_goes_on_after(hub, setup, spec_server, monkeypatch):  # noqa: F811
    """42.4 × the chat: «Подобрать» by hand measures guessing ahead with llama-server past llama-swap; a chat that
    begins on the hub meanwhile (its answer held at a gate) would load a second model on the card - the measuring
    stops, the server is let go, nothing is written by its numbers; once the answer is done the model is measured
    again and the pick written."""
    from tests.test_tuning import line, speeds, stand_ins

    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.2)  # stopped, it is background work (48.9б2)
    speeds(spec_server, {"none": 40.0, "ngram-mod": 44.0}, load_s=1.5)
    tuner = hub.app.state.tuner

    def bench(command, progress):  # llama-bench: the config's own settings are the fastest
        return [line(True, "q8_0", 512, 16, pp=900), line(True, "q8_0", 512, 16, tg=40)]

    async def loads(model_id):
        return {"ok": True, "error": "", "seconds": 1.0}

    monkeypatch.setattr(tuner, "_run", bench)
    monkeypatch.setattr(tuner, "_check", loads)
    worker = use(hub, Gated("agent", ["Ответ."]))
    worker.gate.clear()
    assert hub.post("/v1/tune/big").status_code == 200
    wait(lambda: (spec_server / "launches.txt").exists(), timeout=30)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "agent/m"})
    wait(lambda: hub.get("/v1/tune").json()["job"]["status"] == "waiting", timeout=30)
    assert "big" not in hub.get("/v1/tune").json()["results"] and not stand_ins()
    worker.gate.set()  # the answer goes on and ends
    spec = wait(lambda: hub.get("/v1/tune").json()["results"].get("big", {}).get("spec"), timeout=60)
    assert spec["status"] == "better" and spec["kind"] == "ngram"
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]["type"] == "ngram-mod"


# --- an install's trial meets a picture being drawn (47.1) ---


def test_an_installs_trial_picture_waits_while_the_hub_draws(installs, monkeypatch):  # noqa: F811
    """47.1 × the pictures: an install's trial picture loads its model into llama-swap, one model on the card - a
    picture the hub was drawing lost its model in the middle. Its files down, the install waits «ждёт пробного
    запуска» while pictures are drawn, as it waits for tuning and a chat's answer (34.4); its trial comes after."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    catalog["pictures"] = [DRAW, {**DRAW, "id": "best", "tier": "quality", "name": "Best"}]
    mi.CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    first = installs.post("/v1/install/pictures", json={"id": "draw"}).json()["job"]
    assert wait_for(installs, first["id"], "done", "failed")["status"] == "done"

    FakeLlamaSwap.held = held = threading.Event()
    try:
        picture = installs.post("/v1/visual/images", json={"prompt": "a fox", "model": "draw"}).json()["job"]
        wait(lambda: any(b.get("prompt") == "a fox" for b in FakeLlamaSwap.asked), timeout=10)  # being drawn
        job = installs.post("/v1/install/pictures", json={"id": "best"}).json()["job"]
        waiting = wait_for(installs, job["id"], "trialwait", "done", "failed")
        assert waiting["status"] == "trialwait", "its trial loaded «best» in the middle of the picture"
        assert waiting["percent"] == 100.0  # its files are down
        time.sleep(0.4)
        assert wait_for(installs, job["id"], "trialwait")
        assert "best" not in [b.get("model") for b in FakeLlamaSwap.asked]  # nothing loaded meanwhile
    finally:
        held.set()
    done = wait_for(installs, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    drawn = installs.get(f"/v1/visual/jobs/{picture['id']}").json()["job"]
    assert drawn["status"] == "done", drawn["error"]
    asked = [b.get("prompt") == "a fox" or b.get("model") for b in FakeLlamaSwap.asked]
    assert asked.index(True) < asked.index("best")  # the picture first, then the trial


def test_the_studio_stops_an_installs_trial_video_and_it_films_after_quiet(installs, monkeypatch):  # noqa: F811
    """Stage 48's review, finding 2: a Studio's video asked for while an install's trial wrote its config went at once
    and loaded its model into llama-swap in place of the trial's («пробное видео не вышло», the block withdrawn). The
    trial holds the card from its checks on: the config is written whole while the Studio's video waits «в очереди».
    48.9в: then the Studio - the person's, before a trial - stops the trial instead of waiting for it to end: back to
    «ждёт пробного запуска», nothing failed; the Studio films, and the trial once the card has been still."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.5)
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    catalog["pictures"] = [DRAW, CLIP, {**CLIP, "id": "clip2", "name": "Clip two"}]
    mi.CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
    installer = installs.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    first = installs.post("/v1/install/pictures", json={"id": "clip"}).json()["job"]
    assert wait_for(installs, first["id"], "done", "failed")["status"] == "done"
    monkeypatch.setattr(FakeLlamaSwap, "video", [])

    entered, writing = threading.Event(), threading.Event()
    configure = installer._configure_pictures

    async def held(found):  # the trial's config being written - as long as the test says
        entered.set()
        await asyncio.to_thread(writing.wait, 30.0)
        return await configure(found)

    def studio(job_id):
        return installs.get(f"/v1/visual/jobs/{job_id}").json()["job"]["status"]

    monkeypatch.setattr(installer, "_configure_pictures", held)
    FakeLlamaSwap.filming = filming = threading.Event()
    try:
        job = installs.post("/v1/install/pictures", json={"id": "clip2"}).json()["job"]
        assert entered.wait(30.0)
        video = installs.post("/v1/visual/videos", json={"prompt": "a fox", "model": "clip"}).json()["job"]
        time.sleep(0.5)
        assert studio(video["id"]) == "queued", "the Studio's video went while the trial's config was written"
        writing.set()
        waiting = wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=20)
        assert waiting["status"] == "trialwait" and not waiting["error"], "the trial went on before the Studio's video"
        wait(lambda: [v["path"] for v in FakeLlamaSwap.video] == ["/upstream/clip/sdcpp/v1/vid_gen"], timeout=20)
    finally:
        writing.set()
        filming.set()
    wait(lambda: studio(video["id"]) in ("done", "failed"), timeout=20)
    assert studio(video["id"]) == "done"
    assert wait_for(installs, job["id"], "done", "failed")["status"] == "done"
    paths = [v["path"] for v in FakeLlamaSwap.video]
    assert paths == ["/upstream/clip/sdcpp/v1/vid_gen", "/upstream/clip2/sdcpp/v1/vid_gen"]  # the Studio, the trial


def _chats(asked):
    return [b for b in asked if "messages" in b]


def test_a_chat_stops_an_installs_trial_video_and_the_trial_waits_for_a_chat(installs, monkeypatch):  # noqa: F811
    """Stage 48's review, finding 2б: a local chat's answer during a trial video went to llama-swap at once and swapped
    the trial's model out. The trial waits for a chat answering. 48.9в: a chat that begins while the trial films - the
    person's, before a trial - stops it: the engine's job cancelled, «ждёт пробного запуска», nothing failed; the chat
    waits only for that, with words, and answers before the trial films again - once the card has been still. And never
    the two waiting for each other."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 1.0)
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DRAW, CLIP]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(FakeLlamaSwap, "video", [])
    seen, publish = [], installs.app.state.bus.publish

    async def recorded(kind, payload=None, **kw):
        seen.append((kind, payload))
        return await publish(kind, payload, **kw)

    monkeypatch.setattr(installs.app.state.bus, "publish", recorded)

    def say(text):
        conv = installs.post("/v1/conversations", json={}).json()["id"]
        body = {"content": text, "model_id": "llama-swap/fast"}
        sent = installs.post(f"/v1/conversations/{conv}/messages", json=body)
        assert sent.status_code == 202, sent.text
        return conv

    def answered(conv):
        messages = installs.get(f"/v1/conversations/{conv}").json()["messages"]
        return any(m["role"] == "assistant" and "на связи" in json.dumps(m["content"], ensure_ascii=False)
                   for m in messages)  # fmt: skip

    FakeLlamaSwap.answering = answering = threading.Event()
    FakeLlamaSwap.filming = filming = threading.Event()
    try:
        first = say("привет")
        wait(lambda: len(_chats(FakeLlamaSwap.asked)) == 1, timeout=10)  # its answer comes: held at llama-swap
        job = installs.post("/v1/install/pictures", json={"id": "clip"}).json()["job"]
        assert wait_for(installs, job["id"], "trialwait", "trial", "done", "failed")["status"] == "trialwait"
        assert not FakeLlamaSwap.video  # the trial waits for the chat's answer
        answering.set()
        wait(lambda: answered(first), timeout=20)
        wait(lambda: FakeLlamaSwap.video, timeout=20)  # the trial films
        second = say("ещё раз")
        waiting = wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=10)
        assert waiting["status"] == "trialwait" and not waiting["error"], "the chat waited for the whole trial"
        wait(lambda: answered(second), timeout=20)  # before the trial films again
        assert len(FakeLlamaSwap.video) == 1 and len(_chats(FakeLlamaSwap.asked)) == 2
        loading = [p["text"] for k, p in seen if k == "chat.loading" and p["conversation_id"] == second]
        assert loading and "пробный запуск" in loading[0]
    finally:
        answering.set()
        filming.set()
    assert wait_for(installs, job["id"], "done", "failed")["status"] == "done"
    assert len(FakeLlamaSwap.video) == 2  # filmed again


def test_a_chat_models_trial_does_not_wait_for_itself(installs):  # noqa: F811
    """Stage 48's review, finding 2: a chat model's trial holds the card from the Studio, not from local chats - its own
    request goes through the llama-swap provider's gate (run_check) and would wait for itself. Within a time limit."""
    job = installs.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False,
                                             "drive": "T"}).json()["job"]  # fmt: skip
    started = time.monotonic()
    done = wait_for(installs, job["id"], "done", "failed", timeout=20.0)
    assert done["status"] == "done", done["error"]
    assert time.monotonic() - started < 20.0 and done["trial"]["tries"][-1]["ok"]  # its request through the gate
    assert not installs.app.state.visual.holding and not installs.app.state.card.holder


def test_a_chat_stops_a_chat_models_trial_and_it_waits_for_quiet(installs, monkeypatch):  # noqa: F811
    """48.9в (variant А, the user's word): a chat to another local model during a chat model's trial waited in
    llama-swap's queue for the trial's answer - or the trial for the chat's: its time swollen, past 300 s «не
    запустилась» and smaller settings for a sound model. By seniority the chat stops the trial: its question let go,
    back to «ждёт пробного запуска», no try written, its settings as they were. It goes on once the card has been
    still - not as soon as the chat ends, or the models go to and fro on each message."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 1.5)
    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
        job = installs.post("/v1/install", json=spec).json()["job"]
        wait(lambda: any(b.get("model") == "tiny-q4_k_m" for b in FakeLlamaSwap.asked), timeout=20)  # its question
        conv = installs.post("/v1/conversations", json={}).json()["id"]
        body = {"content": "привет", "model_id": "llama-swap/fast"}
        assert installs.post(f"/v1/conversations/{conv}/messages", json=body).status_code == 202
        waiting = wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=10)
        assert waiting["status"] == "trialwait" and not waiting["error"], "the trial went on under the chat"
        assert not (waiting.get("trial") or {}).get("tries")  # nothing of it written
    finally:
        answering.set()
    wait(lambda: _answered(installs, conv) and not installs.app.state.card.asked, timeout=20)
    time.sleep(0.7)
    assert wait_for(installs, job["id"], "trialwait"), "the trial came back as soon as the chat ended"
    done = wait_for(installs, job["id"], "done", "failed", timeout=20)
    assert done["status"] == "done", done["error"]
    tries = done["trial"]["tries"]
    assert len(tries) == 1 and tries[0]["ok"] and tries[0]["context"] == waiting["plan"]["context"]  # not smaller


# --- one owner of the graphics card (48.9) ---


def _meanwhile(call):
    """`call` in a thread of its own - a request held at llama-swap; its answer, once it comes, in the list."""
    answered: list = []
    threading.Thread(target=lambda: answered.append(call()), daemon=True).start()
    return answered


def _cloud_chat(hub):  # noqa: F811
    """A cloud model's answer under way, held at its gate."""
    cloud = use(hub, Cloudy("sky", ["Облако отвечает."]))
    cloud.gate.clear()
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    sent = hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "sky/m"})
    assert sent.status_code == 202, sent.text
    wait(cloud.waiting.is_set)
    return cloud


def test_tuning_waits_for_any_local_request_and_not_for_a_cloud_chat(installs, monkeypatch):  # noqa: F811
    """48.9а: tuning waited for `chat.busy` - the chats going on, cloud ones too, and nothing else. An outside client's
    request through /v1/openai to a local model was not seen (nor the editor's completion, nor «Проверить»): tuning
    unloaded the models and measured beside it. A cloud chat, which does not touch the card, kept it waiting. The
    llama-swap provider's gate counts the local requests now, with who asks; tuning waits for them, not the cloud."""
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    tuner, card = installs.app.state.tuner, installs.app.state.card
    measured = []

    async def tune(model_id, *, automatic=False):  # what it measures is not this test's
        measured.append(model_id)
        return {"status": "same"}

    monkeypatch.setattr(tuner, "tune", tune)
    cloud = _cloud_chat(installs)
    try:
        installs.portal.call(tuner.request, "fast")
        wait(lambda: measured == ["fast"], timeout=5)  # a cloud chat is not on the card
    finally:
        cloud.gate.set()

    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        body = {"model": "llama-swap/fast", "messages": [{"role": "user", "content": "привет"}]}
        answer = _meanwhile(lambda: installs.post("/v1/openai/chat/completions", json=body))
        wait(lambda: len(_chats(FakeLlamaSwap.asked)) == 1, timeout=10)  # held at llama-swap
        assert card.asked == {"openai": 1}
        installs.portal.call(tuner.request, "fast")
        time.sleep(0.5)
        assert measured == ["fast"] and tuner.job["status"] == "waiting", "tuning measured beside a local request"
    finally:
        answering.set()
    wait(lambda: answer and measured == ["fast", "fast"], timeout=10)
    assert answer[0].status_code == 200 and not card.asked


def test_an_installs_trial_waits_for_the_editors_completion_and_not_for_a_cloud_chat(installs, monkeypatch):  # noqa: F811
    """48.9а: an install's trial waited for the chats going on: a cloud chat kept it «ждёт пробного запуска» for
    nothing, and the editor's completion - fill in the middle through llama-swap's /upstream, past the gate - was not
    seen: the trial swapped its model out under it. The completion counts on the card as a whole, its trial after it."""
    cloud = _cloud_chat(installs)
    try:
        spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
        first = installs.post("/v1/install", json=spec).json()["job"]
        done = wait_for(installs, first["id"], "done", "failed", timeout=15.0)
        assert done["status"] == "done", done["error"]
    finally:
        cloud.gate.set()

    folder = installs.tmp / "project"
    folder.mkdir()
    pid = installs.post("/v1/projects", json={"path": str(folder)}).json()["id"]
    roles = {"model_id": "", "settings": {"roles": {"complete": "llama-swap/fast"}}}
    assert installs.put("/v1/model_settings", json=roles).status_code == 200
    monkeypatch.setattr(completion, "DEADLINE_S", 30.0)  # held longer than its 1.5 s: the test holds it
    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        around = {"project_id": pid, "path": "app.py", "before": "def total(items):\n    return ", "after": "\n",
                  "load": True}  # fmt: skip
        completed = _meanwhile(lambda: installs.post("/v1/editor/complete", json=around))
        wait(lambda: any("input_prefix" in b for b in FakeLlamaSwap.asked), timeout=10)  # its /infill, held
        assert installs.app.state.card.asked == {"completion": 1}
        job = installs.post("/v1/install", json={**spec, "quant": "Q8_0"}).json()["job"]
        waiting = wait_for(installs, job["id"], "trialwait", "trial", "done", "failed", timeout=15.0)
        assert waiting["status"] == "trialwait", "its trial loaded the model under the editor's completion"
        time.sleep(0.4)
        assert wait_for(installs, job["id"], "trialwait")
        assert "tiny-q8_0" not in [b.get("model") for b in FakeLlamaSwap.asked]
    finally:
        answering.set()
    assert wait_for(installs, job["id"], "done", "failed")["status"] == "done"
    wait(lambda: completed, timeout=10)
    assert completed[0].status_code == 200 and not installs.app.state.card.asked


def _measuring(tuner, monkeypatch):
    """Tuning's measuring of «fast» as a stand-in: the first time until something stops it - `halted`, what had reached
    llama-swap by then -, then at once; `at` - when each began. Another model (an install's automatic tuning) - at
    once."""
    seen: dict = {"halted": None, "runs": 0, "at": []}

    async def measure(model_id):
        if model_id != "fast":
            return {"status": "same"}
        seen["runs"] += 1
        seen["at"].append(time.monotonic())
        deadline = time.monotonic() + 15
        while seen["runs"] == 1 and not tuner._halt.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if seen["runs"] == 1 and tuner._halt.is_set():
            seen["halted"] = list(FakeLlamaSwap.asked)
            raise tuning._Halted
        return {"status": "same"}

    monkeypatch.setattr(tuner, "_tune", measure)
    return seen


def _answered(hub, conv):  # noqa: F811
    messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
    return any(m["role"] == "assistant" and "на связи" in json.dumps(m["content"], ensure_ascii=False)
               for m in messages)  # fmt: skip


def test_a_local_request_stops_tuning_and_goes_once_it_lets_the_card_go(installs, monkeypatch):  # noqa: F811
    """48.9б1: tuning measured past the card - a chat that began meanwhile went to llama-swap at once and loaded its
    model beside llama-bench until tuning saw it (`_watch`, every WAIT_S) and let go. Tuning holds the card now
    (TUNE), from local requests too; a request - senior to it - stops it at the gate and goes once the card is let
    go, with words meanwhile. Not a result: the model is measured again after the chat."""
    monkeypatch.setattr(tuning, "WAIT_S", 0.5)
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.2)  # stopped, it is background work (48.9б2)
    tuner = installs.app.state.tuner
    seen = _measuring(tuner, monkeypatch)
    installs.portal.call(tuner.request, "fast")
    wait(lambda: tuner.job.get("status") == "measuring", timeout=10)
    conv = installs.post("/v1/conversations", json={}).json()["id"]
    body = {"content": "привет", "model_id": "llama-swap/fast"}
    assert installs.post(f"/v1/conversations/{conv}/messages", json=body).status_code == 202
    wait(lambda: _answered(installs, conv), timeout=20)
    stopped = seen["halted"]
    assert stopped is not None and not _chats(stopped), "the chat's request went to llama-swap while tuning measured"
    wait(lambda: seen["runs"] == 2 and "fast" in tuner.results, timeout=20)  # measured again after it
    # the card let go once the tuning ends (its result is kept a moment before: the store, the window's news)
    wait(lambda: not installs.app.state.card.holder, timeout=5)


def test_the_studio_stops_tuning_and_tuning_waits_for_the_studio(installs, monkeypatch):  # noqa: F811
    """48.9б1 (finding 3): tuning did not know the Studio - while a picture was drawn the card looked quiet to it, and
    llama-bench measured beside sd-server, unloading every model; a picture asked for while it measured went at once.
    Tuning waits while the Studio holds the card; the Studio's job, senior, stops it and draws once it lets go."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DRAW]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    first = installs.post("/v1/install/pictures", json={"id": "draw"}).json()["job"]
    assert wait_for(installs, first["id"], "done", "failed")["status"] == "done"
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.1)  # the install's automatic tuning of «draw» goes by
    tuner = installs.app.state.tuner
    seen = _measuring(tuner, monkeypatch)

    def drawn(job):
        return installs.get(f"/v1/visual/jobs/{job['id']}").json()["job"]["status"]

    FakeLlamaSwap.held = held = threading.Event()
    try:
        fox = installs.post("/v1/visual/images", json={"prompt": "a fox", "model": "draw"}).json()["job"]
        wait(lambda: any(b.get("prompt") == "a fox" for b in FakeLlamaSwap.asked), timeout=10)  # being drawn
        installs.portal.call(tuner.request, "fast")
        time.sleep(0.5)
        assert seen["runs"] == 0 and tuner.job["status"] == "waiting", "tuning measured while the Studio drew"
    finally:
        held.set()
    wait(lambda: drawn(fox) == "done", timeout=10)
    wait(lambda: seen["runs"] == 1, timeout=10)  # after the picture
    cat = installs.post("/v1/visual/images", json={"prompt": "a cat", "model": "draw"}).json()["job"]
    wait(lambda: seen["halted"] is not None, timeout=10)
    assert not any(b.get("prompt") == "a cat" for b in seen["halted"]), "the Studio drew beside llama-bench"
    wait(lambda: drawn(cat) == "done", timeout=10)
    wait(lambda: seen["runs"] == 2 and "fast" in tuner.results, timeout=20)  # measured again after it


def test_tuning_a_chat_stopped_goes_on_after_quiet_not_at_once(installs, monkeypatch):  # noqa: F811
    """48.9б2: «Подобрать» stopped by a chat went back at once when the answer ended - unloading the chat's model
    before the person's next message, which loaded it again: models to and fro on each message (the user's word).
    Stopped, it is background work: it goes on once the card has been still for QUIET_S."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 1.5)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    tuner = installs.app.state.tuner
    seen = _measuring(tuner, monkeypatch)
    installs.portal.call(tuner.request, "fast")
    wait(lambda: seen["runs"] == 1, timeout=10)  # by hand: at once
    conv = installs.post("/v1/conversations", json={}).json()["id"]
    body = {"content": "привет", "model_id": "llama-swap/fast"}
    assert installs.post(f"/v1/conversations/{conv}/messages", json=body).status_code == 202
    wait(lambda: _answered(installs, conv) and not installs.app.state.card.asked, timeout=20)
    ended = time.monotonic()
    wait(lambda: seen["runs"] == 2, timeout=10)
    assert seen["at"][1] - ended >= 1.0, "stopped tuning went back at once after the chat"


def test_background_tuning_waits_for_a_game_and_not_for_a_download(installs, monkeypatch):  # noqa: F811
    """48.9б2: the quiet tuning waited for was the card's load and the installer's work. A game in its menu or paused
    loads the card little - tuning measured beside it; a download does not touch the card - tuning waited for it all
    the same. A game on the card (43.6 knows them) keeps background work waiting, a download does not."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    tuner, installer, state = installs.app.state.tuner, installs.app.state.installer, installs.app.state
    measured = []

    async def tune(model_id, *, automatic=False):  # what it measures is not this test's
        measured.append((model_id, installer.busy()))
        return {"status": "same"}

    monkeypatch.setattr(tuner, "tune", tune)
    state.status.latest = {**state.status.latest, "gpu": {**state.status.latest["gpu"], "util_pct": 3}}
    game = {
        "exe": r"C:\Games\Quest\quest.exe",
        "name": "quest.exe",
        "mib": 900.0,
        "where": "kept",
        "why": "game",
        "playing": True,
    }  # a game for the quiet (48.9б3): one 43.6 knows - in its menu now
    state.gpu.state = {**state.gpu.state, "programs": [game]}
    installs.portal.call(tuner.request, "fast", True)
    time.sleep(1.0)
    assert not measured and tuner.job["status"] == "waiting", "tuning measured beside a game"
    FakeHub.slow = 0.7  # a download goes on - seconds
    spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
    job = installs.post("/v1/install", json=spec).json()["job"]
    wait_for(installs, job["id"], "downloading")
    state.gpu.state = {**state.gpu.state, "programs": []}  # the game closed
    wait(lambda: measured, timeout=10)
    assert measured[0] == ("fast", True), "tuning waited for a download"
    FakeHub.slow = 0.0
    assert wait_for(installs, job["id"], "done", "failed")["status"] == "done"


def _on_card(exe: str, mib: float, busy_3d: float = 0.0) -> dict:
    return {"exe": exe, "name": exe.rsplit("\\", 1)[-1], "pids": [1], "mib": mib, "busy_3d": busy_3d}


# what 43.6 saw on the user's computer before 48.9е (2026-11-15): Epic's launcher, its helper and overlay - «game» by
# the folder's name, a few MiB, drawing nothing
EPIC = [_on_card(r"C:\Program Files (x86)\Epic Games\Epic Online Services\managedArtifacts\98bc04bc842e4906993fd6d6644f"
                 r"fb8d\EOSOverlayRenderer-Win64-Shipping.exe", 14.1),
        _on_card(r"C:\Program Files\Epic Games\Launcher\Engine\Binaries\Win64\EpicWebHelper.exe", 13.0),
        _on_card(r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
                 35.2)]  # fmt: skip


def test_a_launcher_does_not_break_the_quiet_a_known_game_paused_does(installs, monkeypatch):  # noqa: F811
    """48.9б3 (found before 48.9е): the quiet took any program 43.6 keeps on the card as a game for a game on the card -
    Epic's launcher, its helper and overlay are «game» by their folder («Epic Games») and drew nothing, and background
    work never went while the launcher was open, «Продолжить» neither. A game for the quiet is one Windows' Game Bar
    knows, or 43.6 remembers drawing - in its menu, paused, it is still one - or any program drawing 3D at BUSY_3D now;
    never AFO's own (its engines) nor Windows' compositor. 43.6's keeping a program on the card stays as it was."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    state = installs.app.state
    tuner, watcher = state.tuner, state.gpu
    measured = []

    async def tune(model_id, *, automatic=False):  # what it measures is not this test's; it ends as tuning does
        measured.append(model_id)
        tuner.job = {"status": "idle"}
        return {"status": "same"}

    monkeypatch.setattr(tuner, "tune", tune)
    seen: list = []
    windows_games: set = set()
    two = {
        "two": True,
        "power_saving": {"luid": "i", "name": "Intel"},
        "high_performance": {"luid": "n", "vendor": "nvidia"},
    }
    monkeypatch.setattr(watcher, "_choice", lambda: two)
    monkeypatch.setattr(watcher, "_adapters", lambda: [{"luid": "n", "vendor": "nvidia"}, {"luid": "i"}])
    monkeypatch.setattr(watcher, "_look", lambda luids: list(seen))
    monkeypatch.setattr(watcher, "_facts", lambda: {"games": set(windows_games), "game_folders": [],
                                                    "windows": "c:\\windows\\", "own": []})  # fmt: skip
    monkeypatch.setattr(watcher, "_own", lambda: {1})  # the programs here are AFO's own processes (after 45.5)

    def on_card(*programs) -> None:
        seen[:] = programs
        installs.portal.call(watcher.check)

    def tuned(model_id: str) -> bool:
        measured.clear()
        installs.portal.call(tuner.request, model_id, True)
        time.sleep(1.0)
        return bool(measured)

    on_card(*EPIC)
    assert {p["why"] for p in watcher.state["programs"]} == {"game"}  # 43.6 keeps them on the card, as before
    assert tuned("fast"), "Epic's launcher, drawing nothing, kept background work waiting"
    on_card(
        _on_card(r"C:\Riot Games\Riot Client\RiotClientServices.exe", 22.0),
        _on_card(r"C:\Program Files\Rockstar Games\Launcher\Launcher.exe", 41.0),
        _on_card(r"C:\Windows\System32\dwm.exe", 1130.0, busy_3d=40.0),  # Windows' compositor
        _on_card(r"D:\AI model\llama.cpp\b11899\llama-server.exe", 9000.0, busy_3d=80.0),  # AFO's own engine
    )
    assert tuned("big"), "another launcher, the compositor or AFO's engine kept background work waiting"

    hades = r"D:\SteamLibrary\steamapps\common\Hades II\Ship\Hades2.exe"
    windows_games.add(hades.lower())  # Game Bar knows it
    on_card(_on_card(hades, 2100.0))  # paused: drawing nothing
    assert not tuned("fast"), "a game Windows knows, paused, did not keep background work waiting"
    on_card()
    wait(lambda: measured == ["fast"], timeout=10)

    quest = r"E:\Games\Quest\quest.exe"
    installs.app.state.db.call(lambda conn: gpu_prefs._put(conn, gpu_prefs.GAMES_SETTING, [quest.lower()]))
    on_card(_on_card(quest, 900.0))  # 43.6 saw it drawing once; in its menu now
    assert not tuned("big"), "a game 43.6 remembers, in its menu, did not keep background work waiting"
    on_card(_on_card(r"C:\Tools\new-game.exe", 1500.0, busy_3d=60.0))  # one nobody knows yet, drawing now
    time.sleep(1.0)
    assert not measured, "a program drawing 3D now did not keep background work waiting"
    on_card()
    wait(lambda: measured == ["big"], timeout=10)


def test_another_program_s_model_keeps_background_work_waiting_as_a_game_does(installs, monkeypatch):  # noqa: F811
    """Found at 45.5 (the stand; the user's word after it - «Ждать, как игру»): another program's model on the card -
    the person's own AFO on 8080 with gemma, 15,8 GB - was not seen by the card's queue: not a game, and its
    llama-server is named as AFO's engine. Strata's trial went beside it, Windows pushed gemma out and the answer
    broke off. Now a program that works on the card and is not AFO's own, holding 2 GB or more, keeps background work
    waiting as a game does - when the card says so too: the counters of the desktop say more than it holds (the
    user's card, nothing loaded: dwm 7,1 GB by them, 1,5 GB by the card). AFO's own engine, its name the same, keeps
    nothing."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    state = installs.app.state
    tuner, watcher = state.tuner, state.gpu
    measured = []

    async def tune(model_id, *, automatic=False):  # what it measures is not this test's
        measured.append(model_id)
        tuner.job = {"status": "idle"}
        return {"status": "same"}

    monkeypatch.setattr(tuner, "tune", tune)
    seen: list = []
    one = {"two": False, "power_saving": {"luid": "n"}, "high_performance": {"luid": "n", "vendor": "nvidia"}}
    monkeypatch.setattr(watcher, "_choice", lambda: one)  # one card, no integrated graphics beside it
    monkeypatch.setattr(watcher, "_adapters", lambda: [{"luid": "n", "vendor": "nvidia", "dedicated_mib": 16303}])
    monkeypatch.setattr(watcher, "_look", lambda luids: list(seen))
    monkeypatch.setattr(watcher, "_facts", lambda: {"games": set(), "game_folders": [], "windows": "c:\\windows\\",
                                                    "own": []})  # fmt: skip
    monkeypatch.setattr(watcher, "_own", lambda: {7})  # AFO's own process: pid 7

    def on_card(used_mib: int, *programs) -> None:
        state.status.latest = {**state.status.latest, "gpu": {**state.status.latest["gpu"], "vram_used_mib": used_mib}}
        seen[:] = programs
        installs.portal.call(watcher.check)

    def tuned(model_id: str) -> bool:
        measured.clear()
        installs.portal.call(tuner.request, model_id, True)
        time.sleep(1.0)
        return bool(measured)

    theirs = {**_on_card(r"C:\Users\p\AppData\Local\Programs\AFO\engines\llama.cpp\llama-server.exe", 16180.0),
              "pids": [8]}  # fmt: skip
    desktop = [_on_card(r"C:\Windows\System32\dwm.exe", 7095.0),
               _on_card(r"C:\Program Files\NVIDIA Corporation\NVIDIA App\CEF\NVIDIA Overlay.exe", 3568.0)]  # fmt: skip
    on_card(16100, theirs, *desktop)
    assert not tuned("fast"), "tuning measured beside another program's model"
    assert state.card.other() == {"kind": "program", "name": "llama-server", "gib": 15.8}
    assert tuner.job["card"] == state.card.other(), tuner.job  # the window names it (after 45.5)
    on_card(1542, *desktop)  # its model unloaded; the desktop's counters as they were on the user's card
    wait(lambda: measured == ["fast"], timeout=10)
    assert state.card.other() is None

    ours = {**_on_card(r"D:\AFO\engines\llama.cpp\llama-server.exe", 15000.0), "pids": [7]}
    on_card(16600, ours, *desktop)
    assert state.card.other() is None and tuned("big"), "AFO's own model kept background work waiting"
    comfy = {**_on_card(r"C:\ComfyUI\python_embeded\python.exe", 9000.0), "pids": [9]}
    on_card(10500, comfy, *desktop)
    assert state.card.other() == {"kind": "program", "name": "python", "gib": 8.8}
    on_card(1500, comfy, *desktop)  # its counter says 9 GB, the card 1,5: nothing of it there
    assert state.card.other() is None


def test_a_game_that_takes_the_card_during_a_trial_leaves_no_verdict(installs, monkeypatch):  # noqa: F811
    """48.9б4: a game started during an install's trial (the quiet is looked at before it starts) took the card's
    memory - the model did not start, and the trial wrote it smaller settings, then «не запустилась» and took it out of
    the config: a verdict on a sound model, by the game. The trial does not give way to the game, but what it found
    beside it is not kept: no try written, its settings as they were, «ждёт пробного запуска» again as background
    work - tried once the card has been still for QUIET_S, the game gone."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.5)
    state = installs.app.state
    game = {"exe": r"E:\Games\Quest\quest.exe", "name": "quest.exe", "mib": 9000.0, "where": "card", "why": "",
            "playing": True}  # fmt: skip
    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
        job = installs.post("/v1/install", json=spec).json()["job"]
        wait(lambda: any(b.get("model") == "tiny-q4_k_m" for b in FakeLlamaSwap.asked), timeout=20)  # its question
        planned = lc.model_view(lc.config_path().read_text(encoding="utf-8"), "tiny-q4_k_m")["context"]
        state.gpu.state = {**state.gpu.state, "programs": [game]}  # a game begins and takes the card's memory
        FakeLlamaSwap.logs["tiny-q4_k_m"] = "cudaMalloc failed: out of memory\n"
        FakeLlamaSwap.fail = True  # the model does not start
    finally:
        answering.set()
    waiting = wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=20)
    assert waiting["status"] == "trialwait", ("a verdict beside a game", waiting["status"], waiting["error"])
    assert waiting["error"] in ("", "ждёт: на видеокарте игра quest")  # not failed: waiting, and why (48.9б5)
    assert not (waiting.get("trial") or {}).get("tries")  # nothing of it written
    text = lc.config_path().read_text(encoding="utf-8")
    assert "tiny-q4_k_m" in lc.model_blocks(text) and lc.model_view(text, "tiny-q4_k_m")["context"] == planned
    FakeLlamaSwap.fail = False
    time.sleep(1.2)
    again = wait_for(installs, job["id"], "trialwait")  # not tried again beside the game
    assert again["error"] == "ждёт: на видеокарте игра quest", again["error"]
    state.gpu.state = {**state.gpu.state, "programs": []}  # the game closed
    done = wait_for(installs, job["id"], "done", "failed", timeout=20)
    assert done["status"] == "done", done["error"]
    tries = done["trial"]["tries"]
    assert len(tries) == 1 and tries[0]["ok"] and tries[0]["context"] == planned  # not smaller


def test_a_trial_by_the_button_with_a_game_on_the_card_loads_nothing(installs, monkeypatch):  # noqa: F811
    """48.9б5: «Пробный запуск» by the person's button did not look at the quiet - with a game on the card the model
    was loaded beside it and asked its question, and the answer thrown away after (48.9б4): the game hindered, the
    work for nothing. The trial looks for a game before the model loads: with one, nothing is loaded, the row says
    why, and the trial waits for the card still - then it goes."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.5)
    state = installs.app.state
    model = installs.tmp / "E" / "LLM" / "Mine-Q8_0.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(tiny_gguf(340_000))
    job = installs.post("/v1/install/local", json={"path": str(model), "name": "Mine-Q8_0"}).json()["job"]
    added = wait_for(installs, job["id"], "added", "failed")
    assert added["status"] == "added", added["error"]
    game = {"exe": r"E:\Games\Quest\quest.exe", "name": "quest.exe", "mib": 900.0, "where": "kept", "why": "game",
            "playing": True}  # fmt: skip
    state.gpu.state = {**state.gpu.state, "programs": [game]}  # a game on the card - in its menu
    assert installs.post(f"/v1/install/jobs/{job['id']}/trial").status_code == 200
    time.sleep(1.0)
    asked = [b for b in FakeLlamaSwap.asked if b.get("model") == added["model_id"]]
    assert not asked, "the model was loaded beside the game"
    waiting = wait_for(installs, job["id"], "trialwait")
    assert waiting["error"] == "ждёт: на видеокарте игра quest", waiting["error"]
    state.gpu.state = {**state.gpu.state, "programs": []}  # the game closed
    done = wait_for(installs, job["id"], "done", "failed", timeout=20)
    assert done["status"] == "done" and not done["error"], done["error"]


def test_a_trial_waits_while_another_program_s_model_holds_the_card_and_names_it(installs, monkeypatch):  # noqa: F811
    """After 45.5 (the user's word «Ждать, как игру»): Strata's trial went beside the person's own AFO with gemma on
    the card (15,8 GB), Windows pushed one of them out and the answer broke off. With another program's model on the
    card a trial - by the person's button too - loads nothing, says why by name and what it holds, and goes once the
    card is still: that model unloaded."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.5)
    state = installs.app.state
    model = installs.tmp / "E" / "LLM" / "Mine-Q8_0.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(tiny_gguf(340_000))
    job = installs.post("/v1/install/local", json={"path": str(model), "name": "Mine-Q8_0"}).json()["job"]
    added = wait_for(installs, job["id"], "added", "failed")
    assert added["status"] == "added", added["error"]
    theirs = {"exe": r"C:\Users\p\AppData\Local\Programs\AFO\engines\llama.cpp\llama-server.exe",
              "name": "llama-server.exe", "mib": 16180.0, "where": "kept", "why": "tool", "playing": False,
              "afo_mib": 0.0, "other_mib": 16180.0}  # fmt: skip
    state.status.latest = {**state.status.latest, "gpu": {**state.status.latest["gpu"], "vram_used_mib": 16100}}
    state.gpu.state = {**state.gpu.state, "programs": [theirs]}  # the person's own AFO holds gemma
    assert installs.post(f"/v1/install/jobs/{job['id']}/trial").status_code == 200
    time.sleep(1.0)
    asked = [b for b in FakeLlamaSwap.asked if b.get("model") == added["model_id"]]
    assert not asked, "the model was loaded beside another program's model"
    waiting = wait_for(installs, job["id"], "trialwait")
    assert waiting["error"] == "ждёт: видеокарту занимает llama-server — 15,8 ГБ", waiting["error"]
    state.status.latest = {**state.status.latest, "gpu": {**state.status.latest["gpu"], "vram_used_mib": 1500}}
    state.gpu.state = {**state.gpu.state, "programs": []}  # its model unloaded
    done = wait_for(installs, job["id"], "done", "failed", timeout=20)
    assert done["status"] == "done" and not done["error"], done["error"]


def test_unload_stops_the_studio_as_by_x_and_its_queue_waits_for_the_person(installs, monkeypatch):  # noqa: F811
    """48.9г (finding 4): «Выгрузить модель» unloaded everything telling no one - the Studio's video looked at its job
    through /upstream and loaded the model again a second later; a picture lost its model and stepped a rung lower,
    remembered as the model's place. «Выгрузить» goes through the card's owner: the Studio's job stops as by ✕ -
    «отменено» -, and the card is the person's: the Studio's queue does not take it back by itself, the person's next
    Studio job does. Removing a model stops the job filming with it as ✕ does and unloads that model only."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DRAW, CLIP]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    first = installs.post("/v1/install/pictures", json={"id": "clip"}).json()["job"]
    assert wait_for(installs, first["id"], "done", "failed")["status"] == "done"
    monkeypatch.setattr(FakeLlamaSwap, "video", [])
    state = installs.app.state

    def film(prompt):
        return installs.post("/v1/visual/videos", json={"prompt": prompt, "model": "clip"}).json()["job"]

    def studio(job):
        return installs.get(f"/v1/visual/jobs/{job['id']}").json()["job"]["status"]

    FakeLlamaSwap.filming = filming = threading.Event()
    try:
        fox = film("a fox")
        wait(lambda: len(FakeLlamaSwap.video) == 1, timeout=20)  # it films
        cat = film("a cat")  # in the queue
        assert installs.post("/v1/models/unload").status_code == 200
        wait(lambda: studio(fox) in ("cancelled", "failed", "done"), timeout=20)
        assert studio(fox) == "cancelled", "«Выгрузить» did not stop the video as ✕ does"
        assert state.card.given
        time.sleep(0.6)
        assert studio(cat) == "queued" and len(FakeLlamaSwap.video) == 1, "the queue took the card back by itself"
        filming.set()
        dog = film("a dog")  # the person's own: the card is theirs again, and the queue goes on
        wait(lambda: studio(dog) == "done", timeout=20)
        assert studio(cat) == "done" and not state.card.given
    finally:
        filming.set()

    FakeLlamaSwap.filming = filming = threading.Event()
    try:
        owl = film("an owl")
        wait(lambda: len(FakeLlamaSwap.video) == 4, timeout=20)
        state.status.latest = {**state.status.latest, "local": {"reachable": True, "running": [{"model": "clip"}]}}
        FakeLlamaSwap.unloaded.clear()
        removed = installs.delete("/v1/install/models/clip")
        assert removed.status_code == 200, removed.text
        wait(lambda: studio(owl) in ("cancelled", "failed", "done"), timeout=20)
        assert studio(owl) == "cancelled", "removing its model failed the video instead of stopping it"
        assert "/api/models/unload" not in FakeLlamaSwap.unloaded, "removing one model unloaded every model"
    finally:
        filming.set()


def test_unload_puts_a_trial_off_and_the_persons_unattended_work_waits_for_them(installs, monkeypatch):  # noqa: F811
    """48.9г: «Выгрузить» during an install's trial left it «не запустилась», and smaller settings went on a sound
    model. Through the card's owner the trial is put off as a senior puts it off (48.9в) - «ждёт пробного
    запуска», nothing written - and the card is the person's until their next action: background work does not take
    it back, nor does the person's work without them - an outside client is told so (503, Retry-After), the editor's
    completion says why it is silent. The person's chat takes the card back; the trial goes on once the card has been
    still."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.5)
    state = installs.app.state
    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
        job = installs.post("/v1/install", json=spec).json()["job"]
        wait(lambda: any(b.get("model") == "tiny-q4_k_m" for b in FakeLlamaSwap.asked), timeout=20)  # its question
        assert installs.post("/v1/models/unload").status_code == 200
        waiting = wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=10)
        assert waiting["status"] == "trialwait", "«Выгрузить» failed the trial"
        assert "не запустилась" not in waiting["error"]
    finally:
        answering.set()
    assert state.card.given
    time.sleep(1.0)
    assert wait_for(installs, job["id"], "trialwait"), "the trial took the card the person was given"

    body = {"model": "llama-swap/fast", "messages": [{"role": "user", "content": "привет"}]}
    refused = installs.post("/v1/openai/chat/completions", json=body)
    assert refused.status_code == 503 and refused.headers.get("retry-after"), refused.text
    assert "Продолжить" in refused.json()["error"]["message"]
    folder = installs.tmp / "project"
    folder.mkdir()
    pid = installs.post("/v1/projects", json={"path": str(folder)}).json()["id"]
    roles = {"model_id": "", "settings": {"roles": {"complete": "llama-swap/fast"}}}
    assert installs.put("/v1/model_settings", json=roles).status_code == 200
    around = {"project_id": pid, "path": "app.py", "before": "x = ", "after": "\n", "load": True}
    head = json.loads(installs.post("/v1/editor/complete", json=around).text.splitlines()[0])
    assert head.get("paused") == "given" and "Продолжить" in head["message"]
    assert state.card.given and wait_for(installs, job["id"], "trialwait")

    conv = installs.post("/v1/conversations", json={}).json()["id"]
    hello = {"content": "привет", "model_id": "llama-swap/fast"}
    assert installs.post(f"/v1/conversations/{conv}/messages", json=hello).status_code == 202
    wait(lambda: _answered(installs, conv), timeout=20)
    assert not state.card.given  # the person's own action takes the card back
    done = wait_for(installs, job["id"], "done", "failed", timeout=20)
    assert done["status"] == "done" and len(done["trial"]["tries"]) == 1


def test_unload_stops_tuning_and_it_waits_while_the_card_is_the_persons(installs, monkeypatch):  # noqa: F811
    """48.9г: «Выгрузить» while tuning measured unloaded the models under llama-bench, which measured on - and the
    numbers it wrote by were not the card's. Through the card's owner tuning is stopped, nothing kept, and it does not
    come back while the card is the person's; after their next action, once the card has been still."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    tuner = installs.app.state.tuner
    seen = _measuring(tuner, monkeypatch)
    installs.portal.call(tuner.request, "fast")
    wait(lambda: seen["runs"] == 1, timeout=10)
    assert installs.post("/v1/models/unload").status_code == 200
    wait(lambda: seen["halted"] is not None, timeout=10)
    time.sleep(0.8)
    assert seen["runs"] == 1 and installs.app.state.card.given, "tuning took the card the person was given"
    conv = installs.post("/v1/conversations", json={}).json()["id"]
    hello = {"content": "привет", "model_id": "llama-swap/fast"}
    assert installs.post(f"/v1/conversations/{conv}/messages", json=hello).status_code == 202
    wait(lambda: _answered(installs, conv), timeout=20)
    wait(lambda: seen["runs"] == 2 and "fast" in tuner.results, timeout=20)


def test_continue_gives_the_card_back_and_tuning_goes_on_at_once(installs, monkeypatch):  # noqa: F811
    """48.9д: after «Выгрузить» the card is the person's - the window's status line says so (the hub's status), tuning
    says what it waits for; «Продолжить» gives it back: what waited goes on at once, in the order of seniority, not
    after QUIET_S of quiet."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 30.0)  # longer than the test: only «Продолжить» lets it go on
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    tuner = installs.app.state.tuner
    seen = _measuring(tuner, monkeypatch)
    installs.portal.call(tuner.request, "fast")
    wait(lambda: seen["runs"] == 1, timeout=10)
    assert installs.get("/v1/status").json()["card"] == {"given": False, "studio_held": False}
    assert installs.post("/v1/models/unload").status_code == 200
    wait(lambda: seen["halted"] is not None, timeout=10)
    assert installs.get("/v1/status").json()["card"] == {"given": True, "studio_held": True}
    wait(lambda: installs.get("/v1/tune").json()["job"].get("why") == "given", timeout=10)
    went = installs.post("/v1/gpu/continue")
    assert went.status_code == 200 and went.json()["card"] == {"given": False, "studio_held": False}
    wait(lambda: seen["runs"] == 2 and "fast" in tuner.results, timeout=10)


def test_continue_lets_a_trial_put_off_by_unload_go_on_at_once(installs, monkeypatch):  # noqa: F811
    """48.9д: an install's trial put off by «Выгрузить» says why it waits - «видеокарта отдана — „Продолжить“» - and
    goes on at once by «Продолжить»."""
    monkeypatch.setattr(gpu_card, "QUIET_S", 30.0)
    FakeLlamaSwap.answering = answering = threading.Event()
    try:
        spec = {"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False, "drive": "T"}
        job = installs.post("/v1/install", json=spec).json()["job"]
        wait(lambda: any(b.get("model") == "tiny-q4_k_m" for b in FakeLlamaSwap.asked), timeout=20)  # its question
        assert installs.post("/v1/models/unload").status_code == 200
        assert wait_for(installs, job["id"], "trialwait", "done", "failed", timeout=10)["status"] == "trialwait"
    finally:
        answering.set()
    wait(lambda: "Продолжить" in wait_for(installs, job["id"], "trialwait")["error"], timeout=10)
    assert installs.post("/v1/gpu/continue").status_code == 200
    done = wait_for(installs, job["id"], "done", "failed", timeout=15)
    assert done["status"] == "done" and not done["error"]


# --- a found model that draws, installed beside its family's set (47.6) ---


def test_a_found_picture_model_installs_with_its_familys_parts_and_draws(installs, monkeypatch):  # noqa: F811
    """47.6: a model found on Hugging Face whose family AFO knows (47.2) installs as the family's set does - the
    engine, a block for sd-server, a trial picture - with its own variant as the model and the encoder and VAE the set
    already keeps (47.3), not downloaded again. It is its own install: the set's card does not take it for the set,
    the same variant twice is refused, another variant is another model; removing it keeps the set's parts."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DRAW]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    repo = "someone/Draw-More-GGUF"

    def install(spec: dict) -> dict:
        answer = installs.post("/v1/install/pictures", json=spec)
        assert answer.status_code == 200, answer.text
        done = wait_for(installs, answer.json()["job"]["id"], "done", "failed")
        assert done["status"] == "done", done["error"]
        return done

    draw = install({"id": "draw"})
    found = install({"id": "draw", "repo": repo, "quant": "Q4_0"})
    pictures = installs.tmp / "localchat-model" / "pictures"
    model = pictures / "someone" / "Draw-More-GGUF" / "draw-more-Q4_0.gguf"
    shared = {f["role"]: f.get("at") for f in draw["files"] if f["role"] not in ("engine", "diffusion-model")}
    assert [(f["role"], f.get("repo"), f.get("at")) for f in found["files"]] == [
        ("diffusion-model", repo, None), ("llm", "test/Words-GGUF", shared["llm"]),
        ("vae", "test/Vae", shared["vae"])]  # fmt: skip
    assert model.read_bytes() == b"d" * 40_000 and (found["repo"], found["quant"]) == (repo, "Q4_0")
    assert (found["model_id"], found["name"], found["plan"]["set"]) == ("draw-more-q4_0", "Draw-More Q4_0", "draw")
    command = lc.model_view(installs.config.read_text(encoding="utf-8"), found["model_id"])["command"]
    assert f'--diffusion-model "{model.as_posix()}"' in command
    assert f'--llm "{Path(shared["llm"]).as_posix()}"' in command
    assert "draw-more-q4_0" in [b.get("model") for b in FakeLlamaSwap.asked]  # its trial picture
    drawing = {m["model_id"] for m in installs.get("/v1/visual/models").json()["models"]}
    assert drawing == {"draw", "draw-more-q4_0"}
    assert installs.get("/v1/install/pictures").json()["models"][0]["installed"] == ["draw"]  # the set's card: its own

    again = installs.post("/v1/install/pictures", json={"id": "draw", "repo": repo, "quant": "Q4_0"})
    assert again.status_code == 409 and "уже установлена" in again.json()["detail"]
    for wrong, status in (({"id": "draw", "repo": repo, "quant": "Q5_K"}, 404),
                          ({"id": "draw", "repo": "test/Words-GGUF", "quant": "Q4_K_M"}, 422)):  # fmt: skip
        assert installs.post("/v1/install/pictures", json=wrong).status_code == status, wrong
    other = install({"id": "draw", "repo": repo, "quant": "Q2_K"})  # another variant: another model
    assert other["model_id"] == "draw-more-q2_k"

    installs.delete(f"/v1/install/models/{found['model_id']}")
    assert not model.exists() and Path(shared["llm"]).exists()  # the set and the other variant name them


FOUND_FILM = {
    "id": "someone/Film-Two-GGUF",
    "downloads": 9,
    "likes": 1,
    "trendingScore": 1,
    "createdAt": "2026-09-05T00:00:00.000Z",
    "lastModified": "2026-10-05T00:00:00.000Z",
    "tags": ["gguf", "text-to-video", "license:apache-2.0", "base_model:quantized:test/Film"],
    "gguf": {"total": 70_000},
}  # unsloth's LTX-2.5: dev and distilled, the families' own repository
FOUND_DRAW = {
    "id": "test/Draw-GGUF",
    "downloads": 8,
    "likes": 1,
    "trendingScore": 1,
    "createdAt": "2026-09-04T00:00:00.000Z",
    "lastModified": "2026-10-04T00:00:00.000Z",
    "tags": ["gguf", "text-to-image", "license:apache-2.0"],
    "gguf": {"total": 120_000},
}  # a card's own


@contextmanager
def window_on(server, qapp):  # noqa: F811
    """The window on this hub: its client the real one, each call through the hub's own routes."""
    from app.hub_client import HubClient
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        token = server.headers["Authorization"].removeprefix("Bearer ")
        quick.controller.attach(HubClient("http://127.0.0.1:8765", token, http=server), start_events=False)
        wait_until(lambda: not quick.controller.bg.busy, timeout=20)
        yield quick
    finally:
        quick.close()


def install_of(server, family):
    """The install as this picture set's or family's, if there is one."""
    return next((j for j in server.get("/v1/install/jobs").json()["jobs"] if j["plan"].get("set") == family), None)


@pytest.mark.filterwarnings("ignore:You should not use the .timeout. argument")  # the window's client
def test_a_found_model_clicked_in_the_search_shows_its_variants_to_install(installs, qapp, monkeypatch):  # noqa: F811
    """The user's complaint (2026-11-17, AFO 0.1.15): a video model could not be installed through the window - a
    click on unsloth/LTX-2.5-GGUF in «Поиск на Hugging Face» led to «Подборка», «Рисовать», where it is not: 0.1.13's
    jump to a picture set's card (`picture`) stood before 47.7б's path, and the hub names the set of every repository
    holding a family's main file - 26 families without a card since 0.1.14. The window's tests had stood in an answer
    of the hub's own making. Here the hub is the real one: a click on a found model, of a family without a card and of
    a set with one, shows its variants in the search, and «Установить» there installs the one chosen."""
    from PySide6.QtCore import QObject

    from tests import test_model_install as tmi

    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DRAW, FILM, DISTILLED]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    monkeypatch.setattr(tmi, "SEARCH", [*tmi.SEARCH, FOUND_FILM, FOUND_DRAW])
    memory = {**tmi.SPACE["memory"], "commit_limit_gib": 80.0, "commit_used_gib": 20.0}  # as the hub's own look says
    installs.app.state.status.latest = {**tmi.SPACE, "memory": memory}
    with window_on(installs, qapp) as quick:
        window, view = quick.window, quick.controller.install_view
        quick.controller.openUi("install-models")
        sheet = window.findChild(QObject, "installSheet")
        wait_until(lambda: sheet.property("opened"))
        body = sheet.property("contentItem")
        click(window, item_saying(body, "Поиск на Hugging Face"))
        click(window, item_saying(body, "Видео"))
        wait_until(lambda: not view.searching and view.found, timeout=10)
        click(window, item_saying(body, FOUND_FILM["id"]))
        wait_until(lambda: view.repoName == FOUND_FILM["id"] and view.variants, timeout=10)
        assert view.tab == "search"  # still the search: its variants open in it
        row = next(r for r in view.found if r["repo"] == FOUND_FILM["id"])
        assert "«Рисовать»" not in row["fit"]  # nowhere else to go
        assert [v["quant"] for v in view.variants] == ["Film dev · Q4_0", "Film dev · UD-Q4_0",
                                                       "Film distilled · Q4_0"]  # fmt: skip
        button = item_named(body, "installVariant_film-distilled/Q4_0")
        assert button is not None and button.isVisible() and button.property("text") == "Установить"
        click(window, button)
        wait_until(lambda: install_of(installs, "film-distilled"), timeout=10)
        film = install_of(installs, "film-distilled")
        assert (film["repo"], film["quant"], film["plan"].get("found")) == (FOUND_FILM["id"], "Q4_0", FOUND_FILM["id"])
        assert wait_for(installs, film["id"], "done", "failed")["status"] == "done"

        # a set with a card among «Рисовать» - its own repository's variants too, the card's variant the card's install
        click(window, item_saying(body, "Назад"))
        wait_until(lambda: view.repoName == "")
        click(window, item_saying(body, "Рисовать"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_DRAW["id"] for r in view.found), timeout=10)
        click(window, item_saying(body, FOUND_DRAW["id"]))
        wait_until(lambda: view.repoName == FOUND_DRAW["id"] and view.variants, timeout=10)
        click(window, item_named(body, "installVariant_draw/Q4_0"))
        wait_until(lambda: install_of(installs, "draw"), timeout=10)
        draw = install_of(installs, "draw")
        assert wait_for(installs, draw["id"], "done", "failed")["status"] == "done"
        assert "found" not in draw["plan"]  # the card's own model: the card says it is installed
        card = next(m for m in installs.get("/v1/install/pictures").json()["models"] if m["id"] == "draw")
        assert card["installed"] == [draw["model_id"]]
        assert quick.warnings == []


@pytest.mark.filterwarnings("ignore:You should not use the .timeout. argument")  # the window's client
def test_a_found_picture_model_says_what_goes_on_the_card_and_what_into_memory(installs, qapp, monkeypatch):  # noqa: F811
    """The user's complaint (2026-11-17, AFO 0.1.15): MiniMax-H3 on a 16 GB card - «≈ 35,7 ГБ с кодировщиком и VAE —
    помещается в видеопамять — быстро», though only the model goes on the card, the encoder and VAE into the
    computer's memory. Now a found model says how much goes on the card, how much into the computer's memory and how
    much comes from the internet; one the memory cannot hold - «не хватит памяти компьютера»; a family nobody ran
    here - «по расчёту, не проверялась»; the line over the sheet says what the video memory it names is. The catalog's
    descriptions say what a model is, not how fast it is: the verdict beside it does not argue with them."""
    import re

    from PySide6.QtCore import QObject

    from app.ui.install_view import gb
    from tests import test_model_install as tmi

    real = json.loads(Path(mi.__file__).with_name("model_catalog.json").read_text(encoding="utf-8"))["pictures"]
    speed = re.compile(r"быстр|медлен|за секунд|за несколько|\d+ с\b|минут", re.IGNORECASE)
    assert [e["id"] for e in real if speed.search(e["about"]["ru"]) or speed.search(e["about"].get("en", ""))] == []
    assert {e["id"] for e in real if e.get("checked")} == {
        "sdxs-512", "flux2-klein-4b", "z-image-turbo", "wan2.2-ti2v-5b-turbo", "qwen-image-2.1-turbo",
        "flux3-image-klein", "qwen-image", "wan2.2-ti2v-5b", "wan2.2-i2v-a14b", "ltx-2.3-distilled"}  # fmt: skip

    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [{**DRAW, "checked": True}, FILM, DISTILLED]}),
                          encoding="utf-8")  # fmt: skip
    installer = installs.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(tmi, "SEARCH", [*tmi.SEARCH, FOUND_FILM, FOUND_DRAW])
    for name in ("PICTURE_COMPUTE_GIB", "PICTURE_STAGING_GIB", "SD_MARGIN_GIB", "TIGHT_GIB"):  # the stand: kilobytes
        monkeypatch.setattr(mi, name, 0.0)
    card = {"gpu": "RTX", "vram_total_gib": 15.9, "vram_gib": 14.9, "ram_total_gib": 64.0, "ram_gib": 60.0,
            "loaded": [], "busy": ""}  # fmt: skip
    space = {"now": card}
    monkeypatch.setattr(installer, "space", lambda: space["now"])
    with window_on(installs, qapp) as quick:
        window, view = quick.window, quick.controller.install_view
        quick.controller.openUi("install-models")
        sheet = window.findChild(QObject, "installSheet")
        wait_until(lambda: sheet.property("opened") and not quick.controller.bg.busy)
        body = sheet.property("contentItem")
        line = item_named(body, "installSystem").property("text")
        assert line.startswith("RTX: модели можно занять ≈ 14,9 ГБ видеопамяти из 15,9 ГБ — остальное оставлено "
                               "рабочему столу и другим программам · ")  # fmt: skip

        # the model (≈ 42 875 bytes, 70 000 parameters as about Q4_K_M) on the card, its encoder and VAE (90 000)
        # beyond the card's rest - in the computer's memory
        space["now"] = {**card, "vram_gib": 50_000 / mi.GIB, "ram_gib": 1_000_000 / mi.GIB}
        click(window, item_saying(body, "Поиск на Hugging Face"))
        click(window, item_saying(body, "Видео"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_FILM["id"] for r in view.found), timeout=10)
        fit = next(m for m in view._found if m["repo"] == FOUND_FILM["id"])["fit"]
        model = int(70_000 * mi.FIT_BITS / 8)
        assert (fit["verdict"], fit["card"], fit["ram"], fit["download"]) == ("gpu", model, 90_000, model + 90_000)
        said = (f"AFO ставит её сам: на видеокарте ≈ {gb(model)}, в памяти компьютера ≈ {gb(90_000)} — быстро "
                f"(по расчёту, не проверялась) · скачается ≈ {gb(model + 90_000)}")  # fmt: skip
        assert next(r for r in view.found if r["repo"] == FOUND_FILM["id"])["fit"] == said
        assert item_saying(body, said) is not None  # in the window, under the found model

        click(window, item_saying(body, FOUND_FILM["id"]))
        wait_until(lambda: view.repoName == FOUND_FILM["id"] and view.variants, timeout=10)
        words = {v["quant"]: v["words"] for v in view.variants}
        assert words["Film distilled · Q4_0"] == (
            f"на видеокарте ≈ {gb(41_000)}, в памяти компьютера ≈ {gb(90_000)} — быстро (по расчёту, не проверялась) "
            f"· скачается ≈ {gb(41_000 + 90_000)}")  # fmt: skip
        assert item_saying(body, words["Film distilled · Q4_0"]) is not None
        click(window, item_saying(body, "Назад"))
        wait_until(lambda: view.repoName == "")

        # a checked family whose model does not fit the card: all of it in the computer's memory, no «по расчёту»
        click(window, item_saying(body, "Рисовать"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_DRAW["id"] for r in view.found), timeout=10)
        drawn = int(120_000 * mi.FIT_BITS / 8)
        assert next(r for r in view.found if r["repo"] == FOUND_DRAW["id"])["fit"] == (
            f"AFO ставит её сам: в памяти компьютера ≈ {gb(drawn + 90_000)} — медленнее: модель не помещается в "
            f"видеопамять, видеокарта считает её по частям · скачается ≈ {gb(drawn + 90_000)}")  # fmt: skip

        # the computer's memory does not hold the encoder and VAE beside the model
        space["now"] = {**card, "vram_gib": 50_000 / mi.GIB, "ram_gib": 1_000 / mi.GIB}
        click(window, item_saying(body, "Видео"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_FILM["id"] for r in view.found), timeout=10)
        row = next(r for r in view.found if r["repo"] == FOUND_FILM["id"])
        assert row["fit"].startswith("AFO ставит её сам: не хватит памяти компьютера: нужно ≈ ")
        assert row["fitTone"] == "error"
        assert quick.warnings == []


def gguf_header(tensors):
    """A GGUF file's header alone: an architecture, a few tokens and the table of `tensors` - (name, dims, ggml type) -
    with no weights after it: what Hugging Face's first bytes of an encoder show."""

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data

    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), 2)
    out += string("general.architecture") + struct.pack("<I", 8) + string("gemma3")
    out += string("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + b"".join(map(string, "abc"))
    for name, dims, kind in tensors:
        out += string(name) + struct.pack(f"<I{len(dims)}Q", len(dims), *dims) + struct.pack("<IQ", kind, 0)
    return out


@pytest.mark.filterwarnings("ignore:You should not use the .timeout. argument")  # the window's client
def test_a_found_video_model_at_the_cards_edge_is_not_called_fast(installs, qapp, monkeypatch):  # noqa: F811
    """The user's complaint and 48.8's numbers: LTX-2.3 distilled Q4_0 on the RTX 5080 was «помещается — быстро», and
    sd.cpp's auto-fit put all of it in RAM - the hub took the card less 1 GB while the desktop held 1,3, and half a
    gigabyte for the encoder's largest tensor while gemma's is ≈ 0,8. Now a picture or video model is judged by what
    the card has free when sd.cpp starts: the desktop and other programs as they are (measured while llama-swap holds
    no model, kept while it does), not less than the reserve; the encoder's largest tensor is read from its file's
    header on Hugging Face; less than 0,5 GB to spare is «впритык». A chat model's room stays the computer's (38.1)."""
    from PySide6.QtCore import QObject

    from tests import test_model_install as tmi

    mib = 1 << 20
    model, encoder, vae = 12_127 * mib, 9_286 * mib, 1_384 * mib  # 48.8's auto-fit plan, MiB
    embedding = 262_144 * 3_840 * 210 // 256  # gemma-3-12b's token_embd in Q6_K: ≈ 0,8 GB
    header = gguf_header([("token_embd.weight", (3_840, 262_144), 14), ("blk.0.attn_q.weight", (3_840, 4_096), 12)])
    monkeypatch.setitem(tmi.PICTURES["test/Words-GGUF"], "words-Q4_K_M.gguf", header)
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DISTILLED]}), encoding="utf-8")
    installer = installs.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(tmi, "SEARCH", [*tmi.SEARCH, FOUND_FILM])
    listed, real = installer._listed, {"distilled/film-distilled-Q4_0.gguf": model, "words-Q4_K_M.gguf": encoder,
                                       "split_files/vae/vae.safetensors": vae}  # fmt: skip

    async def sized(repo, **how):  # the stand's files are kilobytes: Hugging Face's sizes of the real ones
        found = await listed(repo, **how)
        return {**found, "files": {p: {**f, "size": real.get(p, f["size"])} for p, f in found["files"].items()}}

    monkeypatch.setattr(installer, "_listed", sized)
    memory = {**tmi.SPACE["memory"], "commit_limit_gib": 80.0, "commit_used_gib": 20.0}
    card = {"name": "RTX 5080", "vram_total_mib": 16_303, "vram_used_mib": 1_331}  # the desktop: 1,3 GB
    installs.app.state.status.latest = {"gpu": card, "memory": memory, "local": {"reachable": True, "running": []}}
    with window_on(installs, qapp) as quick:
        window, view = quick.window, quick.controller.install_view
        quick.controller.openUi("install-models")
        sheet = window.findChild(QObject, "installSheet")
        wait_until(lambda: sheet.property("opened") and not quick.controller.bg.busy)
        body = sheet.property("contentItem")
        click(window, item_saying(body, "Поиск на Hugging Face"))
        click(window, item_saying(body, "Видео"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_FILM["id"] for r in view.found), timeout=10)
        click(window, item_saying(body, FOUND_FILM["id"]))
        wait_until(lambda: view.repoName == FOUND_FILM["id"] and view.variants, timeout=10)
        (variant,) = view.variants
        assert variant["words"].startswith("в памяти компьютера ≈ ") and "медленнее" in variant["words"]
        assert "быстро" not in variant["words"] and variant["tone"] == "warn"
        assert item_saying(body, variant["words"]) is not None  # in the window
        assert embedding in installer._largest.values()  # read from the encoder's header
        assert quick.warnings == []

        space = installs.get("/v1/install/system").json()["space"]
        assert (space["vram_gib"], space["picture_gib"]) == (14.9, 14.6)  # a chat model's room: the reserve's (38.1)
        # the desktop as it was: kept while llama-swap holds a model of its own (it would count with the others)
        held = {"reachable": True, "running": [{"model": "llama-swap/a"}]}
        installs.app.state.status.latest = {"gpu": {**card, "vram_used_mib": 14_000}, "memory": memory, "local": held}
        assert installs.get("/v1/install/system").json()["space"]["picture_gib"] == 14.6
    space = {"vram_gib": 14.9, "picture_gib": 14.6, "vram_total_gib": 15.9, "ram_gib": 61.0}
    assert mi.picture_fit(model, encoder + vae, space, staging=embedding) == "split"
    assert mi.picture_fit(11_500 * mib, encoder + vae, space, staging=embedding) == "tight"  # 0,1 GB to spare
    assert mi.picture_fit(11_000 * mib, encoder + vae, space, staging=embedding) == "gpu"


@pytest.mark.filterwarnings("ignore:You should not use the .timeout. argument")  # the window's client
def test_a_part_closed_on_hugging_face_is_said_before_anything_downloads(installs, qapp, monkeypatch):  # noqa: F811
    """The user's complaint (2026-11-18, AFO 0.1.16): LTX-2.5 Q3_K_M from the search - «1,0 МБ/с · ещё ≈ 359 мин».
    Beside the slow GitHub, its encoder (Gemma 4 12B, 8,4 GB) lies in a gated repository (elix3r's): without a token
    and its terms accepted Hugging Face answers 401, and the install would have stopped on it after the engine and the
    11 GB model - the hub looked at the found repository's gating only. Now the variants say which part is closed,
    where to accept its terms and where the token goes; «Установить» starts nothing until the token may read it."""
    from PySide6.QtCore import QObject

    from tests import test_model_install as tmi

    closed = "someone/Closed-Words-GGUF"
    encoder = {"role": "llm", "repo": closed, "path": "closed-words-Q4_K_M.gguf"}  # LTX-2.5's Gemma 4 12B, as it lies
    family = {**DISTILLED, "files": [DISTILLED["files"][0], encoder, DISTILLED["files"][2]]}
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [family]}), encoding="utf-8")
    installer = installs.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    monkeypatch.setattr(tmi, "SEARCH", [*tmi.SEARCH, FOUND_FILM])
    token = {"now": None}  # «Подключения»: no Hugging Face token, as on the user's computer
    monkeypatch.setattr(installer, "token", lambda: token["now"])
    memory = {**tmi.SPACE["memory"], "commit_limit_gib": 80.0, "commit_used_gib": 20.0}
    installs.app.state.status.latest = {**tmi.SPACE, "memory": memory}
    with window_on(installs, qapp) as quick:
        window, view, controller = quick.window, quick.controller.install_view, quick.controller
        controller.openUi("install-models")
        sheet = window.findChild(QObject, "installSheet")
        wait_until(lambda: sheet.property("opened") and not controller.bg.busy)
        body = sheet.property("contentItem")
        click(window, item_saying(body, "Поиск на Hugging Face"))
        click(window, item_saying(body, "Видео"))
        wait_until(lambda: not view.searching and any(r["repo"] == FOUND_FILM["id"] for r in view.found), timeout=10)
        click(window, item_saying(body, FOUND_FILM["id"]))
        wait_until(lambda: view.repoName == FOUND_FILM["id"] and view.variants, timeout=10)

        # the variants say it before a click: which part, where to accept its terms, where the token goes
        note = item_named(body, "installRepoClosed")
        assert note is not None and note.isVisible(), "nothing says a part of the model is closed"
        said = note.property("text")
        assert f"huggingface.co/{closed}" in said and "«Настройки → Подключения»" in said, said
        assert item_named(body, "installRepoClosedPage").isVisible()  # its page, a click away

        # «Установить»: nothing starts - no job, no engine archive from GitHub, no file of the model
        click(window, item_named(body, "installVariant_film-distilled/Q4_0"))
        wait_until(lambda: controller.flashError and closed in controller.flashText, timeout=10)
        assert "«Настройки → Подключения»" in controller.flashText
        assert install_of(installs, "film-distilled") is None and FakeHub.github_auth == []
        assert not (installs.tmp / "localchat-model" / "pictures" / "someone").exists()

        # the token of an account that accepted the terms: the note goes, the install goes to the end
        token["now"] = "hf_good"
        click(window, item_saying(body, "Назад"))
        wait_until(lambda: view.repoName == "")
        click(window, item_saying(body, FOUND_FILM["id"]))
        wait_until(lambda: view.repoName == FOUND_FILM["id"] and view.variants and not view.repoLoading, timeout=10)
        assert not item_named(body, "installRepoClosed").isVisible()
        click(window, item_named(body, "installVariant_film-distilled/Q4_0"))
        wait_until(lambda: install_of(installs, "film-distilled"), timeout=10)
        film = install_of(installs, "film-distilled")
        assert wait_for(installs, film["id"], "done", "failed")["status"] == "done"
        assert quick.warnings == []


def test_a_sum_hidden_before_the_token_came_does_not_spoil_the_download(installs, monkeypatch):  # noqa: F811
    """The user's complaint (2026-11-19, AFO 0.1.17): «LTX-2.5 UD-Q3_K_XL — не вышло: gemma4-12b-with-proj-ltx-2.5-
    Q4_K_M.gguf: файл пришёл повреждённым (sha256 не совпал) — скачается заново» - four times in half an hour, after
    8,4 GB twice each. Hugging Face lists a gated repository's files to one it does not let in with 64 asterisks in
    place of each sha256; the hub had looked at the encoder's repository before the token came (AFO 0.1.16), kept the
    answer in hf_answers.json, and the install and its «Повторить» checked the file against the asterisks. Now an answer
    kept before is asked again, and a file whose sum is not known - in an install 0.1.17 made - has its sum asked at its
    commit with the token before it comes: «Повторить» brings that file alone."""
    from tests import test_model_install as tmi

    closed, path = "someone/Closed-Words-GGUF", "closed-words-Q4_K_M.gguf"
    encoder = {"role": "llm", "repo": closed, "path": path}  # LTX-2.5's Gemma 4 12B, as it lies
    family = {**DISTILLED, "files": [DISTILLED["files"][0], encoder, DISTILLED["files"][2]]}
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [family]}), encoding="utf-8")
    installer = installs.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    monkeypatch.setattr(tmi, "SEARCH", [*tmi.SEARCH, FOUND_FILM])
    token = {"now": None}  # «Подключения»: no Hugging Face token yet, as on the user's computer
    monkeypatch.setattr(installer, "token", lambda: token["now"])
    memory = {**tmi.SPACE["memory"], "commit_limit_gib": 80.0, "commit_used_gib": 20.0}
    installs.app.state.status.latest = {**tmi.SPACE, "memory": memory}
    real = hashlib.sha256(tmi.PICTURES[closed][path]).hexdigest()

    # AFO 0.1.16 looked at the model without the token and kept what Hugging Face said as it said it: the asterisks
    assert installs.get(f"/v1/install/pictures/repo/{FOUND_FILM['id']}").status_code == 200
    file = data_paths().hf_answers
    kept = json.loads(file.read_text(encoding="utf-8"))
    for entry in kept["repos"].values():
        entry["found"].pop("hidden", None)
    for listed in kept["repos"][f"files:{closed}"]["found"]["files"].values():
        listed["sha256"] = "*" * 64
    file.write_text(json.dumps(kept), encoding="utf-8")
    installer._repos.clear()
    installer._load_answers()  # the update to 0.1.17: a new hub, the answers from the file

    # the token of an account that accepted the terms: the install goes to the end, the encoder checked
    token["now"] = "hf_good"
    answer = installs.post("/v1/install/pictures", json={"id": "film-distilled", "repo": FOUND_FILM["id"],
                                                         "quant": "Q4_0"})  # fmt: skip
    assert answer.status_code == 200, answer.text
    film = wait_for(installs, answer.json()["job"]["id"], "done", "failed")
    assert film["status"] == "done", film["error"]
    part = next(f for f in film["files"] if f["role"] == "llm")
    assert part["sha256"] == real

    # the install as 0.1.17 left it on the user's computer: the encoder's sum the asterisks, its file thrown away
    model = Path(film["folder"]) / "distilled" / "film-distilled-Q4_0.gguf"
    before = model.stat().st_mtime_ns
    Path(part["at"]).unlink()
    files = [{**f, "sha256": "*" * 64, "done": False} if f["role"] == "llm" else f for f in film["files"]]
    said = f"{path}: файл пришёл повреждённым (sha256 не совпал) — скачается заново"
    installs.app.state.db.call(lambda conn: mi._update(conn, film["id"], files=files, status="failed", error=said))
    assert installs.post(f"/v1/install/jobs/{film['id']}/resume").status_code == 200  # «Повторить»
    again = wait_for(installs, film["id"], "done", "failed")
    assert again["status"] == "done", again["error"]
    assert Path(part["at"]).read_bytes() == tmi.PICTURES[closed][path]
    assert next(f for f in again["files"] if f["role"] == "llm")["sha256"] == real
    assert model.stat().st_mtime_ns == before  # the model's 12 GB stayed: only the encoder came again


@pytest.mark.filterwarnings("ignore:You should not use the .timeout. argument")  # the window's client
def test_the_time_said_while_the_engine_comes_from_github_is_the_engines(installs, qapp, monkeypatch):  # noqa: F811
    """The user's complaint (2026-11-18, AFO 0.1.16): «0,3 ГБ из 22,1 ГБ · 1,0 МБ/с · ещё ≈ 359 мин» - the video
    engine (0,94 GB) comes first, from GitHub (0,6 MB/s a connection here), and the window counted all 22,1 GB at its
    speed; the model's files come from Hugging Face at ≈ 35. Now while the engine comes the hub says how much of it is
    left and the window says its time - «программа для видео: ещё ≈ …»; the model's time comes with its own files, at
    their speed. The hub's events as the window's event thread hands them, the window's own code reading them."""
    import os

    from app.ui.install_view import left_text
    from tests import test_model_install as tmi

    engine = next(name for name in tmi.ENGINE if "cuda12" in name)
    archive = tmi._zip({"sd-server.exe": b"MZ server", "ggml-cuda.dll": os.urandom(400_000)})  # 25 chunks of 16 KB
    monkeypatch.setitem(tmi.ENGINE, engine, archive)
    model = "distilled/film-distilled-Q4_0.gguf"
    monkeypatch.setitem(tmi.PICTURES["someone/Film-Two-GGUF"], model, b"F" * 400_000)
    FakeHub.github_slow, FakeHub.slow = 0.08, 0.01  # GitHub: 25 chunks ≈ 2 s; Hugging Face: 25 ≈ 0,3 s
    monkeypatch.setattr(mi, "PROGRESS_S", 0.1)
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    mi.CATALOG.write_text(json.dumps({**catalog, "pictures": [DISTILLED]}), encoding="utf-8")
    monkeypatch.setattr(installs.app.state.installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installs.app.state.visual, "_machine", lambda: MACHINE)
    memory = {**tmi.SPACE["memory"], "commit_limit_gib": 80.0, "commit_used_gib": 20.0}  # as the hub's own look says
    installs.app.state.status.latest = {**tmi.SPACE, "memory": memory}
    events = {**dict(installs.headers), "Host": "127.0.0.1:8765"}
    with window_on(installs, qapp) as quick:
        view = quick.controller.install_view
        with installs.websocket_connect("/v1/events", headers=events) as ws:
            answer = installs.post("/v1/install/pictures", json={"id": "film-distilled", "repo": FOUND_FILM["id"],
                                                                 "quant": "Q4_0"})  # fmt: skip
            assert answer.status_code == 200, answer.text
            job_id, progress, said = answer.json()["job"]["id"], [], []
            for _ in range(5000):
                message = ws.receive_json()
                kind, payload = message.get("type") or "", message.get("payload") or {}
                if kind.startswith("install."):
                    view.on_event(kind, payload)
                if kind == "install.progress" and payload["id"] == job_id and payload["speed"] > 0:
                    progress.append(payload)
                    said.append(view.status["text"])
                if kind == "install.changed" and payload["job"]["id"] == job_id and payload["job"]["status"] in (
                        "done", "failed"):  # fmt: skip
                    assert payload["job"]["status"] == "done", payload["job"]["error"]
                    break
    engine_bytes = sum(len(data) for data in tmi.ENGINE.values())
    github = [(p, s) for p, s in zip(progress, said, strict=True) if p.get("engine_left")]
    assert github, "the hub does not say how much of the engine is left"
    for p, s in github:  # the engine's own time, at GitHub's speed
        assert 0 < p["engine_left"] <= engine_bytes and p["engine_left"] < p["total_bytes"] - p["done_bytes"]
        assert f"программа для видео: {left_text(p['engine_left'] / p['speed'])}" in s, s
    hugging_face = [(p, s) for p, s in zip(progress, said, strict=True) if not p.get("engine_left")]
    assert hugging_face, said
    for p, s in hugging_face:  # the model's files: the rest of it, at their speed
        assert "программа" not in s and left_text((p["total_bytes"] - p["done_bytes"]) / p["speed"]) in s, s


# --- «Доверенный» by rules (0.1.17, the user's complaint of 2026-11-17 and his word of 2026-11-18) ---


def cards_of(hub, conv):  # noqa: F811 - the fixture's name
    """Every request of this chat the queue has kept, answered or not, oldest first."""
    rows = hub.app.state.db.call(lambda c: c.execute(
        "SELECT tool, status, decided_by FROM approvals WHERE conversation_id = ? ORDER BY created_at, rowid",
        (conv,)).fetchall())  # fmt: skip
    return [dict(r) for r in rows]


def answered(hub, conv):  # noqa: F811
    """The chat's answer has ended - or a card of it waits (then the test says which, instead of hanging)."""
    waits = lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["conversation_id"] == conv]  # noqa: E731
    wait(lambda: waits() or not hub.get(f"/v1/conversations/{conv}").json()["busy"])
    assert [a["tool"] for a in waits()] == []
    messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
    return [b for m in messages if m["role"] == "tool" for b in m["content"]]


def test_a_trusted_chat_that_read_the_web_goes_on_without_cards(hub, repo, site):  # noqa: F811
    """The user's complaint: «Доверенный», a task that reads a site - and a card on every edit after it. His word:
    it asks nothing inside the project, after reading what others wrote too."""
    steps = [[fetch_call(f"{site}/doc")], [{"id": "w1", "name": "edit_file", "input": edit()}],
             [tc.run("Write-Output after-the-page")]]  # fmt: skip
    pid, conv, _provider = ch.setup_chat(hub, repo, steps, mode="trusted")
    hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": True})  # the page itself is read unasked then (2026-10-28)
    ch.say(hub, conv)
    page, written, ran = answered(hub, conv)
    assert "# Установка" in page["output"] and hub.portal.call(hub.app.state.chat.read_the_web, conv)
    assert written["error"] is False and (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('пр")
    assert ran["error"] is False and "after-the-page" in ran["output"]
    assert cards_of(hub, conv) == []  # nothing was asked


def test_a_trusted_chat_is_refused_what_cannot_be_undone_and_asked_only_when_it_insists(hub, repo):  # noqa: F811
    """The user's word 2026-11-18, «как auto»: what deletes or cannot be undone is not asked on every step - the
    model reads a refusal and looks for another way; refused twice in a row, the person is asked. A command into the
    hub's own data is refused every time, never asked."""
    push, reset = tc.run("git push origin main"), tc.run("git reset --hard HEAD~1")
    _pid, conv, provider = ch.setup_chat(hub, repo, [[push], [tc.run("Write-Output another-way")]], mode="trusted")
    ch.say(hub, conv)
    refused, ran = answered(hub, conv)
    assert refused["error"] is True and refused["output"].startswith("not done: «Доверенный» does not do this unasked")
    assert "cannot be undone" in refused["output"] and "once more" in refused["output"]
    assert ran["error"] is False and "another-way" in ran["output"]
    assert cards_of(hub, conv) == [{"tool": "run_command", "status": "denied", "decided_by": "rules"}]

    provider.steps += [[push], [reset]]  # it found no other way: the second refusal in a row is the person's to say
    ch.say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["destructive"] is True and asked["input"]["command"] == "git reset --hard HEAD~1"
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "не надо"})
    results = answered(hub, conv)
    assert results[-2]["output"].startswith("not done: «Доверенный» does not do this unasked")
    assert results[-1]["output"] == "not done: the user said no - не надо"

    provider.steps += [[tc.run("Get-Content app.token")], [tc.run("type hub-claude-code.token")]]
    ch.say(hub, conv)
    first, second = answered(hub, conv)[-2:]  # twice in a row - and still no card
    for said in (first, second):
        assert said["error"] is True and said["output"].startswith("not done: «Доверенный» never does this")
        assert "own data" in said["output"]
    assert [c["decided_by"] for c in cards_of(hub, conv)] == ["rules", "rules", "app", "rules", "rules"]


def test_always_in_the_project_covers_every_edit_or_every_command_of_it(hub, repo):  # noqa: F811
    """The user's word 2026-11-17: «Разрешить всегда в «web»» on an edit takes the questions off all edits of this
    project, on a command - off all its commands; the button says what it allows. Before, the answer covered one
    tool (edit_file, not write_file) or that very command, and the next one was asked again."""
    from app.ui import project_rows
    from app.ui.approvals import approval_rows

    new_file = {"id": "w2", "name": "write_file", "input": {"project": "proj", "path": "docs/new.md", "content": "x\n"}}
    pid, conv, provider = ch.setup_chat(hub, repo, [[{"id": "w1", "name": "edit_file", "input": edit()}]])
    projects = {pid: {"name": "proj", "path": str(repo)}}
    ch.say(hub, conv)
    asked = tc.answer_next(hub, scope="project")
    assert approval_rows([asked], projects=projects, chats={})[0]["projectScope"] == "все правки файлов в «proj»"
    ch.finished(hub, conv)
    provider.steps += [[new_file], [tc.run("Write-Output one")]]  # another write tool, another chat: not asked
    other = ch.new_chat(hub, pid)
    ch.say(hub, other)
    asked = tc.answer_next(hub, scope="project")
    assert asked["tool"] == "run_command" and (repo / "docs" / "new.md").read_text(encoding="utf-8") == "x\n"
    assert approval_rows([asked], projects=projects, chats={})[0]["projectScope"] == "все команды в «proj»"
    ch.finished(hub, other)
    rules = [r["grant_key"] for r in hub.get(f"/v1/projects/{pid}/rules").json()["rules"]]
    assert rules == ["write", "command"]
    assert [project_rows.rule_text(r) for r in rules] == ["все правки файлов", "все команды"]

    provider.steps += [[tc.run("Write-Output two")], [tc.run("pip install --dry-run nothing-at-all")]]
    third = ch.new_chat(hub, pid)  # another command: not asked; one that reaches the network: asked as ever
    ch.say(hub, third)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["network"] is True and "nothing-at-all" in asked["input"]["command"]
    assert approval_rows([asked], projects=projects, chats={})[0]["projectScope"] == ""
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False})
    two = ch.result_of(ch.finished(hub, third))
    assert two["error"] is False and "two" in two["output"]


def test_an_answer_for_the_chat_or_the_project_holds_after_reading_the_web(hub, repo, site):  # noqa: F811
    """The user's complaint 2026-11-17: after the chat had read a site, «Разрешить: все edit_file в чате» and
    «Разрешить всегда» did nothing - the answer was read as «this once» and the rule never written, though the card
    offered both. An answer the person gave holds; the card says the chat has read what others wrote."""
    from app.ui.approvals import approval_rows

    second = {"id": "w2", "name": "edit_file", "input": edit("README.md", "# Проект", "# Проект web")}
    steps = [[fetch_call(f"{site}/doc")], [{"id": "w1", "name": "edit_file", "input": edit()}], [second],
             [tc.run("Write-Output one")], [tc.run("Write-Output two")]]  # fmt: skip
    pid, conv, _provider = ch.setup_chat(hub, repo, steps)  # «Спрашивать»
    ch.say(hub, conv)
    assert tc.answer_next(hub)["tool"] == "web_fetch"
    asked = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "edit_file"])[0]
    assert asked["tainted"] is True
    row = approval_rows([asked], projects={pid: {"name": "proj", "path": str(repo)}}, chats={})[0]
    assert row["readOthers"] is True
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "scope": "chat"})
    asked = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "run_command"])[0]
    assert "one" in asked["input"]["command"] and asked["tainted"] is True  # the second edit was not asked
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "scope": "project"})
    results = answered(hub, conv)  # the second command was not asked
    assert [r["error"] for r in results] == [False] * 5 and "two" in results[-1]["output"]
    assert (repo / "README.md").read_text(encoding="utf-8").startswith("# Проект web")
    assert [r["grant_key"] for r in hub.get(f"/v1/projects/{pid}/rules").json()["rules"]] == ["command"]
    fresh = ch.new_chat(hub, pid)  # a chat that read nothing of others': the card does not say it
    hub.app.state.gateway.resolve("fake/echo")[0].steps += [[second]]
    ch.say(hub, fresh)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tainted"] is False
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False})
    ch.finished(hub, fresh)


def test_switching_to_trusted_lets_the_open_card_go(hub, repo):  # noqa: F811
    """The user's word 2026-11-17: «смена режима действует сразу, в том числе на идущий ответ». The next action took
    the new mode already; the card open at that moment stayed and the answer stood on it. Now it closes as allowed
    when «Доверенный» would not have asked it - a card for what cannot be undone stays the person's."""
    steps = [[{"id": "w1", "name": "edit_file", "input": edit()}], [tc.run("Write-Output next")]]
    pid, conv, provider = ch.setup_chat(hub, repo, steps)  # «Спрашивать»
    ch.say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "edit_file"
    assert hub.put(f"/v1/conversations/{conv}/context", json={"mode": "trusted"}).status_code == 200
    written, ran = answered(hub, conv)
    assert written["error"] is False and ran["error"] is False and "next" in ran["output"]
    assert cards_of(hub, conv) == [{"tool": "edit_file", "status": "allowed", "decided_by": "hub"}]

    provider.steps += [[tc.run("git push origin main")]]
    other = ch.new_chat(hub, pid)
    ch.say(hub, other)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["destructive"] is True
    assert hub.put(f"/v1/conversations/{other}/context", json={"mode": "trusted"}).status_code == 200
    assert [a["id"] for a in hub.get("/v1/approvals").json()["approvals"]] == [asked["id"]]  # still the person's
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False})
    ch.finished(hub, other)


def test_what_the_rules_refused_is_listed_and_can_be_let_through_once(hub, repo):  # noqa: F811
    """The user's word 2026-11-18, «Недавно заблокировано»: what a trusted chat's rules refused is listed for the
    window; «Разрешить» there lets the chat's next same request through, once. What reaches the hub's own data is
    listed too - and can never be let through."""
    push = tc.run("git push origin main")
    _pid, conv, provider = ch.setup_chat(hub, repo, [[push], [tc.run("Get-Content app.token")]], mode="trusted")
    ch.say(hub, conv)
    answered(hub, conv)
    never, blocked = hub.get("/v1/approvals/blocked").json()["blocked"]  # the newest first
    assert blocked["tool"] == "run_command" and blocked["input"]["command"] == "git push origin main"
    assert blocked["conversation_id"] == conv and blocked["pass"] is None and blocked["never"] is False
    assert never["never"] is True and hub.post(f"/v1/approvals/{never['id']}/pass").status_code == 409
    assert hub.post(f"/v1/approvals/{blocked['id']}/pass").json()["pass"] == "open"
    assert hub.post(f"/v1/approvals/{blocked['id']}/pass").status_code == 409  # let through already

    provider.steps += [[tc.run("git   push origin main")], [push]]  # the same command, spaced otherwise; then again
    ch.say(hub, conv)
    passed, again = answered(hub, conv)[-2:]
    assert passed["error"] is False and passed["output"].startswith("$ git   push origin main")  # it ran
    assert again["output"].startswith("not done: «Доверенный» does not do this unasked")  # once means once
    states = [b["pass"] for b in hub.get("/v1/approvals/blocked").json()["blocked"]]
    assert states == [None, None, "used"]  # the new refusal, the hub's data, the one let through
