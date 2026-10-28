"""Watching projects (stage 2): sorting raw file events, and a real watcher on a temporary repository."""

import asyncio
import os

from conftest import run_git
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings
from hub.watch import ProjectWatcher, head_change, relative, sort_event

ROOT = os.path.normcase("C:\\proj")


def sort(kind, src, dest="", **kw):
    batch = sort_event(ROOT, kind, src, dest, **kw)
    return sorted(batch.changed), sorted(batch.folders), batch.git


# --- sorting events (no file system) ---


def test_relative_paths():
    assert relative(ROOT, "C:\\proj\\src\\a.py") == "src/a.py"
    assert relative(ROOT, "C:\\PROJ\\src\\a.py") == "src/a.py"  # Windows paths ignore case
    assert relative(ROOT, "C:\\proj") == ""
    assert relative(ROOT, "C:\\project2\\a.py") is None  # a neighbour that starts the same way
    assert relative(ROOT, "C:\\proj\\data\\hub.db", [os.path.normcase("C:\\proj\\data")]) is None


def test_sorting_events():
    assert sort("modified", "C:\\proj\\src\\a.py") == (["src/a.py"], [], False)
    assert sort("modified", "C:\\proj\\src", is_dir=True) == ([], [], False)  # its child reports itself
    assert sort("created", "C:\\proj\\src\\b.py") == (["src/b.py"], ["src"], False)
    assert sort("created", "C:\\proj\\docs", is_dir=True) == ([], [""], False)
    assert sort("deleted", "C:\\proj\\README.md") == (["README.md"], [""], False)
    assert sort("moved", "C:\\proj\\a.txt", "C:\\proj\\src\\a.txt") == (["a.txt", "src/a.txt"], ["", "src"], False)
    assert sort("opened", "C:\\proj\\a.txt") == ([], [], False)
    # caches are noise, git's own files only matter when they move its state
    assert sort("created", "C:\\proj\\node_modules\\x\\y.js") == ([], [], False)
    assert sort("modified", "C:\\proj\\src\\__pycache__\\a.pyc") == ([], [], False)
    assert sort("modified", "C:\\proj\\.git\\index") == ([], [], True)
    assert sort("modified", "C:\\proj\\.git\\refs\\heads\\main") == ([], [], True)
    assert sort("created", "C:\\proj\\.git\\objects\\ab\\cdef") == ([], [], False)
    assert sort("created", "C:\\proj\\.git\\index.lock") == ([], [], False)


def test_what_moved_head():
    def state(branch, *shas):
        return {"repo": True, "branch": branch, "log": [{"sha": s, "subject": f"s{s}", "author": "A"} for s in shas]}

    assert head_change(None, state("main", "b")) is None  # nothing to compare with yet
    assert head_change(state("main", "a"), state("main", "a")) is None
    assert head_change(state("main", "a"), state("main", "b", "a"))["kind"] == "commit"
    assert head_change(state("main", "b", "a"), state("main", "a"))["kind"] == "moved"  # a reset
    switched = head_change(state("main", "a"), state("dev", "c"))
    assert (switched["kind"], switched["from"], switched["branch"], switched["subject"]) == (
        "branch",
        "main",
        "dev",
        "sc",
    )
    assert head_change({"repo": False}, state("main", "a")) is None


# --- a real watcher ---


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
        assert persist is (event_type == "git.head")  # only a moved HEAD goes into the log
        self.events.append((event_type, payload))


