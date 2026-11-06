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
its ref go then, as on «Отбросить» - and with the chat, and at the hub's start when the chat is gone. A chat whose
goal is still open keeps it (19.2): the goal goes on there, and a new sandbox would begin without what the user took.

The user may apply only the files ticked (17.1, D51): those go the same way, the others stay in the sandbox, and its
base moves past what went (a commit of the hub's own: the base with those files as the sandbox has them) - so the
sandbox's diff from then on is only what is still to go, and a file applied and changed again brings only its new
change. Of a changed text file, the user may take only some hunks (17.4): they are the hunks of the hub's own writes
(hub/changes.py), worked out in git's form of the text; the part is merged as a whole file would be, and the base
moves to it - the rest of the file waits in the sandbox.
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

from common.i18n import tr

from . import changes as edits
from . import projects
from .changes import write_atomic
from .projects import Project

GIT_TIMEOUT = 60
DIFF_MAX = 400_000
LOCK_WAITS = (0.3, 0.7, 1.5)  # the user's git (a terminal, VS Code) holding .git/index.lock: tries again, then says
LOCKED = re.compile(r"index\.lock|\.lock'?: File exists|cannot lock ref|Unable to create '[^']*\.lock'", re.I)


class SandboxError(Exception):
    """What the model or the window reads."""


def _run(cwd: Path, *args: str, stdin: bytes | None = None, retry: bool = False,
         env: dict[str, str] | None = None) -> bytes:  # fmt: skip
    """git in `cwd` (projects.git: no prompts, paths as they are); `retry`: a write into the user's repository - a
    lock held by the user's own git is waited out a little."""
    for wait in (*LOCK_WAITS, None) if retry else (None,):
        try:
            done = projects.git(cwd, *args, input=stdin, timeout=GIT_TIMEOUT, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"git did not run: {exc}") from exc
        if done.returncode == 0:
            return done.stdout
        said = (done.stderr or done.stdout).decode("utf-8", "replace").strip()[:500] or "git failed"
        if wait is None or not LOCKED.search(said):
            raise SandboxError(said)
        time.sleep(wait)
    raise AssertionError("unreachable")


def _git(cwd: Path, *args: str, stdin: bytes | None = None, retry: bool = False,
         env: dict[str, str] | None = None) -> str:  # fmt: skip
    return _run(cwd, *args, stdin=stdin, retry=retry, env=env).decode("utf-8", "replace")


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


def ensure(folder: Path, conversation_id: str, project: Project, start: str = "HEAD") -> Project:
    """The project as the chat's tools see it in the sandbox: the same name and id, its worktree for a root. A new
    one begins at `start` - the project's last commit, or what other tasks left (start_from, 16.2)."""
    if not is_git(project):
        raise SandboxError(f"«{project.name}» is not a git repository: the sandbox needs one - switch the chat to "
                           "«Спрашивать»")  # fmt: skip  # i18n: keep
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = branch_name(conversation_id)
        existing = _git(project.root, "branch", "--list", branch).strip()
        if existing:  # a branch left by a worktree that was removed by hand: start it again
            _git(project.root, "worktree", "prune", retry=True)
            _git(project.root, "worktree", "add", str(path), branch, retry=True)
        else:
            _git(project.root, "worktree", "add", "-b", branch, str(path), start, retry=True)
        if not _git(project.root, "for-each-ref", base_ref(conversation_id)).strip():
            _git(project.root, "update-ref", base_ref(conversation_id), _git(path, "rev-parse", "HEAD").strip(),
                 retry=True)  # fmt: skip
    return dataclasses.replace(project, path=str(path))


IDENTITY = ("-c", "user.name=AFO", "-c", "user.email=afo@localhost")  # the hub's own commits in its sandboxes


def snapshot(folder: Path, conversation_id: str, project: Project) -> str | None:
    """What the sandbox holds now, as a commit on its own branch (files the model did not commit go into it); None
    when there is no sandbox. Plumbing only - no hooks, no signing, the user's branches untouched (16.2, D50)."""
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        return None
    _git(path, "add", "-A")
    tree = _git(path, "write-tree").strip()
    head = _git(path, "rev-parse", "HEAD").strip()
    if tree == _git(path, "rev-parse", "HEAD^{tree}").strip():
        return head
    commit = _git(path, *IDENTITY, "commit-tree", tree, "-p", head, "-m", "AFO: what the task left").strip()
    _git(path, "update-ref", f"refs/heads/{branch_name(conversation_id)}", commit, head, retry=True)
    return commit


def start_from(project: Project, commits: list[str]) -> tuple[str, list[str]]:
    """Where a task's sandbox starts when it waited for others (16.2, D50): what they left, merged by git
    (merge-tree, nothing checked out); two that changed the same place leave conflict marks there, and those files
    are named - the task sees them and can mend them."""
    head, conflicts = commits[0], []
    for other in commits[1:]:
        try:
            done = projects.git(project.root, "merge-tree", "--write-tree", "--name-only", "--no-messages", head,
                                other, timeout=GIT_TIMEOUT)  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"git did not run: {exc}") from exc
        if done.returncode not in (0, 1):  # 1: merged, with conflicts
            raise SandboxError((done.stderr or done.stdout).decode("utf-8", "replace").strip()[:500])
        tree, *named = done.stdout.decode("utf-8", "replace").splitlines()
        conflicts += [n for n in named if n]
        head = _git(project.root, *IDENTITY, "commit-tree", tree, "-p", head, "-p", other, "-m",
                    "AFO: what the tasks left").strip()  # fmt: skip
    return head, list(dict.fromkeys(conflicts))


