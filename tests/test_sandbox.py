"""«Песочница» (D17, stage 9.4b): in this mode the model writes without asking - into a git worktree of the project
on a branch of its own; the project stays as it was until the user applies the diff, or throws it away."""

import subprocess

import pytest

from hub import sandbox as sb
from hub.projects import Project
from tests.conftest import run_git
from tests.test_changes import edit, finished, say, setup_chat
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def project_of(root, pid="p1"):
    return Project(pid, "proj", str(root), "", True, "", "")


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
    assert found["files"] == [{"path": "README.md", "status": "changed"}, {"path": "docs.md", "status": "added"}]
    assert "+Описание." in found["diff"] and found["branch"] == "localchat/sandbox-conv1"
    assert sb.ensure(folder, "conv-1", project).root == inside.root  # the same sandbox the next time
    done = sb.apply(folder, "conv-1", project)
    assert done == {"applied": ["README.md", "docs.md"], "conflicts": []}
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
    assert shown[0]["files"] == [{"path": "src/app.py", "status": "changed"}] and "+print('привет')" in shown[0]["diff"]
    assert hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply").json()["applied"] == ["src/app.py"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('привет')")
    assert hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"][0]["files"] == []
    feed = [e["type"] for e in hub.get("/v1/feed", params={"types": "sandbox.*"}).json()["events"]]
    assert feed == ["sandbox.applied", "sandbox.changed"]
    assert hub.post(f"/v1/conversations/{conv}/sandbox/nope/discard").status_code == 404


def test_a_sandbox_without_git(hub, tmp_path):  # noqa: F811
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a\n", encoding="utf-8")
    write = {"id": "w1", "name": "write_file", "input": {"project": "plain", "path": "b.txt", "content": "b\n"}}
    _pid, conv, _provider = setup_chat(hub, plain, [[write]], mode="sandbox")
    say(hub, conv)
    result = next(m for m in finished(hub, conv) if m["role"] == "tool")["content"][0]
    assert result["error"] is True and "not a git repository" in result["output"] and not (plain / "b.txt").exists()
