"""Files the hub's models write (stage 5.1, D17): planning a write, keeping it, undoing it, and the whole way through
a chat - asked with a diff, allowed or denied, by the chat's mode, by a rule of the project, stopped while asking."""

import time

import pytest

from hub import changes
from hub.changes import WriteError
from hub.projects import Project
from tests.test_tools import ToolProvider, hub  # noqa: F401 - the hub fixture

SECRET = "sk-ant-api03-" + "w" * 40


def project(root):
    return Project(id="p1", name="proj", path=str(root), description="", cloud_ok=False, created_at="", updated_at="")


def edit(path="src/app.py", old="print('hi')", new="print('привет')", **extra):
    return {"project": "proj", "path": path, "old_text": old, "new_text": new, **extra}


# --- planning, keeping, undoing ---


def test_what_a_write_would_do(repo):
    p = project(repo)
    planned = changes.plan("edit_file", p, edit())
    assert planned.path == "src/app.py" and b"print('\xd0\xbf" in planned.after and planned.before.startswith(b"print")
    new = changes.plan("write_file", p, {"project": "proj", "path": "docs/new.md", "content": "# Новое\n"})
    assert new.before is None and new.target == repo / "docs" / "new.md"
    assert changes.summary(new) == "docs/new.md created: +1 -0 lines"
    (repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    crlf = changes.plan("edit_file", p, edit("crlf.txt", "one\ntwo", "1\n2"))  # the model writes \n
    assert crlf.after == b"1\r\n2\r\nthree\r\n"  # the file's own line ends stay
    (repo / "twice.txt").write_bytes(b"x = 1\nx = 1\n")
    assert (
        changes.plan("edit_file", p, edit("twice.txt", "x = 1", "x = 2", replace_all=True)).after == b"x = 2\nx = 2\n"
    )


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (edit(old="absent"), "is not in src/app.py"),
        (edit(old="print('hj')"), r"The closest lines in it: 1\| print\('hi'\)"),  # a misspelt copy: the real line
        (edit("twice.txt", "x", "y"), "appears 2 times"),
        (edit(new="print('hi')"), "nothing would change"),
        (edit("missing.py"), "does not exist"),
        (edit(".env"), "holds secrets"),
        (edit(".git/config"), "git's own folder"),
        (edit("../outside.txt"), "leaves the project"),
        (edit("src"), "is a folder"),
        (edit("latin1.txt", "caf", "x"), "not UTF-8"),
    ],
)
def test_what_a_write_refuses(repo, args, error):
    (repo / "twice.txt").write_text("x\nx\n", encoding="utf-8")
    (repo / ".env").write_text(f"KEY={SECRET}\n", encoding="utf-8")
    (repo / "latin1.txt").write_bytes("café".encode("latin-1"))
    with pytest.raises(WriteError, match=error):
        changes.plan("edit_file", project(repo), args)


def test_a_change_is_kept_and_can_be_undone(repo, tmp_path):
    p, folder = project(repo), tmp_path / "snapshots"
    planned = changes.plan("edit_file", p, edit())
    before, after = changes.apply(planned, folder)
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('привет')")
    assert (folder / before).read_bytes() == planned.before and (folder / after).read_bytes() == planned.after
    change = {"path": "src/app.py", "before_hash": before, "after_hash": after, "reverted_at": None}
    changes.revert(change, p, folder)
    assert (repo / "src" / "app.py").read_bytes() == planned.before

    stale = changes.plan("edit_file", p, edit())
    (repo / "src" / "app.py").write_text("changed meanwhile\n", encoding="utf-8")
    with pytest.raises(WriteError, match="changed while the user was deciding"):
        changes.apply(stale, folder)

    created = changes.plan("write_file", p, {"project": "proj", "path": "new.txt", "content": "x\n"})
    hashes = changes.apply(created, folder)
    made = {"path": "new.txt", "before_hash": None, "after_hash": hashes[1], "reverted_at": None}
    (repo / "new.txt").write_text("the user's own edit\n", encoding="utf-8")
    with pytest.raises(WriteError, match="has changed since"):
        changes.revert(made, p, folder)
    changes.revert(made, p, folder, force=True)
    assert not (repo / "new.txt").exists()  # it created the file: undoing removes it
    with pytest.raises(WriteError, match="undone already"):
        changes.revert({**made, "reverted_at": "x"}, p, folder)