def base_of(path: Path, conversation_id: str) -> str:
    """The commit the sandbox began from; a sandbox made before its ref was kept: where its branch stands."""
    found = _git(path, "for-each-ref", "--format=%(objectname)", base_ref(conversation_id)).strip()
    return found or _git(path, "rev-parse", "HEAD").strip()


def changes(folder: Path, conversation_id: str, project: Project) -> dict[str, Any]:
    """What the model changed there: the files and the diff (new files too), against the commit it began from."""
    path = place(folder, conversation_id, project)
    if not (path / ".git").exists():
        return {"project_id": project.id, "name": project.name, "files": [], "diff": "", "exists": False}
    _git(path, "add", "-A")  # new files count too; the index is the sandbox's own
    base = base_of(path, conversation_id)
    names = _git(path, "diff", "--cached", "--name-status", "--no-renames", "-z", base).split("\0")
    files = [{"path": name, "status": STATUS.get(status[:1], "changed")}
             for status, name in zip(names[0::2], names[1::2], strict=False) if status and name]  # fmt: skip
    for f, planned in zip(files, _versions(path, base, files), strict=True):
        f["hunks"] = edits.hunks(planned) if planned is not None else []  # 17.4: none for a binary file
    diff = _git(path, "diff", "--cached", "--no-renames", base)
    return {"project_id": project.id, "name": project.name, "branch": branch_name(conversation_id),
            "files": files, "diff": diff[:DIFF_MAX], "diff_chars": len(diff), "exists": True}  # fmt: skip


