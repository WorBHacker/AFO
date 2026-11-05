"""Files the hub's models write (stage 5.1, D17): planning a write, keeping it, undoing it, and the whole way through
a chat - asked with a diff, allowed or denied, by the chat's mode, by a rule of the project, stopped while asking."""

import time

import pytest

from hub import changes
from hub.api import events_ws
from hub.auth import create_client
from hub.changes import WriteError
from hub.projects import Project
from tests.test_tools import WS_HOST, ToolProvider, hub  # noqa: F401 - the hub fixture

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


# --- a write allowed in part: its hunks (stage 17.2, D51) ---


def test_hunks_and_a_file_rebuilt_from_some_of_them(repo):
    """The hunks of a write are those its unified diff shows, in that order; the file rebuilt from some of them has
    the model's lines in those and its own in the rest - with its byte order mark and Windows line ends."""
    old = "\ufeff" + "".join(f"строка {n}\r\n" for n in range(1, 41))
    (repo / "long.txt").write_bytes(old.encode("utf-8"))
    model = "".join(f"строка {n}\n" for n in range(1, 41))
    model = model.replace("строка 10\n", "строка 10, правка\n").replace("строка 20\n", "")
    model = model.replace("строка 38\n", "строка 38\nновая\n")  # a change, a deletion, an addition
    planned = changes.plan("write_file", project(repo), {"project": "proj", "path": "long.txt", "content": model})
    found = changes.hunks(planned)
    shown = changes.diff(planned.path, planned.before, planned.after)
    assert [h["header"] for h in found] == [line for line in shown.splitlines() if line.startswith("@@")]
    assert [h["index"] for h in found] == [0, 1, 2] and "-строка 20\r\n" in found[1]["text"]
    assert changes.hunks_diff(planned, found) == shown  # the card's diff, made of the hunks

    part = changes.compose(planned, {0, 2})
    text = part.after.decode("utf-8")
    assert part.after.startswith(b"\xef\xbb\xbf") and "\n" not in text.replace("\r\n", "")  # its mark, its line ends
    assert "строка 10, правка\r\n" in text and "строка 20\r\n" in text and "строка 38\r\nновая\r\n" in text
    assert text.startswith("\ufeffстрока 1\r\nстрока 2\r\n")  # all of the start, before the first hunk's context
    assert (part.before, part.target) == (planned.before, planned.target)
    assert changes.compose(planned, {0, 1, 2}).after == planned.after
    assert changes.compose(planned, set()).after == planned.before


def test_what_the_model_is_told_of_the_hunks_left_out(repo):
    code = "def first():\n    a = 1\n    return a\n" + "\n" * 8 + "class Box:\n    def second(self):\n        b = 1\n"
    (repo / "two.py").write_text(code, encoding="utf-8", newline="\n")
    args = {"project": "proj", "path": "two.py", "old_text": "= 1", "new_text": "= 2", "replace_all": True}
    planned = changes.plan("edit_file", project(repo), args)
    assert len(changes.hunks(planned)) == 2
    assert changes.left_out(planned, {0}) == ["line 14 (in Box.second)"]
    assert changes.left_out(planned, {1}) == ["line 2 (in first)"]
    added = changes.plan("write_file", project(repo), {"project": "proj", "path": "two.py",
                                                       "content": "# top\n" + code + "x = 0\ny = 0\n"})  # fmt: skip
    assert changes.left_out(added, set()) == ["before line 1", "after line 14"]


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


def test_a_write_allowed_in_part(hub, repo):  # noqa: F811
    """17.2 (D51): the card's request carries the write's hunks; the user allows some of them, this once. The file
    gets those; the model is told what was left out and why, and may not write the whole file again before it
    reads it: the hub does not count it as knowing the result."""
    (repo / "conf.py").write_text("".join("x = 1\n" if n in (1, 20, 40) else f"# {n}\n" for n in range(1, 41)),
                                  encoding="utf-8", newline="\n")  # fmt: skip
    read = {"id": "r1", "name": "read_file", "input": {"project": "proj", "path": "conf.py"}}
    every = {"id": "w1", "name": "edit_file", "input": edit(path="conf.py", old="x = 1", new="x = 2", replace_all=True)}
    whole = {"id": "w2", "name": "write_file", "input": {"project": "proj", "path": "conf.py", "content": "x = 3\n"}}
    pid, conv, _ = setup_chat(hub, repo, [[read], [every], [whole]])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    hunks = asked["preview"]["hunks"]
    assert [h["index"] for h in hunks] == [0, 1, 2] and all(h["header"] in asked["preview"]["text"] for h in hunks)
    url = f"/v1/approvals/{asked['id']}"
    assert hub.post(url, json={"allow": True, "hunks": []}).status_code == 409  # none: that is a «no»
    assert hub.post(url, json={"allow": True, "hunks": [0, 3]}).status_code == 409  # no hunk 3
    assert hub.post(url, json={"allow": True, "hunks": [0], "scope": "chat"}).status_code == 409  # a part: once
    assert hub.post(url, json={"allow": False, "hunks": [0]}).status_code == 409
    decided = hub.post(url, json={"allow": True, "hunks": [2, 0], "comment": "x на 20-й строке не трогай"})
    assert decided.status_code == 200 and decided.json()["hunks"] == [0, 2]
    results = [m["content"][0] for m in finished(hub, conv) if m["role"] == "tool"]
    said = results[1]["output"]
    assert results[1]["error"] is False and said.startswith("conf.py: 2 of 3 hunks applied, as the user chose")
    assert "left out - the file keeps its own text there: line 20." in said and "x на 20-й строке не трогай" in said
    lines = (repo / "conf.py").read_text(encoding="utf-8").splitlines()
    assert (lines[0], lines[19], lines[39]) == ("x = 2", "x = 1", "x = 2")
    assert results[2]["error"] is True and "has changed since you read it" in results[2]["output"]
    (change,) = hub.get(f"/v1/projects/{pid}/changes").json()["changes"]
    assert change["approval_id"] == asked["id"]  # kept like any change: it can be undone
    assert hub.post(f"/v1/changes/{change['id']}/revert", json={}).status_code == 200
    assert (repo / "conf.py").read_text(encoding="utf-8").count("x = 1") == 3


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


