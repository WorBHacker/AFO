"""The «Песочница» mode of a chat (D17, stage 9.4b): the model writes without asking - into a git worktree of each
attached project, on a branch of its own, never into the project itself. The user then sees the diff and applies it
to the project's working tree, or throws it away.

A worktree starts from the project's last commit (HEAD): changes not committed in the project are not in it. It
lives in `data/worktrees/<chat>/<project>` on the branch `localchat/sandbox-<chat>`; the commit it began from is kept
by the ref `refs/localchat/sandbox/<chat>` (shared by every worktree, safe from git's cleaning) - so what the model
committed there is still its change. The model's reads, writes and commands in that chat see the worktree. A project
that is not a git repository gets no sandbox: writes there are refused with why.

Applying (after the audit, D46) takes the project's own changes into account file by file: three-way, with the
commit both began from, compared and merged in git's own form of the text and written back in the form this computer
checks out (core.autocrlf, .gitattributes) - so Windows line ends are no conflict; a renamed file is a deletion and an
addition; every file is worked out first, and only then is anything written - one that cannot be worked out leaves
the project untouched.
A file changed on both sides at the same place gets conflict marks the user resolves. The worktree, its branch and
its ref go then, as on «Отбросить» - and with the chat, and at the hub's start when the chat is gone.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import projects
from .projects import Project

GIT_TIMEOUT = 60
DIFF_MAX = 400_000
LOCK_WAITS = (0.3, 0.7, 1.5)  # the user's git (a terminal, VS Code) holding .git/index.lock: tries again, then says
LOCKED = re.compile(r"index\.lock|\.lock'?: File exists|cannot lock ref|Unable to create '[^']*\.lock'", re.I)


class SandboxError(Exception):
    """What the model or the window reads."""


def _run(cwd: Path, *args: str, stdin: bytes | None = None, retry: bool = False) -> bytes:
    """git in `cwd` (projects.git: no prompts, paths as they are); `retry`: a write into the user's repository - a
    lock held by the user's own git is waited out a little."""
    for wait in (*LOCK_WAITS, None) if retry else (None,):
        try:
            done = projects.git(cwd, *args, input=stdin, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"git did not run: {exc}") from exc
        if done.returncode == 0:
            return done.stdout
        said = (done.stderr or done.stdout).decode("utf-8", "replace").strip()[:500] or "git failed"
        if wait is None or not LOCKED.search(said):
            raise SandboxError(said)
        time.sleep(wait)
    raise AssertionError("unreachable")


def _git(cwd: Path, *args: str, stdin: bytes | None = None, retry: bool = False) -> str:
    return _run(cwd, *args, stdin=stdin, retry=retry).decode("utf-8", "replace")


STATUS = {"A": "added", "D": "deleted"}


def is_git(project: Project) -> bool:
    return (project.root / ".git").exists()