def _versions(path: Path, base: str, files: list[dict[str, Any]]) -> list[edits.Planned | None]:
    """Each file as the sandbox began (its base) and as it is (its index, after changes() added all), in git's form of
    the text - what its hunks are worked out from (17.4); None for one that is not UTF-8 text or is too big. One git
    for all of them."""
    specs = []
    for f in files:
        specs += [None if f["status"] == "added" else f"{base}:{f['path']}",
                  None if f["status"] == "deleted" else f":{f['path']}"]  # fmt: skip
    blobs = _blobs(path, [s for s in specs if s is not None])
    found = iter(blobs)
    versions = [None if s is None else next(found) for s in specs]
    out = []
    for f, before, after in zip(files, versions[0::2], versions[1::2], strict=True):
        text = all(v is None or (len(v) <= edits.MAX_BYTES and b"\0" not in v and _utf8(v)) for v in (before, after))
        out.append(edits.Planned(f["path"], path / f["path"], before, after or b"") if text else None)
    return out


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _blobs(path: Path, specs: list[str]) -> list[bytes | None]:
    """The objects `specs` name («<commit>:<path>», «:<path>» - the index), read by one `git cat-file --batch`;
    None for one that is missing."""
    if not specs:
        return []
    out = _run(path, "cat-file", "--batch", stdin="".join(f"{s}\n" for s in specs).encode("utf-8"))
    found: list[bytes | None] = []
    at = 0
    for _spec in specs:
        end = out.index(b"\n", at)
        header = out[at:end].split()
        at = end + 1
        if header[-1:] in ([b"missing"], [b"ambiguous"]):  # «<what was asked> missing» - spaces and all
            found.append(None)
            continue
        size = int(header[2])
        found.append(out[at : at + size])
        at += size + 1  # the object, and the newline after it
    return found


def chosen(files: list[dict[str, Any]], only: list[str]) -> list[dict[str, Any]]:
    """The sandbox's files the user ticked (17.1): at least one, and each of them there."""
    if not only:
        raise SandboxError("no file chosen")
    there = {f["path"] for f in files}
    if missing := [name for name in only if name not in there]:
        raise SandboxError("not in the sandbox: " + ", ".join(missing))
    return [f for f in files if f["path"] in set(only)]


def parts(files: list[dict[str, Any]], hunks: dict[str, list[int]]) -> dict[str, list[int]]:
    """The hunks ticked of the files applied (17.4): each file among them, with hunks to choose, at least one ticked,
    each of its own; a file with all of them ticked goes whole (it is not in the result)."""
    by_name = {f["path"]: f for f in files}
    out = {}
    for name, picked in hunks.items():
        if name not in by_name:
            raise SandboxError(f"{name} is not among the files applied")
        total = len(by_name[name].get("hunks") or [])
        if total < 2:
            raise SandboxError(f"{name} has no hunks to choose: it goes whole")
        picked = sorted(set(picked))
        if not picked:
            raise SandboxError(f"no hunk of {name} chosen: untick the file instead")
        if outside := [n for n in picked if not 0 <= n < total]:
            raise SandboxError(f"{name} has {total} hunks, numbered from 0: there is no hunk {outside[0]}")
        if len(picked) < total:
            out[name] = picked
    return out


def apply(folder: Path, conversation_id: str, project: Project, only: list[str] | None = None,
          hunks: dict[str, list[int]] | None = None, *, keep: bool = False) -> dict[str, Any]:  # fmt: skip
    """The sandbox's changes onto the project's working tree - all, or `only` the files ticked (17.1, D51), and of
    a file in `hunks` only those hunks (17.4). What is left (`kept`) stays in the sandbox, past its moved base; with
    nothing left, the sandbox goes - unless `keep` (the chat's goal is still open, 19.2): the goal works on there, with
    what the user took, so the base moves past all of it."""
    found = changes(folder, conversation_id, project)
    files = found["files"] if only is None else chosen(found["files"], only)
    picked = parts(files, hunks or {})
    if not found["exists"] or not files:
        if not keep:
            discard(folder, conversation_id, project)
        return {"applied": [], "conflicts": [], "kept": []}
    path = place(folder, conversation_id, project)
    base = base_of(path, conversation_id)
    partial = [f for f in files if f["path"] in picked]
    taken = {f["path"]: edits.compose(v, set(picked[f["path"]])).after
             for f, v in zip(partial, _versions(path, base, partial), strict=True) if v is not None}  # fmt: skip
    # every file worked out first: one git cannot read stops it all before the project is touched
    planned = [(f["path"], *_merge_one(project.root, path, base, f["path"], f["status"], taken.get(f["path"])))
               for f in files]  # fmt: skip
    for name, data, _clean in planned:
        target = projects.resolve_inside(project.root, name)
        if data is None:
            target.unlink(missing_ok=True)
        else:
            write_atomic(target, data)  # the whole file or nothing (D46)
    kept = [f["path"] for f in found["files"] if f not in files or f["path"] in taken]
    if kept or keep:
        _advance(project, path, conversation_id, base, [f["path"] for f in files], taken)
    else:
        discard(folder, conversation_id, project)
    return {"applied": [name for name, _data, _clean in planned],
            "conflicts": [name for name, _data, clean in planned if not clean], "kept": kept}  # fmt: skip


