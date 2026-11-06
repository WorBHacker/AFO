"""«Песочница» (D17, stage 9.4b): in this mode the model writes without asking - into a git worktree of the project
on a branch of its own; the project stays as it was until the user applies the diff, or throws it away."""

import subprocess

import pytest

from hub import sandbox as sb
from hub.projects import Project
from tests.conftest import run_git
from tests.test_changes import edit, finished, new_chat, say, setup_chat
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def project_of(root, pid="p1"):
    return Project(pid, "proj", str(root), "", True, "", "")


def named(found):
    """The sandbox's files by path and status - each carries its hunks too (17.4)."""
    return [{"path": f["path"], "status": f["status"]} for f in found["files"]]


def test_a_worktree_its_diff_and_applying_it(repo, tmp_path):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-1", project)
    assert inside.name == "proj" and inside.root == sb.place(folder, "conv-1", project)
    assert (inside.root / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"  # from the last commit
    assert not (inside.root / "notes.txt").exists()  # the project's uncommitted files are not there
    (inside.root / "README.md").write_text("# Проект\n\nОписание.\n", encoding="utf-8", newline="\n")
    (inside.root / "docs.md").write_text("# Доки\n", encoding="utf-8", newline="\n")
    found = sb.changes(folder, "conv-1", project)
    assert named(found) == [{"path": "README.md", "status": "changed"}, {"path": "docs.md", "status": "added"}]
    assert "+Описание." in found["diff"] and found["branch"] == "localchat/sandbox-conv1"
    assert sb.ensure(folder, "conv-1", project).root == inside.root  # the same sandbox the next time
    done = sb.apply(folder, "conv-1", project)
    assert done == {"applied": ["README.md", "docs.md"], "conflicts": [], "kept": []}
    assert (repo / "docs.md").exists() and (repo / "notes.txt").exists()  # the project's own changes stay
    assert (repo / "README.md").read_text(encoding="utf-8").endswith("Описание.\n")
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("print('new line')\n")
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"], capture_output=True, text=True).stdout
    assert not inside.root.exists() and "sandbox" not in branches


def test_a_conflict_and_throwing_away(repo, tmp_path):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-2", project)
    (inside.root / "README.md").write_text("# Песочница\n", encoding="utf-8", newline="\n")
    (repo / "README.md").write_text("# Проект, правка человека\n", encoding="utf-8", newline="\n")
    done = sb.apply(folder, "conv-2", project)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert done["conflicts"] == ["README.md"] and "<<<<<<< проект" in readme and ">>>>>>> песочница" in readme
    inside = sb.ensure(folder, "conv-3", project)
    (inside.root / "src" / "app.py").write_text("x\n", encoding="utf-8")
    sb.discard(folder, "conv-3", project)
    assert not inside.root.exists() and (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(sb.SandboxError, match="not a git repository"):
        sb.ensure(folder, "conv-4", project_of(plain, "p2"))


def test_a_sandbox_chat(hub, repo):  # noqa: F811
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "the user's own work, committed")
    read = {"id": "r1", "name": "read_file", "input": {"project": "proj", "path": "src/app.py"}}
    pid, conv, _provider = setup_chat(hub, repo, [[{"id": "w1", "name": "edit_file", "input": edit()}], [read]],
                                     mode="sandbox")  # fmt: skip
    say(hub, conv)
    messages = finished(hub, conv)
    results = [m["content"][0] for m in messages if m["role"] == "tool"]
    assert results[0]["error"] is False and "in the sandbox" in results[0]["output"]
    assert "print('привет')" in results[1]["output"]  # its own reads see its writes
    assert hub.get("/v1/approvals").json()["approvals"] == []  # nothing was asked
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('hi')")  # the project: as it was
    assert hub.get(f"/v1/projects/{pid}/changes").json()["changes"] == []  # not a project change (yet)
    shown = hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"]
    assert named(shown[0]) == [{"path": "src/app.py", "status": "changed"}] and "+print('привет')" in shown[0]["diff"]
    assert hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply").json()["applied"] == ["src/app.py"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('привет')")
    assert hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"][0]["files"] == []
    feed = [e["type"] for e in hub.get("/v1/feed", params={"types": "sandbox.*"}).json()["events"]]
    assert feed == ["sandbox.applied", "sandbox.changed"]
    assert hub.post(f"/v1/conversations/{conv}/sandbox/nope/discard").status_code == 404


