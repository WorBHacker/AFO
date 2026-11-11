"""Terminals in the hub (stage 2): ConPTY consoles with live output and logs, the API and its rights, the end of
terminals with the hub, and keeping logs within their limits (D30)."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import psutil
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.auth import create_client
from hub.db import Database, migrate
from hub.processes import stop_tree
from hub.server import create_app
from hub.settings import HubSettings
from hub.terminals import TerminalError, TerminalManager, find_shell, prune_logs, shell_args


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
        self.events.append((event_type, payload, persist))

    def of(self, event_type):
        return [p for t, p, _ in self.events if t == event_type]

    def output(self, terminal_id):
        return "".join(p["data"] for p in self.of("terminal.output") if p["terminal_id"] == terminal_id)


async def until(condition, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met"
        await asyncio.sleep(0.02)


@pytest.fixture
def db():
    database = Database(data_paths().ensure().db)
    database.call(migrate)
    yield database
    database.close()


def test_shells():
    assert find_shell("cmd")[1].lower().endswith("cmd.exe")
    with pytest.raises(TerminalError):
        find_shell("no-such-shell")
    assert shell_args("cmd", "dir") == ["/d", "/c", "dir"]
    assert shell_args("powershell", None) == ["-NoLogo"]
    assert shell_args("pwsh", "Get-Date") == ["-NoLogo", "-Command", "Get-Date"]


def test_a_command_terminal(db, tmp_path):
    async def scenario():
        bus = Bus()
        terminals = TerminalManager(bus, db, data_paths().terminals)
        await terminals.start(prune=False)
        info = await terminals.create(cwd=tmp_path, by="app", command="echo привет & exit 3", shell="cmd")
        tid = info["id"]
        assert info["status"] == "running" and info["title"] == "echo привет & exit 3"
        await until(lambda: bus.of("terminal.exited"))
        (ended,) = bus.of("terminal.exited")
        assert ended["exit_code"] == 3 and ended["stopped"] is False
        assert "привет" in bus.output(tid)
        assert [persist for t, _, persist in bus.events if t != "terminal.output"] == [True, True]  # in the log
        row = await terminals.get(tid)
        assert row["status"] == "exited" and row["exit_code"] == 3 and row["log_bytes"] > 0
        assert "привет" in terminals.log_path(tid).read_text(encoding="utf-8")
        assert "привет" in (await terminals.output(tid))["data"]  # from the log now
        await terminals.stop()

    asyncio.run(scenario())


def test_an_interactive_terminal(db, tmp_path):
    async def scenario():
        bus = Bus()
        terminals = TerminalManager(bus, db, data_paths().terminals)
        await terminals.start(prune=False)
        tid = (await terminals.create(cwd=tmp_path, by="app", shell="cmd", cols=100, rows=30))["id"]
        await terminals.write(tid, "echo marker-42\r\n")
        await until(lambda: bus.output(tid).count("marker-42") >= 2)  # the command typed, and what it printed
        tail = await terminals.output(tid)
        assert tail["running"] and "marker-42" in tail["data"]
        offsets = bus.of("terminal.output")
        assert tail["end"] == offsets[-1]["offset"] + len(offsets[-1]["data"])  # events and tail agree
        await terminals.resize(tid, 90, 25)
        assert (await terminals.get(tid))["cols"] == 90
        await terminals.kill(tid)
        await until(lambda: bus.of("terminal.exited"))
        assert bus.of("terminal.exited")[0]["stopped"] is True
        with pytest.raises(TerminalError, match="not running"):
            await terminals.write(tid, "x")
        await terminals.remove(tid)
        assert not terminals.log_path(tid).exists()
        with pytest.raises(TerminalError, match="no such terminal"):
            await terminals.get(tid)
        await terminals.stop()

    asyncio.run(scenario())


def running(pid):
    return psutil.pid_exists(pid) and psutil.Process(pid).is_running()


def test_no_terminal_outlives_the_hub_stopping(db, tmp_path):
    """The hub stops while a model's command starts its terminal, or asks for one after (2026-11-11). stop() ended
    the terminals it knew: one starting meanwhile became live after it, one asked for later started as ever - and
    their programs outlived the hub, with nobody to end them. Now the one starting ends with the rest, and none
    starts once the hub stops."""
    long = "ping -n 30 127.0.0.1 > nul"

    async def scenario():
        terminals = TerminalManager(Bus(), db, data_paths().terminals)
        await terminals.start(prune=False)
        starting = asyncio.create_task(terminals.create(cwd=tmp_path, by="model", command=long, shell="cmd"))
        await asyncio.sleep(0)  # it has begun: its program is being started
        await terminals.stop()
        started = await starting  # it became live before the stop took hold
        assert (await terminals.get(started["id"]))["status"] == "exited"  # and ended with the rest
        assert not running(started["pid"])
        with pytest.raises(TerminalError, match="the hub is stopping"):
            await terminals.create(cwd=tmp_path, by="model", command=long, shell="cmd")
        assert len(await terminals.list()) == 1

    try:
        asyncio.run(scenario())
    finally:  # what got away from the hub, ended here
        for row in db.call(lambda conn: conn.execute("SELECT pid FROM terminals").fetchall()):
            if running(row["pid"]):
                stop_tree(row["pid"], 2.0)


def test_a_flood_of_output_wakes_the_hub_rarely(db, tmp_path, monkeypatch):
    """ConPTY hands output over a line or two at a time: each piece flushed the log and woke the event loop - 55 000
    times for 200 000 lines, most of the hub's CPU (D46, step 13.7). Pieces read back to back go on as one."""
    lines = 20_000
    (tmp_path / "chatty.py").write_text(f"for i in range({lines}):\n    print('строка', i)\n", encoding="utf-8")
    woken = []
    queue = TerminalManager._queue_output
    monkeypatch.setattr(TerminalManager, "_queue_output",
                        lambda self, *args: (woken.append(1), queue(self, *args)))  # fmt: skip

    async def scenario():
        bus = Bus()
        terminals = TerminalManager(bus, db, data_paths().terminals)
        await terminals.start(prune=False)
        command = f"& '{sys.executable}' chatty.py"
        tid = (await terminals.create(cwd=tmp_path, by="app", command=command, shell="powershell"))["id"]
        await until(lambda: bus.of("terminal.exited"), timeout=60)
        assert f"строка {lines - 1}" in bus.output(tid)
        assert f"строка {lines - 1}" in terminals.log_path(tid).read_text(encoding="utf-8")
        await terminals.stop()

    asyncio.run(scenario())
    assert len(woken) < lines / 50, len(woken)