# --- through a chat ---


def wait(condition, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not (found := condition()):
        assert time.monotonic() < deadline, "condition not met"
        time.sleep(0.02)
    return found


def setup_chat(hub, repo, steps, mode=None):  # noqa: F811 - the fixture's name
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    provider = ToolProvider(steps)
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = new_chat(hub, pid, mode)
    return pid, conv, provider


def new_chat(hub, pid, mode=None):  # noqa: F811
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})
    if mode:
        hub.put(f"/v1/conversations/{conv}/context", json={"mode": mode})
    return conv


def say(hub, conv, text="поправь"):  # noqa: F811
    assert (
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": text, "model_id": "fake/echo"}).status_code
        == 202
    )


def finished(hub, conv):  # noqa: F811
    wait(lambda: not hub.get(f"/v1/conversations/{conv}").json()["busy"])
    return hub.get(f"/v1/conversations/{conv}").json()["messages"]


def result_of(messages):
    return next(m for m in messages if m["role"] == "tool")["content"][0]


def test_a_write_is_asked_and_can_be_undone(hub, repo):  # noqa: F811
    call = {"id": "w1", "name": "edit_file", "input": edit()}
    pid, conv, _ = setup_chat(hub, repo, [[call]])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "edit_file" and asked["kind"] == "write" and asked["project_id"] == pid
    assert "-print('hi')" in asked["preview"]["text"] and "+print('привет')" in asked["preview"]["text"]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True})
    result = result_of(finished(hub, conv))
    assert result["error"] is False and result["output"].startswith("src/app.py changed: +1 -1 lines (change ")
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('привет')")

    (change,) = hub.get(f"/v1/projects/{pid}/changes").json()["changes"]
    assert change["tool"] == "edit_file" and change["approval_id"] == asked["id"] and change["chat"]
    shown = hub.get(f"/v1/changes/{change['id']}").json()
    assert "-print('hi')\n+print('привет')\n" in shown["diff"]
    undone = hub.post(f"/v1/changes/{change['id']}/revert", json={})
    assert undone.status_code == 200 and undone.json()["reverted_at"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('hi')")
    assert hub.post(f"/v1/changes/{change['id']}/revert", json={}).status_code == 409
    feed = [e["type"] for e in hub.get("/v1/feed", params={"types": "file.changed,file.reverted"}).json()["events"]]
    assert feed == ["file.reverted", "file.changed"]


def test_a_denial_reaches_the_model_and_nothing_changes(hub, repo):  # noqa: F811
    pid, conv, _provider = setup_chat(hub, repo, [[{"id": "w1", "name": "edit_file", "input": edit()}]])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "не трогай app.py"})
    result = result_of(finished(hub, conv))
    assert result["error"] is True and result["output"] == "not done: the user said no - не трогай app.py"
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('hi')")
    assert hub.get(f"/v1/projects/{pid}/changes").json()["changes"] == []


def test_the_chats_mode(hub, repo):  # noqa: F811
    write = {"id": "w1", "name": "write_file", "input": {"project": "proj", "path": "out.txt", "content": "x\n"}}
    pid, conv, provider = setup_chat(hub, repo, [[write]], mode="read_only")
    say(hub, conv)
    result = result_of(finished(hub, conv))
    assert result["error"] is True and "read-only" in result["output"] and not (repo / "out.txt").exists()
    assert hub.get("/v1/approvals").json()["approvals"] == []  # refused without asking

    provider.steps.append([write])  # the same write, in a trusted chat
    trusted = new_chat(hub, pid, "trusted")
    say(hub, trusted)
    result = result_of(finished(hub, trusted))
    assert result["error"] is False and (repo / "out.txt").read_text(encoding="utf-8") == "x\n"  # not asked (D17)
    assert hub.put(f"/v1/conversations/{trusted}/context", json={"mode": "root"}).status_code == 422


def test_an_answer_for_the_project_becomes_a_rule(hub, repo):  # noqa: F811
    first = {"id": "w1", "name": "edit_file", "input": edit()}
    second = {"id": "w2", "name": "edit_file", "input": edit(old="print('new line')", new="print('ещё')")}
    pid, conv, provider = setup_chat(hub, repo, [[first]])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "scope": "project"})
    finished(hub, conv)
    (rule,) = hub.get(f"/v1/projects/{pid}/rules").json()["rules"]
    assert rule["grant_key"] == "edit_file" and rule["created_by"] == "app"

    provider.steps.append([second])
    other = new_chat(hub, pid)  # another chat in the same project: the rule answers
    say(hub, other)
    result = result_of(finished(hub, other))
    assert result["error"] is False and "print('ещё')" in (repo / "src" / "app.py").read_text(encoding="utf-8")
    assert hub.delete(f"/v1/rules/{rule['id']}").status_code == 204
    assert hub.get(f"/v1/projects/{pid}/rules").json()["rules"] == []


