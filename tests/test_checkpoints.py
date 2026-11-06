"""A goal's checkpoints (stage 11.6, D44; the user's plan, idea 2): the working files of a git project as a commit of
the hub's own under refs/localchat/goals/ - no branch, no commit on the user's, their index untouched - and the way
back to it: what is restored and what goes, then done; the refs of goals no longer kept go."""

import subprocess

from hub import checkpoints
from hub import sandbox as sb
from tests.conftest import run_git
from tests.test_sandbox import project_of, write


def status(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout


def refs(repo):
    found = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname) %(objectname)",
                            "refs/localchat/goals"], capture_output=True, text=True).stdout  # fmt: skip
    return dict(line.split() for line in found.splitlines())


def test_a_checkpoint_and_the_way_back_to_it(repo):
    (repo / "отчёт за год.txt").write_text("первая\n", encoding="utf-8", newline="\n")
    before = status(repo)
    saved = checkpoints.save(repo, "g1", "start")
    assert refs(repo) == {"refs/localchat/goals/g1/start": saved["commit"]} and status(repo) == before
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list"], capture_output=True, text=True).stdout
    assert branches.split() == ["*", "main"]  # no branch of the hub's own

    (repo / "README.md").write_text("# Сломано\n", encoding="utf-8", newline="\n")
    (repo / "notes.txt").unlink()  # a new file of the user's, not committed: it was in the checkpoint all the same
    (repo / "мусор.txt").write_text("x\n", encoding="utf-8", newline="\n")
    (repo / "build" / "out2.bin").write_bytes(b"\0")  # ignored by git: not the checkpoint's to touch
    planned = checkpoints.plan(repo, saved["tree"])
    assert planned == {"restore": ["README.md", "notes.txt"], "delete": ["мусор.txt"]}
    assert checkpoints.restore(repo, saved["tree"]) == planned
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Проект\n"
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "свежий файл\n"
    assert not (repo / "мусор.txt").exists() and (repo / "build" / "out2.bin").exists()
    assert (repo / "отчёт за год.txt").read_text(encoding="utf-8") == "первая\n"
    assert status(repo) == before  # the user's index as it was
    assert checkpoints.plan(repo, saved["tree"]) == {"restore": [], "delete": []}


def test_going_back_in_a_sandbox_keeps_what_the_user_applied_since(repo, tmp_path):
    """19.3: a sandbox's checkpoint remembers its base; the user applies a file after it, and the sandbox's base moves
    past it - going back keeps that file as the base has it (the project has it now); the model's work since goes."""
    folder, project = tmp_path / "worktrees", project_of(repo)
    inside = sb.ensure(folder, "conv-1", project)
    saved = checkpoints.save(inside.root, "g1", "start", base=sb.base_of(inside.root, "conv-1"))
    write(inside.root, "взял.txt", "нужное\n")
    write(inside.root, "README.md", "# Убрали всё\n")
    write(inside.root, "черновик.txt", "x\n")
    assert sb.apply(folder, "conv-1", project, only=["взял.txt", "README.md"])["kept"] == ["черновик.txt"]
    write(inside.root, "взял.txt", "нужное, и ещё\n")  # the model goes on with it
    tree = checkpoints.keeping_applied(inside.root, saved["commit"], sb.base_of(inside.root, "conv-1"))
    planned = checkpoints.plan(inside.root, tree)
    assert planned == {"restore": ["взял.txt"], "delete": ["черновик.txt"]}  # README.md is as applied already
    checkpoints.restore(inside.root, tree)
    assert (inside.root / "взял.txt").read_text(encoding="utf-8") == "нужное\n"
    assert sb.changes(folder, "conv-1", project)["files"] == []  # nothing offers to take back what went
    plain = checkpoints.save(repo, "g2", "start")  # in the project itself: no parent, its own tree
    assert checkpoints.keeping_applied(repo, plain["commit"], "HEAD") == plain["tree"]


def test_the_refs_of_goals_no_longer_kept_go(repo):
    checkpoints.save(repo, "g1", "start")
    checkpoints.save(repo, "g1", "stage-1")
    checkpoints.save(repo, "g2", "start")
    assert checkpoints.prune(repo, {"g2"}) == ["refs/localchat/goals/g1/stage-1", "refs/localchat/goals/g1/start"]
    assert list(refs(repo)) == ["refs/localchat/goals/g2/start"]
    plain = repo.parent / "plain"
    plain.mkdir()
    assert checkpoints.prune(plain, set()) == []  # not a git project: nothing to do
    run_git(repo, "status")  # the repository is well