def _advance(project: Project, path: Path, conversation_id: str, base: str, names: list[str],
             taken: dict[str, bytes] | None = None) -> None:  # fmt: skip
    """The sandbox's base, moved past the files that went onto the project: the commit it began from, with those
    files as the sandbox's index has them (changes() put everything there) - or, of a file taken in part (17.4), as
    that part is - built in an index of its own, so the sandbox's index, the user's and their branches stay as they
    are."""
    staged = {}
    for entry in _git(path, "ls-files", "-s", "-z").split("\0"):
        if entry:
            info, name = entry.split("\t", 1)
            staged[name] = info.split()  # mode, object, stage
    for name, data in (taken or {}).items():
        staged[name] = [staged[name][0], _git(path, "hash-object", "-w", "--no-filters", "--stdin", stdin=data).strip()]
    gone = "0" * len(base)  # mode 0 takes a path out of the index
    lines = [f"{staged[n][0]} {staged[n][1]}\t{n}" if n in staged else f"0 {gone}\t{n}" for n in names]
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(path, "read-tree", base, env=env)
        _git(path, "update-index", "-z", "--index-info", stdin="\0".join([*lines, ""]).encode("utf-8"), env=env)
        tree = _git(path, "write-tree", env=env).strip()
    commit = _git(path, *IDENTITY, "commit-tree", tree, "-p", base, "-m", "AFO: applied to the project").strip()
    _git(project.root, "update-ref", base_ref(conversation_id), commit, retry=True)


def _clean(root: Path, name: str, data: bytes) -> bytes:
    """The bytes as git keeps them for this path (core.autocrlf, .gitattributes): a file saved with CRLF and the
    same file with LF are one to git - so they are to the merge."""
    blob = _git(root, "hash-object", "-w", "--stdin", f"--path={name}", stdin=data).strip()
    return _run(root, "cat-file", "blob", blob)


def _smudge(root: Path, name: str, data: bytes) -> bytes:
    """The other way: git's bytes as checkout puts them on disk here for this path."""
    blob = _git(root, "hash-object", "-w", "--no-filters", "--stdin", stdin=data).strip()
    return _run(root, "cat-file", "--filters", f"--path={name}", blob)


def _merge_one(root: Path, sandbox: Path, base_rev: str, name: str, status: str,
               part: bytes | None = None) -> tuple[bytes | None, bool]:  # fmt: skip
    """One file of the sandbox as the project's working tree should have it, the project's own changes kept: a
    three-way merge with the commit both began from, done in git's own form of the text and put back in the form
    this computer checks out (core.autocrlf, .gitattributes - Windows line ends are no conflict, D46). (bytes, or None
    for a file to delete; False when both changed the same place - the bytes then hold conflict marks, or (a binary
    one, or deleted on one side and changed on the other) are the project's as they are). `part`: the file as the
    hunks ticked make it (17.4), in git's form - merged in place of the sandbox's whole file."""
    target = root / name
    mine = target.read_bytes() if target.is_file() else None
    if part is not None:
        theirs = _smudge(root, name, part)
    else:
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
        marks = ["-L", tr("проект"), "-L", tr("начало"), "-L", tr("песочница")]  # what a conflict's lines say
        done = subprocess.run(["git", "merge-file", "-p", *marks,
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