def test_stopping_while_asking(hub, repo):  # noqa: F811
    _pid, conv, _ = setup_chat(hub, repo, [[{"id": "w1", "name": "edit_file", "input": edit()}]])
    say(hub, conv)
    wait(lambda: hub.get("/v1/approvals").json()["approvals"])
    hub.post(f"/v1/conversations/{conv}/cancel")
    result = result_of(finished(hub, conv))
    assert result["error"] is True and result["output"].startswith("not run")
    assert hub.get("/v1/approvals").json()["approvals"] == []  # the request closed with the run
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('hi')")


# --- the audit (D46): nothing lost on a write ---


def test_a_write_keeps_the_files_mark_and_line_ends(repo):
    """write_file over a Windows file with a byte order mark keeps both; edit_file puts CRLF into what it adds even
    when old_text is one line (it mixed line ends before)."""
    p = project(repo)
    (repo / "win.txt").write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    whole = changes.plan("write_file", p, {"project": "proj", "path": "win.txt", "content": "uno\ndos\ntres\n"})
    assert whole.after == b"\xef\xbb\xbfuno\r\ndos\r\ntres\r\n"
    added = changes.plan("edit_file", p, edit("win.txt", "two", "two\nthree"))  # a one-line old_text
    assert added.after == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"
    (repo / "unix.txt").write_bytes(b"a\nb\n")
    assert changes.plan("write_file", p, {"project": "proj", "path": "unix.txt", "content": "x\r\ny\r\n"}).after \
        == b"x\ny\n"  # fmt: skip


def test_a_write_is_whole_or_not_at_all(tmp_path, monkeypatch):
    """Written beside the file, then put in its place: no half file, no temporary file left; a file another program
    holds for a moment is waited out."""
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    replace, tries = changes.os.replace, []

    def busy_once(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            raise PermissionError(32, "the file is used by another process")
        replace(src, dst)

    monkeypatch.setattr(changes.os, "replace", busy_once)
    changes.write_atomic(target, b"new")
    assert target.read_bytes() == b"new" and len(tries) == 2 and [p.name for p in tmp_path.iterdir()] == ["a.txt"]
    monkeypatch.setattr(changes.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError(32, "held")))
    with pytest.raises(PermissionError):
        changes.write_atomic(target, b"newer")
    assert target.read_bytes() == b"new" and [p.name for p in tmp_path.iterdir()] == ["a.txt"]  # the old one stays


def test_write_file_does_not_overwrite_what_the_model_has_not_read(hub, repo):  # noqa: F811
    """A whole new content over a file the model never read in this chat - or the user changed since - is refused;
    after reading it the write goes through, and the model knows its own write."""
    read = {"id": "r1", "name": "read_file", "input": {"project": "proj", "path": "README.md"}}
    whole = {"id": "w1", "name": "write_file", "input": {"project": "proj", "path": "README.md", "content": "# A\n"}}
    again = {"id": "w2", "name": "write_file", "input": {"project": "proj", "path": "README.md", "content": "# B\n"}}
    _pid, conv, provider = setup_chat(hub, repo, [[whole], [read], [whole], [again]], mode="trusted")
    say(hub, conv)
    results = [b for m in finished(hub, conv) if m["role"] == "tool" for b in m["content"]]
    assert results[0]["error"] and "have not read it in this chat" in results[0]["output"]
    assert not results[2]["error"] and not results[3]["error"]  # read, then written; its own write it knows
    assert (repo / "README.md").read_text(encoding="utf-8") == "# B\n"
    provider.steps += [[whole]]
    (repo / "README.md").write_text("# Правка человека\n", encoding="utf-8")  # the user, meanwhile
    say(hub, conv)
    result = [b for m in finished(hub, conv) if m["role"] == "tool" for b in m["content"]][-1]
    assert result["error"] and "has changed since you read it" in result["output"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Правка человека\n"
