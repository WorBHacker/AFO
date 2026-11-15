"""Stages 11, 17 and 18 together (2026-11-06, the user's word «проводи общий тест»): what each does when it meets the
others - a goal in «Песочница» with a part applied meanwhile and a dead end after it; a goal's stages with the loop
guard and with two models; a rollback card over the editor's unsaved edits; Claude Code's goal with its edit allowed
in part; the symbol index of a sandbox and of its project.

One passed at first, six showed one part breaking another: each became a step of stage 19 (D53), and each passes
since its step."""

import asyncio
import json
import logging
import sqlite3
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
from tests import test_claude_code as cc
from tests.test_agents import delegate, ended, profile, register, run_of
from tests.test_attachments import b64
from tests.test_autonomous import PLAN, Scripted, use
from tests.test_changes import edit, wait
from tests.test_gateway import Stalled, stalled  # noqa: F401 - the stalled fixture
from tests.test_goals import DEAD_END, DONE, WRITE, Cloudy, Gated, chat_with_project, set_goal, settled
from tests.test_llama_config import FakeLlamaSwap
from tests.test_model_install import CLIP, DRAW, MACHINE, FakeHub, fake_hub, tiny_gguf, wait_for  # noqa: F401 - the fake_hub fixture
from tests.test_model_install import hub as installs  # noqa: F401 - the installer's hub, as a fixture of this name
from tests.test_tools import ToolProvider, hub  # noqa: F401 - the hub fixture
from tests.test_tuning import setup, spec_server  # noqa: F401 - the tuning's fixtures

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
    assert waiting["error"] in ("", "ждёт: на видеокарте игра")  # not failed: waiting, and why (48.9б5)
    assert not (waiting.get("trial") or {}).get("tries")  # nothing of it written
    text = lc.config_path().read_text(encoding="utf-8")
    assert "tiny-q4_k_m" in lc.model_blocks(text) and lc.model_view(text, "tiny-q4_k_m")["context"] == planned
    FakeLlamaSwap.fail = False
    time.sleep(1.2)
    again = wait_for(installs, job["id"], "trialwait")  # not tried again beside the game
    assert again["error"] == "ждёт: на видеокарте игра", again["error"]
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
    assert waiting["error"] == "ждёт: на видеокарте игра", waiting["error"]
    state.gpu.state = {**state.gpu.state, "programs": []}  # the game closed
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
