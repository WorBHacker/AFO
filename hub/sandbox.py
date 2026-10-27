"""The «Песочница» mode of a chat (D17, stage 9.4b): the model writes without asking - into a git worktree of each
attached project, on a branch of its own, never into the project itself. The user then sees the diff and applies it
to the project's working tree, or throws it away.

A worktree starts from the project's last commit (HEAD): changes not committed in the project are not in it. It
lives in `data/worktrees/<chat>/<project>` on the branch `localchat/sandbox-<chat>`; the model's reads, writes and
commands in that chat see the worktree. A project that is not a git repository gets no sandbox: writes there are
refused with why. Applying puts the diff onto the project's working tree (`git apply --3way`: a file changed since
there too is merged, or left with conflict marks the user resolves); the worktree and its branch go then, as on
«Отбросить».
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .projects import Project

GIT_TIMEOUT = 60
DIFF_MAX = 400_000


class SandboxError(Exception):
    """What the model or the window reads."""


def _run(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, timeout=GIT_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f"git did not run: {exc}") from exc
    if done.returncode != 0:
        raise SandboxError((done.stderr or done.stdout).decode("utf-8", "replace").strip()[:500] or "git failed")
    return done.stdout


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    return _run(cwd, *args, stdin=stdin).decode("utf-8", "replace")


STATUS = {"A": "added", "D": "deleted"}


def is_git(project: Project) -> bool:
    return (project.root / ".git").exists()


def branch_name(conversation_id: str) -> str:
    return "localchat/sandbox-" + re.sub(r"[^A-Za-z0-9]", "", conversation_id)[:12]


def place(folder: Path, conversation_id: str, project: Project) -> Path:
    return folder / re.sub(r"[^A-Za-z0-9_-]", "_", conversation_id) / re.sub(r"[^A-Za-z0-9_-]", "_", project.id)


def ensure(folder: Path, conversation_id: str, project: Project) -> Project:
    """The project as the chat's tools see it in the sandbox: the same name and id, its worktree for a root."""
    if not is_git(project):
        raise SandboxError(f"«{project.name}» is not a git repository: the sandbox needs one - switch the chat to "
                           "«Спрашивать»")  # fmt: skip
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(conversation_id)
        existing = _git(project.root, "branch", "--list", branch).strip()
        if existing:  # a branch left by a worktree that was removed by hand: start it again
            _git(project.root, "worktree", "prune")
            _git(project.root, "worktree", "add", str(path), branch)
        else:
            _git(project.root, "worktree", "add", "-b", branch, str(path), "HEAD")
    return dataclasses.replace(project, path=str(path))


def changes(folder: Path, conversation_id: str, project: Project) -> dict[str, Any]:
    """What the model changed there: the files and the diff (new files too), against the commit it started from."""
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        return {"project_id": project.id, "name": project.name, "files": [], "diff": "", "exists": False}
    _git(path, "add", "-A")  # new files count too; the index is the sandbox's own
    names = _git(path, "diff", "--cached", "--name-status", "HEAD")
    files = []
    for line in names.splitlines():
        status, _, name = line.partition("\t")
        files.append({"path": name.split("\t")[-1], "status": STATUS.get(status[:1], "changed")})
    diff = _git(path, "diff", "--cached", "HEAD")
    return {"project_id": project.id, "name": project.name, "branch": branch_name(conversation_id),
            "files": files, "diff": diff[:DIFF_MAX], "exists": True}  # fmt: skip


def apply(folder: Path, conversation_id: str, project: Project) -> dict[str, Any]:
    """The sandbox's changes onto the project's working tree; then the sandbox goes."""
    found = changes(folder, conversation_id, project)
    if not found["exists"] or not found["files"]:
        discard(folder, conversation_id, project)
        return {"applied": [], "conflicts": []}
    path = place(folder, conversation_id, project)
    base_rev = _git(path, "rev-parse", "HEAD").strip()  # the commit the sandbox started from: it never commits
    conflicts = []
    for f in found["files"]:
        if not _merge_one(project.root, path, base_rev, f["path"], f["status"]):
            conflicts.append(f["path"])
    discard(folder, conversation_id, project)
    return {"applied": [f["path"] for f in found["files"]], "conflicts": conflicts}


def _merge_one(root: Path, sandbox: Path, base_rev: str, name: str, status: str) -> bool:
    """One file of the sandbox onto the project's working tree, the project's own changes kept: a three-way merge
    with the commit both started from (git merge-file). False when both changed the same place - the file then
    has conflict marks, or (a binary one) stays as the project has it."""
    target = root / name
    mine = target.read_bytes() if target.is_file() else None
    base = None if status == "added" else _run(root, "show", f"{base_rev}:{name}")
    theirs = None if status == "deleted" else (sandbox / name).read_bytes()
    if mine in (base, theirs):  # the project did not touch it, or did the same
        if theirs is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(theirs)
        return True
    if mine is None or theirs is None or b"\0" in mine + (base or b"") + theirs:
        return False  # deleted on one side and changed on the other, or binary: the user decides
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for label, data in (("mine", mine), ("base", base or b""), ("theirs", theirs)):
            files.append(Path(tmp) / label)
            files[-1].write_bytes(data)
        done = subprocess.run(["git", "merge-file", "-p", "-L", "проект", "-L", "начало", "-L", "песочница",
                               *map(str, files)], capture_output=True, timeout=GIT_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
    if done.returncode < 0 or done.returncode > 127:
        raise SandboxError(done.stderr.decode("utf-8", "replace")[:300] or "git merge-file failed")
    target.write_bytes(done.stdout)
    return done.returncode == 0


def discard(folder: Path, conversation_id: str, project: Project) -> None:
    path = place(folder, conversation_id, project)
    if path.exists():
        try:
            _git(project.root, "worktree", "remove", "--force", str(path))
        except SandboxError:
            shutil.rmtree(path, ignore_errors=True)
            _git(project.root, "worktree", "prune")
    if is_git(project) and _git(project.root, "branch", "--list", branch_name(conversation_id)).strip():
        _git(project.root, "branch", "-D", branch_name(conversation_id))