# --- the window's editor (D47): the user's unsaved edits ---


def last_result(messages):
    return [m for m in messages if m["role"] == "tool"][-1]["content"][0]


def test_a_file_with_unsaved_edits_is_asked_in_a_trusted_chat_too(hub, repo):  # noqa: F811
    """D47, step 14.2c: the window says which files its editor holds with edits not saved; a model's write into one
    is asked in every mode - a trusted chat's too -, the card says why, and an answer "for the chat" covers nothing.
    Another file, and the same one once saved, are written without asking, as a trusted chat does. The list is the
    window's alone."""
    first = {"id": "w1", "name": "edit_file", "input": edit()}
    again = {"id": "w2", "name": "edit_file", "input": edit(old="print('привет')", new="print('ещё')")}
    other = {"id": "w3", "name": "write_file", "input": {"project": "proj", "path": "out.txt", "content": "x\n"}}
    saved = {"id": "w4", "name": "edit_file", "input": edit(old="print('привет')", new="print('всё')")}
    pid, conv, provider = setup_chat(hub, repo, [[first]], mode="trusted")
    unsaved = [{"project_id": pid, "path": "src/app.py"}]
    told = hub.put("/v1/editor/dirty", json={"files": unsaved})
    assert told.status_code == 200 and told.json() == {"files": unsaved}

    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "edit_file" and asked["unsaved"] is True and asked["grant_key"] is None
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "scope": "chat"})
    assert last_result(finished(hub, conv))["error"] is False
    provider.steps.append([again])
    say(hub, conv)  # the same file again: the answer "for the chat" covered nothing
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["unsaved"] is True
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "у меня там правки"})
    assert last_result(finished(hub, conv))["output"] == "not done: the user said no - у меня там правки"
    provider.steps.append([other])
    say(hub, conv)  # another file: as a trusted chat does, without a card
    assert last_result(finished(hub, conv))["error"] is False and (repo / "out.txt").exists()

    assert hub.put("/v1/editor/dirty", json={"files": []}).json() == {"files": []}  # saved
    provider.steps.append([saved])
    say(hub, conv)
    assert last_result(finished(hub, conv))["error"] is False
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('всё')")
    count = hub.app.state.db.call(lambda conn: conn.execute("SELECT COUNT(*) FROM approvals WHERE conversation_id = ?",
                                                            (conv,)).fetchone()[0])  # fmt: skip
    assert count == 2

    assert hub.put("/v1/editor/dirty", json={"files": [{"project_id": "nope", "path": "a"}]}).status_code == 404
    assert hub.put("/v1/editor/dirty", json={"files": [{"project_id": pid, "path": "../x"}]}).status_code == 422
    token = hub.app.state.db.call(lambda conn: create_client(conn, "vscode", cloud=False))
    assert hub.put("/v1/editor/dirty", json={"files": []},
                   headers={"Authorization": f"Bearer {token}"}).status_code == 403  # fmt: skip


def test_the_hub_forgets_unsaved_edits_when_the_window_is_gone(hub, repo, monkeypatch):  # noqa: F811
    """The list lives while a window is connected to the events: a reconnect (the window comes back within moments,
    and says its list again) keeps it; a window gone for good takes it along."""
    monkeypatch.setattr(events_ws, "UNSAVED_KEPT_S", 0.3)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    unsaved = hub.app.state.approvals.unsaved
    headers = {**dict(hub.headers), **WS_HOST}
    with hub.websocket_connect("/v1/events", headers=headers) as ws:
        ws.receive_json()
        hub.put("/v1/editor/dirty", json={"files": [{"project_id": pid, "path": "src/app.py"}]})
    with hub.websocket_connect("/v1/events", headers=headers) as ws:  # back at once
        ws.receive_json()
        time.sleep(0.5)
        assert unsaved.holds("edit_file", {"path": "src/app.py"}, repo)
    wait(lambda: not unsaved.holds("edit_file", {"path": "src/app.py"}, repo))


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
