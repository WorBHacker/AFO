"""A goal's checkpoints (stage 11.6, D44; the user's plan, idea 2): the working files of a git project as a commit of
the hub's own under refs/localchat/goals/ - no branch, no commit on the user's, their index untouched - and the way
back to it: what is restored and what goes, then done; the refs of goals no longer kept go."""

import subprocess

from hub import checkpoints
from tests.conftest import run_git


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