def test_a_sandbox_chat_applies_the_files_ticked(hub, repo):  # noqa: F811
    """17.1 (D51): «Применить выбранные» - the window sends the files; a file that is not there is refused (409)."""
    other = {"id": "w2", "name": "write_file", "input": {"project": "proj", "path": "docs/new.md", "content": "# Н\n"}}
    pid, conv, _provider = setup_chat(hub, repo, [[{"id": "w1", "name": "edit_file", "input": edit()}, other]],
                                     mode="sandbox")  # fmt: skip
    say(hub, conv)
    finished(hub, conv)
    url = f"/v1/conversations/{conv}/sandbox/{pid}/apply"
    assert hub.post(url, json={"files": ["nope.md"]}).status_code == 409
    done = hub.post(url, json={"files": ["docs/new.md"]}).json()
    assert done == {"applied": ["docs/new.md"], "conflicts": [], "kept": ["src/app.py"]}
    assert (repo / "docs" / "new.md").exists() and (repo / "src" / "app.py").read_text(encoding="utf-8").startswith(
        "print('hi')")  # fmt: skip
    shown = hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"][0]
    assert named(shown) == [{"path": "src/app.py", "status": "changed"}]
    event = hub.get("/v1/feed", params={"types": "sandbox.applied"}).json()["events"][0]
    assert event["payload"]["kept"] == ["src/app.py"]
    assert hub.post(url).json()["applied"] == ["src/app.py"]  # no body: everything left


def test_a_sandbox_chat_applies_the_hunks_ticked(hub, repo):  # noqa: F811
    """17.4 (D51): `hunks` with the apply - a file's hunks by their index, as the sandbox's changes list them."""
    write(repo, "conf.py", "".join("x = 1\n" if n in (1, 20, 40) else f"# {n}\n" for n in range(1, 41)))
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "conf")
    every = {"id": "w1", "name": "edit_file", "input": edit(path="conf.py", old="x = 1", new="x = 2", replace_all=True)}
    pid, conv, _provider = setup_chat(hub, repo, [[every]], mode="sandbox")
    say(hub, conv)
    finished(hub, conv)
    (shown,) = hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"]
    assert [len(f["hunks"]) for f in shown["files"]] == [3]
    url = f"/v1/conversations/{conv}/sandbox/{pid}/apply"
    assert hub.post(url, json={"hunks": {"conf.py": [7]}}).status_code == 409
    done = hub.post(url, json={"hunks": {"conf.py": [1]}}).json()
    assert done == {"applied": ["conf.py"], "conflicts": [], "kept": ["conf.py"]}
    lines = (repo / "conf.py").read_text(encoding="utf-8").splitlines()
    assert (lines[0], lines[19], lines[39]) == ("x = 1", "x = 2", "x = 1")


def test_a_sandbox_without_git(hub, tmp_path):  # noqa: F811
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a\n", encoding="utf-8")
    write = {"id": "w1", "name": "write_file", "input": {"project": "plain", "path": "b.txt", "content": "b\n"}}
    _pid, conv, _provider = setup_chat(hub, plain, [[write]], mode="sandbox")
    say(hub, conv)
    result = next(m for m in finished(hub, conv) if m["role"] == "tool")["content"][0]
    assert result["error"] is True and "not a git repository" in result["output"] and not (plain / "b.txt").exists()