def hub_app():
    settings = HubSettings(
        paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0
    )
    return create_app(settings)


def test_terminals_api_and_the_end_with_the_hub(repo):
    app = hub_app()
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        cline = app.state.db.call(lambda c: create_client(c, "cline"))
        other = {"Authorization": f"Bearer {cline}"}
        assert client.post("/v1/terminals", json={"project_id": pid}, headers=other).status_code == 403
        assert client.post("/v1/terminals", json={"project_id": pid, "path": "../x"}).status_code == 400
        assert client.post("/v1/terminals", json={"project_id": "nope"}).status_code == 404

        started = client.post("/v1/terminals", json={"project_id": pid, "path": "src", "shell": "cmd"})
        assert started.status_code == 201
        terminal = started.json()
        assert terminal["cwd"].endswith("src") and terminal["created_by"] == "app"
        tid = terminal["id"]
        assert (
            client.post(f"/v1/terminals/{tid}/input", json={"data": "echo from-api\r\n"}, headers=other).status_code
            == 403
        )
        assert client.post(f"/v1/terminals/{tid}/input", json={"data": "echo from-api\r\n"}).status_code == 204
        # a terminal's output may hold anything typed in it: over REST it is the window's (D46); MCP gives it filtered
        assert client.get(f"/v1/terminals/{tid}/output", headers=other).status_code == 403
        for _ in range(300):
            if client.get(f"/v1/terminals/{tid}/output").json()["data"].count("from-api") >= 2:
                break
            client.portal.call(asyncio.sleep, 0.05)
        else:
            pytest.fail("no output")
        whole = client.get(f"/v1/terminals/{tid}/output").json()
        assert whole["start"] + len(whole["data"]) == whole["end"]
        # a window that missed events reads from where it stopped (D46): the same characters, from that offset on
        since = whole["end"] - 5
        part = client.get(f"/v1/terminals/{tid}/output", params={"since": since}).json()
        assert part["start"] == since and part["data"][:5] == whole["data"][-5:]
        later = client.get(f"/v1/terminals/{tid}/output", params={"since": whole["end"] + 10**6}).json()
        assert later["start"] != whole["end"] + 10**6  # not there: the last output, and `start` says from where
        listed = client.get("/v1/terminals", params={"project_id": pid}).json()["terminals"]
        assert [t["id"] for t in listed] == [tid] and listed[0]["status"] == "running"
        assert client.delete(f"/v1/terminals/{tid}").status_code == 409  # still running
        shell_pid = terminal["pid"]
    # the hub stopped: its terminal ended with it
    assert not psutil.pid_exists(shell_pid) or not psutil.Process(shell_pid).is_running()
    app.state.db = Database(data_paths().db)
    row = app.state.db.call(lambda c: c.execute("SELECT status FROM terminals WHERE id = ?", (tid,)).fetchone())
    assert row["status"] == "exited"
    # one that was running when a hub died is 'lost' after the next start
    app.state.db.call(lambda c: c.execute("UPDATE terminals SET status = 'running' WHERE id = ?", (tid,)))
    app.state.db.close()
    app = hub_app()
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        assert client.get(f"/v1/terminals/{tid}").json()["status"] == "lost"
        assert client.delete(f"/v1/terminals/{tid}").status_code == 204
        assert client.get(f"/v1/terminals/{tid}").status_code == 404


def test_logs_are_kept_within_limits(db, tmp_path):
    now = datetime(2026, 10, 27, tzinfo=UTC)

    def add(terminal_id, status, days_ago, size):
        at = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        db.call(
            lambda c: c.execute(
                "INSERT INTO terminals (id, command, cwd, cols, rows, status, created_by, created_at, ended_at)"
                " VALUES (?, 'cmd', 'C:\\', 80, 24, ?, 'app', ?, ?)",
                (terminal_id, status, at, at if status != "running" else None),
            )
        )
        (tmp_path / f"{terminal_id}.log").write_bytes(b"x" * size)

    add("old", "exited", 100, 10)
    add("lost", "lost", 50, 400)
    add("mid", "exited", 20, 400)
    add("new", "exited", 1, 400)
    add("live", "running", 200, 400)  # running: never pruned, however old
    removed = db.call(lambda c: prune_logs(c, tmp_path, keep_days=90, max_total_bytes=1000, now=now))
    assert removed == 3  # too old, then the oldest until the rest fits
    assert sorted(p.stem for p in tmp_path.glob("*.log")) == ["live", "new"]
    deleted = db.call(lambda c: [r["id"] for r in c.execute("SELECT id FROM terminals WHERE log_deleted = 1")])
    assert sorted(deleted) == ["lost", "mid", "old"]
    assert db.call(lambda c: prune_logs(c, tmp_path, keep_days=90, max_total_bytes=1000, now=now)) == 0
