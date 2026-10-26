"""Processes and ports in project folders (stage 2): which project owns a process, the tree, a real server found
with its port and stopped through the API."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.auth import create_client
from hub.processes import (
    ProcessMonitor,
    ProcessScanner,
    as_tree,
    owner,
    port_changes,
    roots_of,
    stop_tree,
    without_launchers,
)
from hub.server import create_app
from hub.settings import HubSettings

# a small server: listens on a free port, starts a child, says which port and its own pid (a virtual environment's
# python.exe is a launcher: the pid that holds the port is the real interpreter's), and waits
SERVER = (
    "import os, socket, subprocess, sys, time\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(s.getsockname()[1], os.getpid(), flush=True)\n"
    "time.sleep(60)\n"
)


def test_which_project_owns_a_process():
    roots = roots_of([SimpleNamespace(id="outer", path="C:\\work"), SimpleNamespace(id="inner", path="C:\\work\\api")])
    assert owner(roots, "C:\\work\\api\\src") == ("inner", "src")  # the deepest project wins
    assert owner(roots, "C:\\WORK\\web") == ("outer", "web")  # Windows paths ignore case
    assert owner(roots, "C:\\work") == ("outer", "")
    assert owner(roots, "C:\\workshop\\x") is None  # a neighbour that starts the same way
    assert owner(roots, "", "C:\\work\\.venv\\python.exe") == ("outer", ".venv/python.exe")  # by its program


def test_processes_as_a_tree():
    def p(pid, ppid, at):
        return {"pid": pid, "ppid": ppid, "started_at": at}

    tree = as_tree([p(3, 2, "c"), p(2, 1, "b"), p(9, 100, "a"), p(4, 2, "d")])
    assert [(x["pid"], x["depth"]) for x in tree] == [(9, 0), (2, 0), (3, 1), (4, 1)]


def test_a_venv_launcher_and_its_interpreter_are_one_process():
    def p(pid, ppid, name, args, ports=()):
        return {"pid": pid, "ppid": ppid, "name": name, "args": args, "ports": list(ports)}

    found = without_launchers(
        [
            p(1, 0, "bash.exe", "-c x"),
            p(2, 1, "python.exe", "-m hub"),  # the launcher
            p(3, 2, "python.exe", "-m hub", [8765]),  # the interpreter
            p(4, 3, "python.exe", "-c y"),  # a child of the interpreter: a launcher too
            p(5, 4, "python.exe", "-c y"),
            p(6, 1, "python.exe", "-m other"),  # alone: stays
        ]
    )
    assert [(x["pid"], x["ppid"]) for x in found] == [(1, 0), (3, 1), (5, 3), (6, 1)]


@pytest.fixture
def server(repo):
    process = subprocess.Popen([sys.executable, "-c", SERVER], cwd=repo, stdout=subprocess.PIPE, text=True)
    port, real = (int(v) for v in process.stdout.readline().split())
    yield SimpleNamespace(process=process, port=port, pid=real)
    stop_tree(process.pid, wait_s=2)


def test_a_server_in_a_project_is_found_with_its_port(repo, server):
    project = SimpleNamespace(id="p1", path=str(repo.resolve()))
    scanner = ProcessScanner(own_pid=server.pid)  # pretend the server is the hub: it is marked
    scanner.scan([project])
    found = scanner.scan([project])["p1"]
    by_pid = {p["pid"]: p for p in found}
    row = by_pid[server.pid]
    assert server.port in row["ports"] and row["hub"] and row["cwd"] == ""
    assert row["args"].startswith("-c ") and "socket" in row["args"]
    assert server.process.pid not in by_pid  # the launcher is folded into its interpreter
    assert any(p["depth"] > row["depth"] for p in found)  # the child, under it
    assert all(p["memory_mb"] > 0 for p in found)
    assert scanner.scan([]) == {}


def test_stopping_a_project_process(repo, server):
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        processes = client.get("/v1/processes").json()["projects"][pid]
        assert any(server.port in p["ports"] for p in processes)

        cline = app.state.db.call(lambda c: create_client(c, "cline"))
        other = {"Authorization": f"Bearer {cline}"}
        assert client.post(f"/v1/processes/{server.pid}/stop", headers=other).status_code == 403
        assert client.post(f"/v1/processes/{os.getpid()}/stop").status_code == 404  # not in any project

        stopped = client.post(f"/v1/processes/{server.pid}/stop").json()
        assert stopped["stopped"] and len(stopped["pids"]) >= 2  # with its child
        assert server.process.wait(10) is not None  # the launcher ends with its interpreter
        assert client.post(f"/v1/processes/{server.pid}/stop").status_code == 404
        logged = app.state.db.call(
            lambda c: c.execute("SELECT payload FROM events WHERE type = 'process.stopped'").fetchall()
        )
        assert len(logged) == 1 and '"by": "app"' in logged[0]["payload"]


def test_ports_that_open_and_close_go_into_the_log():
    def proc(pid, ports, hub=False):
        return {"pid": pid, "name": f"p{pid}", "args": "-x", "ports": ports, "hub": hub}

    before = {"a": [proc(1, [8000]), proc(9, [8765], hub=True)]}
    after = {"a": [proc(1, [8000, 8001]), proc(9, [8766], hub=True)], "b": [proc(2, [3000])]}
    assert [(k, p["project_id"], p["port"]) for k, p in port_changes(before, after)] == [
        ("port.opened", "a", 8001),
        ("port.opened", "b", 3000),
    ]  # the hub's own ports are not news
    assert [(k, p["port"]) for k, p in port_changes(after, before)] == [("port.closed", 8001), ("port.closed", 3000)]

    class Scanner:
        def __init__(self):
            self.views = [{"a": [proc(1, [8000])]}, {"a": [proc(1, [8000, 9000])]}, {"a": [proc(1, [8000])]}]

        def scan(self, projects):
            return self.views.pop(0)

    class Bus:
        def __init__(self):
            self.events = []

        async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
            self.events.append((event_type, payload.get("port"), persist))

    class Db:
        async def acall(self, fn):
            return []

    async def scenario():
        bus = Bus()
        monitor = ProcessMonitor(bus, Db(), interval_s=0, scanner=Scanner())
        for _ in range(3):
            await monitor.sample()
        return [e for e in bus.events if e[0] != "processes.changed"]

    # the first scan is where things stand: port 8000 was already open
    assert asyncio.run(scenario()) == [("port.opened", 9000, True), ("port.closed", 9000, True)]