async def until(condition, timeout=8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met"
        await asyncio.sleep(0.05)


def project(root):
    return Project(id="p1", name="proj", path=str(root), description="", cloud_ok=False, created_at="", updated_at="")


def test_watching_a_repository(repo, tmp_path):
    root = repo.resolve()
    (root / "data").mkdir()

    async def scenario():
        bus = Bus()
        watcher = ProjectWatcher(bus, ignore=[root / "data"], flush_s=0.1)
        watcher.start([project(root)])
        assert watcher.watched == {"p1"}

        def files():
            return [p for t, p in bus.events if t == "project.files"]

        def gits():
            return [p for t, p in bus.events if t == "project.git"]

        def heads():
            return [p for t, p in bus.events if t == "git.head"]

        (root / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
        await until(lambda: any("src/new.py" in p["changed"] for p in files()))
        assert any("src" in p["folders"] for p in files())
        await until(lambda: any(c["path"] == "src/new.py" for g in gits() for c in g["changes"]))
        assert gits()[-1]["branch"] == "main" and gits()[-1]["log"][0]["subject"] == "first"

        # ignored by git, a cache, the hub's own data: nothing to report
        bus.events.clear()
        (root / "secret.txt").write_text("other token", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "x.js").write_text("1", encoding="utf-8")
        (root / "data" / "hub.db").write_bytes(b"db")
        (root / "README.md").write_text("# Проект!\n", encoding="utf-8")  # a marker that does get through
        await until(lambda: any("README.md" in p["changed"] for p in files()))
        await asyncio.sleep(0.4)
        reported = {path for p in files() for path in p["changed"] + p["folders"]}
        assert reported <= {"README.md", ""}, reported

        # a commit moves git's state: the new commit comes as an event
        await asyncio.to_thread(run_git, root, "add", ".")
        await asyncio.to_thread(run_git, root, "commit", "-q", "-m", "second")
        await until(lambda: gits() and gits()[-1]["log"][0]["subject"] == "second")
        assert gits()[-1]["changes"] == []
        await until(lambda: heads())
        assert heads()[-1] | {"sha": ""} == {"project_id": "p1", "kind": "commit", "branch": "main", "sha": "",
                                              "subject": "second", "author": "Test"}  # fmt: skip
        await asyncio.to_thread(run_git, root, "checkout", "-q", "-b", "feature")
        await until(lambda: len(heads()) == 2)
        assert (heads()[-1]["kind"], heads()[-1]["from"], heads()[-1]["branch"]) == ("branch", "main", "feature")
        await asyncio.sleep(0.8)  # the checkout's last file events settle first
        count = len(gits())
        await watcher.refresh_git("p1")  # nothing changed since: no new event
        assert len(gits()) == count

        watcher.unwatch("p1")
        bus.events.clear()
        (root / "after.txt").write_text("x", encoding="utf-8")
        await asyncio.sleep(0.6)
        assert bus.events == []
        await watcher.stop()

    asyncio.run(scenario())


def test_a_missing_folder_is_not_watched(tmp_path):
    async def scenario():
        watcher = ProjectWatcher(Bus())
        watcher.start([project(tmp_path / "gone")])
        assert watcher.watched == set()
        await watcher.stop()

    asyncio.run(scenario())


def hub():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    return app, client


def test_the_hub_watches_projects_while_they_are_listed(repo):
    app, client = hub()
    with client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        assert app.state.watcher.watched == {pid}
        subscription = app.state.bus.subscribe(("project.*",))

        async def next_type():
            return (await asyncio.wait_for(subscription.queue.get(), 8)).type

        (repo / "src" / "live.py").write_text("y = 2\n", encoding="utf-8")
        seen = set()
        while not {"project.files", "project.git"} <= seen:
            seen.add(client.portal.call(next_type))
    app, client = hub()  # after a restart the hub watches what is listed
    with client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        assert app.state.watcher.watched == {pid}
        assert client.delete(f"/v1/projects/{pid}").status_code == 204
        assert app.state.watcher.watched == set()


def test_a_projects_folder_renamed_gone_and_back(tmp_path):
    """2026-10-28 (a debt of 5.4): a project's folder renamed on the disk is found again by its file id - the path
    follows it and watching goes on; deleted - not watched, said once; back - watched again."""
    import dataclasses
    import shutil

    class Recorder:
        def __init__(self):
            self.events = []

        async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
            self.events.append((event_type, payload, persist))

        def kinds(self):
            return [e[0] for e in self.events]

    folder = tmp_path / "proj"
    (folder / "src").mkdir(parents=True)
    (folder / "a.txt").write_text("a", encoding="utf-8")
    listed = [project(folder)]

    async def relocate(project_id, new):
        listed[0] = dataclasses.replace(listed[0], path=str(new.resolve()))
        return listed[0]

    async def scenario():
        bus = Recorder()
        watcher = ProjectWatcher(bus, relocate=relocate)  # no check_s: the test checks itself
        watcher.start(list(listed))
        assert watcher.watched == {"p1"}
        renamed = tmp_path / "proj-renamed"
        folder.rename(renamed)
        await watcher.check(list(listed))
        moved = [e for e in bus.events if e[0] == "project.moved"]
        assert len(moved) == 1 and moved[0][2] is True  # stored: the feed's
        assert moved[0][1]["from"] == str(folder) and moved[0][1]["to"] == str(renamed.resolve())
        assert listed[0].path == str(renamed.resolve()) and watcher.watched == {"p1"}
        (renamed / "new.txt").write_text("x", encoding="utf-8")  # watched at its new place
        await until(lambda: "project.files" in bus.kinds())
        shutil.rmtree(renamed)
        await watcher.check(list(listed))
        await watcher.check(list(listed))  # said once
        assert bus.kinds().count("project.missing") == 1 and watcher.watched == set()
        renamed.mkdir()
        await watcher.check(list(listed))
        assert "project.found" in bus.kinds() and watcher.watched == {"p1"}
        await watcher.stop()

    asyncio.run(scenario())


def test_a_deleted_folders_handle_is_closed_once(monkeypatch, tmp_path):
    """watchdog 6 closed a deleted folder's handle twice (when it went, and when unscheduled): the second CloseHandle
    hit a reused number and the hub fell. The hub's emitter closes it once."""
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("the Windows emitter")
    import watchdog.observers.winapi as winapi

    from hub import watch

    closed = []
    monkeypatch.setattr(winapi, "close_directory_handle", closed.append)  # no real CloseHandle in a test
    observer = watch._observer()
    emitter = observer._emitter_class(observer.event_queue, watch.ObservedWatch(str(tmp_path), recursive=True))
    emitter._whandle = 1234
    emitter.on_thread_stop()
    emitter.on_thread_stop()
    assert closed == [1234]