def test_a_chat_takes_its_sandbox_with_it_and_a_failed_one_does_nothing(hub, repo, monkeypatch):  # noqa: F811
    """The audit (D46): deleting a chat removes its sandbox (worktree, branch, ref); a sandbox git could not make
    does not hand the model the project itself - its write and command are refused with why."""
    write = {"id": "w1", "name": "edit_file", "input": edit()}
    pid, conv, provider = setup_chat(hub, repo, [[write]], mode="sandbox")
    say(hub, conv)
    finished(hub, conv)
    folder = hub.app.state.chat.sandboxes
    kept = sb.place(folder, conv, project_of(repo, pid))
    assert kept.exists()
    assert hub.delete(f"/v1/conversations/{conv}").status_code == 204
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"], capture_output=True, text=True).stdout
    assert not kept.exists() and "sandbox" not in branches

    def cannot(*_args, **_kwargs):
        raise sb.SandboxError("fatal: Unable to create '.git/index.lock': File exists.")

    monkeypatch.setattr(sb, "ensure", cannot)
    run = {"id": "c1", "name": "run_command", "input": {"project": "proj", "command": "echo x > made.txt"}}
    provider.steps += [[write], [run]]
    other = new_chat(hub, pid, "sandbox")
    say(hub, other)
    results = [m["content"][0] for m in finished(hub, other) if m["role"] == "tool"]
    assert all(r["error"] and "sandbox copy of «proj» could not be made" in r["output"] for r in results)
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('hi')")  # untouched
    assert not (repo / "made.txt").exists() and hub.get("/v1/approvals").json()["approvals"] == []