def _key(conversation_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", conversation_id)[:12]


def branch_name(conversation_id: str) -> str:
    return "localchat/sandbox-" + _key(conversation_id)


def base_ref(conversation_id: str) -> str:
    return "refs/localchat/sandbox/" + _key(conversation_id)


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
            _git(project.root, "worktree", "prune", retry=True)
            _git(project.root, "worktree", "add", str(path), branch, retry=True)
        else:
            _git(project.root, "worktree", "add", "-b", branch, str(path), "HEAD", retry=True)
        if not _git(project.root, "for-each-ref", base_ref(conversation_id)).strip():
            _git(project.root, "update-ref", base_ref(conversation_id), _git(path, "rev-parse", "HEAD").strip(),
                 retry=True)  # fmt: skip
    return dataclasses.replace(project, path=str(path))


def _base(path: Path, conversation_id: str) -> str:
    """The commit the sandbox began from; a sandbox made before its ref was kept: where its branch stands."""
    found = _git(path, "for-each-ref", "--format=%(objectname)", base_ref(conversation_id)).strip()
    return found or _git(path, "rev-parse", "HEAD").strip()


def changes(folder: Path, conversation_id: str, project: Project) -> dict[str, Any]:
    """What the model changed there: the files and the diff (new files too), against the commit it began from."""
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        return {"project_id": project.id, "name": project.name, "files": [], "diff": "", "exists": False}
    _git(path, "add", "-A")  # new files count too; the index is the sandbox's own
    base = _base(path, conversation_id)
    names = _git(path, "diff", "--cached", "--name-status", "--no-renames", "-z", base).split("\0")
    files = [{"path": name, "status": STATUS.get(status[:1], "changed")}
             for status, name in zip(names[0::2], names[1::2], strict=False) if status and name]  # fmt: skip
    diff = _git(path, "diff", "--cached", "--no-renames", base)
    return {"project_id": project.id, "name": project.name, "branch": branch_name(conversation_id),
            "files": files, "diff": diff[:DIFF_MAX], "diff_chars": len(diff), "exists": True}  # fmt: skip


def apply(folder: Path, conversation_id: str, project: Project) -> dict[str, Any]:
    """The sandbox's changes onto the project's working tree; then the sandbox goes."""
    found = changes(folder, conversation_id, project)
    if not found["exists"] or not found["files"]:
        discard(folder, conversation_id, project)
        return {"applied": [], "conflicts": []}
    path = place(folder, conversation_id, project)
    base = _base(path, conversation_id)
    # every file worked out first: one git cannot read stops it all before the project is touched
    planned = [(f["path"], *_merge_one(project.root, path, base, f["path"], f["status"])) for f in found["files"]]
    for name, data, _clean in planned:
        target = projects.resolve_inside(project.root, name)
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    discard(folder, conversation_id, project)
    return {"applied": [name for name, _data, _clean in planned],
            "conflicts": [name for name, _data, clean in planned if not clean]}  # fmt: skip


def _clean(root: Path, name: str, data: bytes) -> bytes:
    """The bytes as git keeps them for this path (core.autocrlf, .gitattributes): a file saved with CRLF and the
    same file with LF are one to git - so they are to the merge."""
    blob = _git(root, "hash-object", "-w", "--stdin", f"--path={name}", stdin=data).strip()
    return _run(root, "cat-file", "blob", blob)


def _smudge(root: Path, name: str, data: bytes) -> bytes:
    """The other way: git's bytes as checkout puts them on disk here for this path."""
    blob = _git(root, "hash-object", "-w", "--no-filters", "--stdin", stdin=data).strip()
    return _run(root, "cat-file", "--filters", f"--path={name}", blob)


def _merge_one(root: Path, sandbox: Path, base_rev: str, name: str, status: str) -> tuple[bytes | None, bool]:
    """One file of the sandbox as the project's working tree should have it, the project's own changes kept: a
    three-way merge with the commit both began from, done in git's own form of the text and put back in the form
    this computer checks out (core.autocrlf, .gitattributes - Windows line ends are no conflict, D46). (bytes, or None
    for a file to delete; False when both changed the same place - the bytes then hold conflict marks, or (a binary
    one, or deleted on one side and changed on the other) are the project's as they are)."""
    target = root / name
    mine = target.read_bytes() if target.is_file() else None
    theirs = None if status == "deleted" else (sandbox / name).read_bytes()
    base = None if status == "added" else _run(root, "cat-file", "blob", f"{base_rev}:{name}")
    mine_kept = None if mine is None else _clean(root, name, mine)
    if mine_kept == base:  # the project did not touch it: the sandbox's file as it is
        return theirs, True
    if mine_kept == (None if theirs is None else _clean(root, name, theirs)):  # both did the same
        return mine, True
    if mine is None or theirs is None or b"\0" in mine + (base or b"") + theirs:
        return mine, False  # deleted on one side and changed on the other, or binary: the user decides
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for label, data in (("mine", mine_kept), ("base", base or b""), ("theirs", _clean(root, name, theirs))):
            files.append(Path(tmp) / label)
            files[-1].write_bytes(data or b"")
        done = subprocess.run(["git", "merge-file", "-p", "-L", "проект", "-L", "начало", "-L", "песочница",
                               *map(str, files)], capture_output=True, timeout=GIT_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
    if done.returncode < 0 or done.returncode > 127:
        raise SandboxError(done.stderr.decode("utf-8", "replace")[:300] or "git merge-file failed")
    return _smudge(root, name, done.stdout), done.returncode == 0


def discard(folder: Path, conversation_id: str, project: Project) -> None:
    path = place(folder, conversation_id, project)
    if path.exists():
        try:
            _git(project.root, "worktree", "remove", "--force", str(path), retry=True)
        except SandboxError:
            shutil.rmtree(path, ignore_errors=True)
            _git(project.root, "worktree", "prune", retry=True)
    if not is_git(project):
        return
    if _git(project.root, "branch", "--list", branch_name(conversation_id)).strip():
        _git(project.root, "branch", "-D", branch_name(conversation_id), retry=True)
    if _git(project.root, "for-each-ref", base_ref(conversation_id)).strip():
        _git(project.root, "update-ref", "-d", base_ref(conversation_id), retry=True)


def orphans(folder: Path, live: set[str], by_id: dict[str, Project]) -> list[str]:
    """The sandboxes whose chat is gone (deleted while the hub was off, or before sandboxes went with their chat):
    their worktrees, branches and refs removed. Returns what went, as «<chat>/<project>»."""
    gone = []
    if not folder.is_dir():
        return gone
    for chat in folder.iterdir():
        if not chat.is_dir() or chat.name in live:
            continue
        for kept in [p for p in chat.iterdir() if p.is_dir()]:
            project = by_id.get(kept.name)
            try:
                if project is not None:
                    discard(folder, chat.name, project)
                else:  # its project is gone too: only the folder is left to remove
                    shutil.rmtree(kept, ignore_errors=True)
                gone.append(f"{chat.name}/{kept.name}")
            except SandboxError:
                continue  # the user's git busy: the next start tries again
        if not any(chat.iterdir()):
            chat.rmdir()
    return gone
