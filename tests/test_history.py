"""A project's files version after version (stage 26, the audit's Local History): whatever changes a file - another
program, a command, Claude Code, git - its content is kept, so the change can be taken back; the hub's own writes come
with their names and the version before them. Not kept: a secret file's content, what git ignores, a big or binary
file, the hub's half-written temporaries. Within D30's term and size, as the other snapshots."""

import asyncio
import time

from hub import changes, history, projects, retention
from hub.auth import create_client
from hub.db import Database, migrate
from hub.watch import ProjectWatcher
from tests.test_retention import DAY, aged
from tests.test_tools import hub  # noqa: F401 - the hub fixture


class Bus:
    async def publish(self, *_args, **_kw):
        pass


async def until(condition, timeout=8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met"
        await asyncio.sleep(0.05)


def watched(root, tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    project = db.call(lambda c: projects.add_project(c, str(root)))
    folder = tmp_path / "snapshots"

    def contents(path):
        found = db.call(lambda c: history.versions(c, folder, project.id, path))
        return [None if v["deleted"] else (folder / v["hash"]).read_bytes() for v in found]

    return db, project, history.History(db, Bus(), folder), contents


def test_every_change_the_watcher_sees_is_kept(repo, tmp_path):
    root = repo.resolve()
    db, project, keeper, contents = watched(root, tmp_path)

    async def scenario():
        watcher = ProjectWatcher(Bus(), flush_s=0.1, keep=keeper.keep)
        watcher.start([project])
        app = root / "src" / "app.py"
        app.write_bytes(b"v1\n")
        await until(lambda: contents("src/app.py") == [b"v1\n"])
        app.write_bytes(b"v2\r\n")
        await until(lambda: contents("src/app.py") == [b"v2\r\n", b"v1\n"])  # newest first, the bytes as they are
        app.write_bytes(b"v2\r\n")  # the same again: no new version

        # not kept: a secret file's content, what git ignores, too big, binary, the hub's half-written temporary
        (root / ".env").write_text("KEY=1234567890abcdef\n", encoding="utf-8")
        (root / "secret.txt").write_text("other token", encoding="utf-8")
        (root / "big.txt").write_bytes(b"x" * (changes.MAX_BYTES + 1))
        (root / "pic.png").write_bytes(b"\x89PNG\0\0data")
        (root / "src" / ".app.py.k3j.localchat").write_bytes(b"half")
        (root / "marker.txt").write_text("m", encoding="utf-8")
        await until(lambda: contents("marker.txt") == [b"m"])
        await asyncio.sleep(0.3)
        for path in (".env", "secret.txt", "big.txt", "pic.png", "src/.app.py.k3j.localchat"):
            assert contents(path) == [], path
        assert contents("src/app.py") == [b"v2\r\n", b"v1\n"]

        app.unlink()  # gone: a version without content, those before it stay
        await until(lambda: contents("src/app.py") == [None, b"v2\r\n", b"v1\n"])
        listed = {f["path"]: f for f in db.call(lambda c: history.files(c, project.id))}
        assert (listed["src/app.py"]["versions"], listed["src/app.py"]["deleted"]) == (3, True)
        assert not listed["marker.txt"]["deleted"]
        await watcher.stop()

    asyncio.run(scenario())
    db.close()


def test_outside_git_build_folders_are_not_kept(tmp_path):
    """Outside git nothing says what is the project's own: the folders the tree hides there (dist, build, ...) are
    not kept either - a build rewrites them every time."""
    root = (tmp_path / "plain").resolve()
    (root / "dist").mkdir(parents=True)
    db, project, keeper, contents = watched(root, tmp_path)

    async def scenario():
        watcher = ProjectWatcher(Bus(), flush_s=0.1, keep=keeper.keep)
        watcher.start([project])
        (root / "dist" / "out.js").write_text("built", encoding="utf-8")
        (root / "main.js").write_text("source", encoding="utf-8")
        await until(lambda: contents("main.js") == [b"source"])
        await asyncio.sleep(0.3)
        assert contents("dist/out.js") == []
        await watcher.stop()

    asyncio.run(scenario())
    db.close()


def test_the_hubs_own_writes_come_with_their_name_and_the_version_before(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]

    def listed(path):
        found = hub.get(f"/v1/projects/{pid}/history", params={"path": path})
        assert found.status_code == 200, found.text
        return found.json()["versions"]

    def wait(condition):
        deadline = time.monotonic() + 8
        while not condition():
            assert time.monotonic() < deadline, "condition not met"
            time.sleep(0.05)

    opened = hub.get(f"/v1/projects/{pid}/file", params={"path": "README.md"}).json()
    saved = hub.put(f"/v1/projects/{pid}/file", json={"path": "README.md", "text": "# Проект\nновая строка\n",
                                                      "base": opened["sha256"]}).json()  # fmt: skip
    found = listed("README.md")  # the user's save, and what the file was before it
    assert [(v["tool"], v["change_id"]) for v in found] == [("save", saved["change"]["id"]), (None, None)]
    assert found[1]["hash"] == saved["change"]["before_hash"] and found[0]["hash"] == saved["change"]["after_hash"]
    (repo / "marker.txt").write_text("m", encoding="utf-8")
    wait(lambda: listed("marker.txt"))
    assert len(listed("README.md")) == 2  # the watcher saw the same write: nothing new

    (repo / "README.md").write_text("# Чужое\n", encoding="utf-8", newline="\n")  # another program
    wait(lambda: len(listed("README.md")) == 3)
    newest, first = listed("README.md")[0], listed("README.md")[-1]
    assert newest["tool"] is None and newest["size"] == len("# Чужое\n".encode())
    shown = hub.get(f"/v1/history/{newest['id']}").json()  # against the version before it
    assert shown["text"] == "# Чужое\n" and shown["encoding"] == "utf-8"
    assert "-новая строка" in shown["diff"] and "+# Чужое" in shown["diff"]
    back = hub.get(f"/v1/history/{first['id']}", params={"against": "current"}).json()  # what taking it back changes
    assert back["text"] == "# Проект\n" and "-# Чужое" in back["diff"] and "+# Проект" in back["diff"]
    assert "+# Проект" in hub.get(f"/v1/history/{first['id']}").json()["diff"]  # the first: all of it is new

    (repo / "notes.txt").unlink()  # the repo's new file, never seen changed: no history, nothing to say
    (repo / "marker.txt").unlink()
    wait(lambda: listed("marker.txt")[0]["deleted"])
    files = {f["path"]: f for f in hub.get(f"/v1/projects/{pid}/history").json()["files"]}
    assert (files["README.md"]["versions"], files["marker.txt"]["deleted"]) == (3, True)
    assert "notes.txt" not in files

    assert hub.get("/v1/history/999999").status_code == 404
    assert hub.get(f"/v1/history/{first['id']}", params={"against": "other"}).status_code == 422
    token = hub.app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
    other = hub.get(f"/v1/projects/{pid}/history", params={"path": "README.md"},
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    assert other.status_code == 403  # the window's alone


def test_versions_live_within_the_term_and_the_size(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    folder = tmp_path / "snapshots"
    for name, days in (("old", 200), ("fresh", 200), ("newer", 1)):
        aged(folder / name, days)

    def write(conn):
        conn.execute("INSERT INTO projects (id, name, path) VALUES ('p', 'P', 'D:/p')")
        for name, days in (("old", 200), ("fresh", 10), ("newer", 0)):
            at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days * DAY))
            conn.execute("INSERT INTO file_versions (project_id, path, hash, size, created_at) VALUES"
                         " ('p', 'a.txt', ?, 100, ?)", (name, at))  # fmt: skip

    db.call(write)

    def kept():
        return [v["hash"] for v in db.call(lambda c: history.versions(c, folder, "p", "a.txt"))]

    assert kept() == ["newer", "fresh", "old"]
    removed = db.call(lambda c: retention.prune_snapshots(c, folder, days=90, max_bytes=10**9))
    assert removed == 1 and kept() == ["newer", "fresh"]  # past the term: the row and its content
    assert sorted(p.name for p in folder.iterdir()) == ["fresh", "newer"]  # «fresh» is as young as its version
    removed = db.call(lambda c: retention.prune_snapshots(c, folder, days=90, max_bytes=150))
    assert removed == 1 and kept() == ["newer"]  # over the size: its content went, so it is not offered
    db.close()


def test_a_version_is_taken_back_the_way_a_save_goes(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]

    def listed():
        return hub.get(f"/v1/projects/{pid}/history", params={"path": "old.txt"}).json()["versions"]

    def wait(condition):
        deadline = time.monotonic() + 8
        while not condition():
            assert time.monotonic() < deadline, "condition not met"
            time.sleep(0.05)

    first = "привет\r\nмир\r\n".encode("cp1251")  # another program's: cp1251, Windows line ends
    (repo / "old.txt").write_bytes(first)
    wait(lambda: len(listed()) == 1)
    (repo / "old.txt").write_bytes("пока\n".encode())
    wait(lambda: len(listed()) == 2)
    old, now = listed()[1], listed()[0]

    def restore(version, base):
        return hub.post(f"/v1/history/{version['id']}/restore", json={"base": base})

    stale = restore(old, "0" * 64)  # the file is not what the user saw
    assert stale.status_code == 409 and stale.json()["detail"]["reason"] == "changed"
    assert stale.json()["detail"]["file"]["text"] == "пока\n"
    assert restore(now, now["hash"]).status_code == 422  # it is this version already

    back = restore(old, now["hash"])
    assert back.status_code == 200, back.text
    assert (repo / "old.txt").read_bytes() == first  # its own bytes: the encoding and the line ends as they were
    result = back.json()
    assert (result["change"]["tool"], result["created"], result["restored"]) == ("restore", False, old["id"])
    assert listed()[0]["tool"] == "restore"
    feed = hub.get("/v1/feed", params={"types": "file.saved"}).json()["events"]
    assert feed[-1]["payload"]["restored"] == old["id"]
    assert hub.post(f"/v1/changes/{result['change']['id']}/revert", json={}).status_code == 200  # undone as any
    assert (repo / "old.txt").read_bytes() == "пока\n".encode()

    (repo / "old.txt").unlink()
    wait(lambda: listed()[0]["deleted"])
    assert restore(listed()[0], "").status_code == 422  # the file deleted is nothing to take back
    again = restore(old, "")  # a deleted file comes back
    assert again.status_code == 200 and again.json()["created"] and (repo / "old.txt").read_bytes() == first
    token = hub.app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
    other = hub.post(f"/v1/history/{old['id']}/restore", json={"base": ""},
                     headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    assert other.status_code == 403