def test_what_the_audit_found_names_renames_and_windows_line_ends(repo, tmp_path):
    """The audit (D46): a file with Cyrillic or a space in its name, a renamed file, a project that checks files out
    with CRLF (core.autocrlf=true, as Git for Windows sets it) - applied without a false conflict; what the model
    committed in the sandbox is still its change."""
    run_git(repo, "config", "core.autocrlf", "true")
    (repo / "отчёт за год.txt").write_text("первая\nвторая\n", encoding="utf-8", newline="\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "report")
    (repo / "отчёт за год.txt").write_text("первая\r\nвторая\r\n", encoding="utf-8", newline="")  # as checkout has it
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-5", project)
    report = inside.root / "отчёт за год.txt"
    report.write_bytes(report.read_bytes().replace("вторая".encode(), "вторая, правка модели".encode()))
    (inside.root / "README.md").rename(inside.root / "ПРОЧТИ.md")
    run_git(inside.root, "add", "-A")
    run_git(inside.root, "commit", "-q", "-m", "the model committed")  # its change all the same
    found = sb.changes(folder, "conv-5", project)
    assert sorted((f["path"], f["status"]) for f in found["files"]) == [
        ("README.md", "deleted"), ("ПРОЧТИ.md", "added"), ("отчёт за год.txt", "changed")]  # fmt: skip
    done = sb.apply(folder, "conv-5", project)
    assert done["conflicts"] == [] and sorted(done["applied"]) == ["README.md", "ПРОЧТИ.md", "отчёт за год.txt"]
    assert (repo / "отчёт за год.txt").read_text(encoding="utf-8").splitlines() == ["первая", "вторая, правка модели"]
    assert not (repo / "README.md").exists() and (repo / "ПРОЧТИ.md").read_text(encoding="utf-8").startswith("# ")
    refs = subprocess.run(["git", "-C", str(repo), "for-each-ref", "refs/localchat"], capture_output=True, text=True)
    assert refs.stdout == ""  # the sandbox's base went with it


def test_nothing_is_written_until_every_file_is_worked_out(repo, tmp_path, monkeypatch):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-6", project)
    (inside.root / "README.md").write_text("# Новое\n", encoding="utf-8", newline="\n")
    (inside.root / "src" / "app.py").write_text("print('x')\n", encoding="utf-8", newline="\n")
    merge = sb._merge_one

    def second_fails(root, sandbox, base, name, status, part=None):
        if name == "src/app.py":
            raise sb.SandboxError("git could not read it")
        return merge(root, sandbox, base, name, status, part)

    monkeypatch.setattr(sb, "_merge_one", second_fails)
    with pytest.raises(sb.SandboxError, match="could not read"):
        sb.apply(folder, "conv-6", project)
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект\n"  # the first file was not written either
    assert inside.root.exists()  # the sandbox stays: the user may try again


def refs(repo):
    return subprocess.run(["git", "-C", str(repo), "for-each-ref", "refs/localchat"], capture_output=True,
                          text=True).stdout  # fmt: skip


def test_only_the_files_chosen_go_and_the_rest_stay_in_the_sandbox(repo, tmp_path):
    """17.1 (D51): the user ticks the files to apply; those go onto the project, the others stay in the sandbox for
    later - and the sandbox's diff from then on is only what is still to go, so a file applied and then changed again
    by the model brings only its new change."""
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-7", project)
    write(inside.root, "README.md", "# Проект\n\nОписание.\n")
    (inside.root / "доки").mkdir()
    write(inside.root, "доки/план за год.md", "# План\n")
    (inside.root / ".gitignore").unlink()
    with pytest.raises(sb.SandboxError, match=r"not in the sandbox: nope\.txt"):
        sb.apply(folder, "conv-7", project, only=["README.md", "nope.txt"])
    with pytest.raises(sb.SandboxError, match="no file chosen"):
        sb.apply(folder, "conv-7", project, only=[])
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект\n"  # neither touched the project

    done = sb.apply(folder, "conv-7", project, only=["README.md", ".gitignore"])
    assert done == {"applied": [".gitignore", "README.md"], "conflicts": [], "kept": ["доки/план за год.md"]}
    assert (repo / "README.md").read_text(encoding="utf-8").endswith("Описание.\n")
    assert not (repo / ".gitignore").exists()
    assert not (repo / "доки").exists() and inside.root.exists()  # the rest waits in the sandbox
    assert named(sb.changes(folder, "conv-7", project)) == [{"path": "доки/план за год.md", "status": "added"}]

    write(inside.root, "README.md", "# Проект\n\nОписание.\n\nЕщё абзац.\n")  # the model goes on with an applied file
    write(repo, "README.md", "# Проект, правка человека\n\nОписание.\n")  # and the user with the project's
    found = sb.changes(folder, "conv-7", project)
    assert [f["path"] for f in found["files"]] == ["README.md", "доки/план за год.md"]
    assert "+Ещё абзац." in found["diff"] and "+Описание." not in found["diff"]  # only what is new since
    done = sb.apply(folder, "conv-7", project)
    assert done["conflicts"] == [] and done["kept"] == []
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект, правка человека\n\nОписание.\n\nЕщё абзац.\n"
    assert (repo / "доки" / "план за год.md").exists() and not inside.root.exists() and refs(repo) == ""


LINES = [f"строка {n}" for n in range(1, 41)]


def test_the_hunks_ticked_go_and_the_rest_waits_in_the_sandbox(repo, tmp_path):
    """17.4 (D51): a changed text file's hunks - as the hub's own writes have them - come with the sandbox's changes;
    the user takes some: the project gets those, merged with its own changes as a whole file would be; the rest
    stays in the sandbox, its base past what went, so its diff is only the rest. A project checking files out with
    CRLF (core.autocrlf) gets them so."""
    run_git(repo, "config", "core.autocrlf", "true")
    write(repo, "long.txt", "\n".join(LINES) + "\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "long")
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-9", project)
    model = [line + ", правка" if line == "строка 10" else line for line in LINES if line != "строка 20"]
    model.insert(model.index("строка 38") + 1, "новая")  # a change, a deletion, an addition
    write(inside.root, "long.txt", "\n".join(model) + "\n")
    write(inside.root, "README.md", "# Новое\n")  # one hunk only: it goes whole
    found = {f["path"]: f for f in sb.changes(folder, "conv-9", project)["files"]}
    assert [h["index"] for h in found["long.txt"]["hunks"]] == [0, 1, 2]
    assert "-строка 20\n" in found["long.txt"]["hunks"][1]["text"] and len(found["README.md"]["hunks"]) == 1
    wrong = (({"long.txt": [5]}, None, "there is no hunk 5"), ({"long.txt": []}, None, "no hunk of long.txt chosen"),
             ({"README.md": [0]}, None, "README.md has no hunks to choose"),
             ({"long.txt": [0]}, ["README.md"], "long.txt is not among the files applied"))  # fmt: skip
    for hunks, only, said in wrong:
        with pytest.raises(sb.SandboxError, match=said):
            sb.apply(folder, "conv-9", project, only=only, hunks=hunks)
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект\n"  # none of them touched the project

    write(repo, "long.txt", "\n".join(line + ", человек" if line == "строка 30" else line for line in LINES) + "\n")
    done = sb.apply(folder, "conv-9", project, hunks={"long.txt": [2, 0]})
    assert done == {"applied": ["README.md", "long.txt"], "conflicts": [], "kept": ["long.txt"]}
    text = (repo / "long.txt").read_text(encoding="utf-8").splitlines()
    assert "строка 10, правка" in text and "строка 20" in text and "новая" in text and "строка 30, человек" in text
    data = (repo / "long.txt").read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n") == len(text)  # as checkout puts it here: CRLF
    left = sb.changes(folder, "conv-9", project)["files"]
    assert [(f["path"], len(f["hunks"])) for f in left] == [("long.txt", 1)]
    assert "-строка 20\n" in left[0]["hunks"][0]["text"]  # only what is still to go
    assert sb.apply(folder, "conv-9", project)["kept"] == []
    text = (repo / "long.txt").read_text(encoding="utf-8").splitlines()
    assert "строка 20" not in text and "строка 30, человек" in text and "новая" in text
    assert not inside.root.exists() and refs(repo) == ""


def test_blobs_are_read_by_one_git_and_a_missing_one_is_none(repo):
    """A missing object's line is «<what was asked> missing» - its name may hold spaces."""
    found = sb._blobs(repo, ["HEAD:нет такого файла.txt", "HEAD:README.md", "HEAD:src/app.py"])
    assert found == [None, "# Проект\n".encode(), b"print('hi')\n"]


def test_applying_the_last_files_chosen_takes_the_sandbox_away(repo, tmp_path):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-8", project)
    write(inside.root, "README.md", "# Новое\n")
    run_git(inside.root, "add", "-A")
    run_git(inside.root, "commit", "-q", "-m", "the model committed")  # its commit does not hide the change
    assert sb.apply(folder, "conv-8", project, only=["README.md"]) == {"applied": ["README.md"], "conflicts": [],
                                                                       "kept": []}  # fmt: skip
    assert not inside.root.exists() and refs(repo) == ""


def test_a_sandbox_kept_for_its_goal_moves_past_all_that_went(repo, tmp_path):
    """19.2: a chat whose goal is still open keeps its sandbox when the user applies all of it - the goal works on
    there, with what the user took (not committed in the project); its diff is only what comes after."""
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    inside = sb.ensure(folder, "conv-10", project)
    write(inside.root, "a.txt", "a\n")
    assert sb.apply(folder, "conv-10", project, keep=True) == {"applied": ["a.txt"], "conflicts": [], "kept": []}
    assert (repo / "a.txt").exists() and (inside.root / "a.txt").exists() and "sandbox/conv10" in refs(repo)
    assert sb.changes(folder, "conv-10", project)["files"] == []
    write(inside.root, "a.txt", "a\nb\n")
    found = sb.changes(folder, "conv-10", project)
    assert named(found) == [{"path": "a.txt", "status": "changed"}]
    assert "+b" in found["diff"] and "+a" not in found["diff"]  # only what came after
    assert sb.apply(folder, "conv-10", project, keep=True)["applied"] == ["a.txt"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a\nb\n" and inside.root.exists()
    assert sb.apply(folder, "conv-10", project, keep=True)["applied"] == []  # nothing to go: it stays all the same
    assert inside.root.exists()
    sb.apply(folder, "conv-10", project)  # the goal is over: nothing to go, and the sandbox goes
    assert not inside.root.exists() and refs(repo) == ""


def test_sandboxes_of_chats_that_are_gone_are_swept(repo, tmp_path):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    kept, gone = sb.ensure(folder, "keep1", project), sb.ensure(folder, "gone1", project)
    assert sb.orphans(folder, {"keep1"}, {project.id: project}) == ["gone1/p1"]
    assert kept.root.exists() and not gone.root.exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"], capture_output=True, text=True).stdout
    assert "sandbox-keep1" in branches and "sandbox-gone1" not in branches


def write(root, name, text):
    (root / name).write_text(text, encoding="utf-8", newline="\n")


def test_a_sandbox_that_starts_from_what_others_left(repo, tmp_path):
    """16.2 (D50): a task that waited for others starts from what they left in their sandboxes (not committed there:
    a snapshot), merged; its own diff is only its own - so applying them one after another has no conflict."""
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "second")
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    assert sb.snapshot(folder, "task-a", project) is None  # no sandbox, nothing to start from
    a = sb.ensure(folder, "task-a", project)
    write(a.root, "src/app.py", "print('hi')\nprint('new line')\nprint('a')\n")
    c = sb.ensure(folder, "task-c", project)
    write(c.root, "README.md", "# Проект\n\nC.\n")
    start, conflicts = sb.start_from(project, [sb.snapshot(folder, "task-a", project),
                                               sb.snapshot(folder, "task-c", project)])  # fmt: skip
    assert conflicts == []
    b = sb.ensure(folder, "task-b", project, start=start)
    assert (b.root / "src" / "app.py").read_text(encoding="utf-8").endswith("print('a')\n")
    assert (b.root / "README.md").read_text(encoding="utf-8") == "# Проект\n\nC.\n"
    write(b.root, "src/app.py", "print('hi')\nprint('new line')\nprint('a')\nprint('b')\n")
    assert named(sb.changes(folder, "task-b", project)) == [{"path": "src/app.py", "status": "changed"}]
    assert named(sb.changes(folder, "task-a", project)) == [{"path": "src/app.py", "status": "changed"}]
    for task in ("task-a", "task-c", "task-b"):  # in the order they ran
        assert sb.apply(folder, task, project)["conflicts"] == [], task
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("print('a')\nprint('b')\n")
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект\n\nC.\n"
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert "src/app.py" in status and "README.md" in status  # in the working tree, for the user to commit


def test_tasks_that_changed_the_same_place_hand_over_a_conflict(repo, tmp_path):
    folder = tmp_path / "worktrees"
    project = project_of(repo)
    for task, said in (("task-a", "A"), ("task-c", "C")):
        write(sb.ensure(folder, task, project).root, "README.md", f"# {said}\n")
    start, conflicts = sb.start_from(project, [sb.snapshot(folder, "task-a", project),
                                               sb.snapshot(folder, "task-c", project)])  # fmt: skip
    assert conflicts == ["README.md"]
    b = sb.ensure(folder, "task-b", project, start=start)
    text = (b.root / "README.md").read_text(encoding="utf-8")
    assert "<<<<<<<" in text and "# A" in text and "# C" in text  # the task after them sees it and can mend it
    one, none = sb.start_from(project, [sb.snapshot(folder, "task-a", project)])
    assert none == [] and one == sb.snapshot(folder, "task-a", project)


def test_a_lock_of_the_users_git_is_waited_out(repo, monkeypatch):
    """The user's git (a terminal, VS Code) holds .git/index.lock: the hub's own write tries again a little, then
    says it in plain words (common/plain_errors.py)."""
    from common.plain_errors import explain
    from hub import projects

    real, calls = projects.git, []
    locked = b"fatal: Unable to create '.git/index.lock': File exists."

    def busy_twice(root, *args, **kwargs):
        calls.append(args)
        if len(calls) <= 2:
            return subprocess.CompletedProcess(args, 128, b"", locked)
        return real(root, *args, **kwargs)

    monkeypatch.setattr(sb, "LOCK_WAITS", (0, 0, 0))
    monkeypatch.setattr(projects, "git", busy_twice)
    assert "main" in sb._git(repo, "branch", "--list", retry=True) and len(calls) == 3  # the third try went through
    calls.clear()
    monkeypatch.setattr(projects, "git", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", b"cannot lock ref"))
    with pytest.raises(sb.SandboxError) as caught:
        sb._git(repo, "branch", "-D", "x", retry=True)
    assert explain(str(caught.value))[0] == "Git занят другой программой"
