"""Stages 11, 17 and 18 together (2026-11-06, the user's word «проводи общий тест»): what each does when it meets the
others - a goal in «Песочница» with a part applied meanwhile and a dead end after it; a goal's stages with the loop
guard and with two models; a rollback card over the editor's unsaved edits; Claude Code's goal with its edit allowed
in part; the symbol index of a sandbox and of its project.

One passed at first, six showed one part breaking another: each became a step of stage 19 (D53), and each passes
since its step."""

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import Qt

from app.events import TOPICS, EventThread
from common.paths import data_paths
from hub import goals
from hub.api_reference import event_types
from hub.auth import create_client
from hub.claude_code import ClaudeCodeProvider
from hub.events import matches
from hub.projects import get_project
from tests import test_claude_code as cc
from tests.test_autonomous import use
from tests.test_changes import wait
from tests.test_goals import DEAD_END, DONE, WRITE, Gated, chat_with_project, set_goal, settled
from tests.test_tools import hub  # noqa: F401 - the hub fixture

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
