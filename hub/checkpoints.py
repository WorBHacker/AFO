"""A goal's checkpoints (stage 11.6, D44; the user's plan, idea 2 «shadow git refs»): where a git project's working
files stood at the goal's start and at each stage done, kept by git itself - and the way back there when the goal
reaches a dead end.

A checkpoint is the working files as a tree (hub/judge.py's snapshot: tracked and new files, not the ignored ones,
written through a copy of the index - the user's own is never touched), in a commit of the hub's own with no parent
on any branch, kept by a ref `refs/localchat/goals/<goal>/<name>` - so git's gc keeps it, and the user's branches,
log and index never see it. Going back writes the checkpoint's files over the working ones and removes what was
added since; ignored files are not the checkpoint's to touch. A file still there is written with the line ends it
has now (the worker keeps a file's own, D46) - git keeps its text with LF, and its own checkout (CRLF on Windows)
left an LF file looking changed to `git status`; a file gone comes back as git's checkout writes it. In «Песочница» the
files are the sandbox's own and the hub goes back by itself; in the project they are the user's, and a card asks
(hub/goals.py). A sandbox's checkpoint has its base then for a parent (19.3): what the user applied to the project
after it is not taken back. The refs of goals no longer kept (ended longer ago than D30's term, or deleted with their
chat) go with the hub's pruning (hub/retention.py).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import judge, projects
from .changes import write_atomic
from .judge import JudgeError

REFS = "refs/localchat/goals"
IDENTITY = ("-c", "user.name=AFO", "-c", "user.email=afo@localhost")  # the hub's own commits (as its sandboxes')


def ref(goal_id: str, name: str) -> str:
    return f"{REFS}/{goal_id}/{name}"


def save(root: Path, goal_id: str, name: str, tree: str | None = None, base: str | None = None) -> dict[str, str]:
    """The working files now (or `tree`, taken already) as the goal's checkpoint `name`: {commit, tree}. `base`: in
    «Песочница», the sandbox's base now - the commit's parent, so going back knows what the user applied since."""
    tree = tree or judge.snapshot(root)
    parent = ("-p", base) if base else ()
    commit = judge._git(root, *IDENTITY, "commit-tree", tree, *parent, "-m", f"AFO goal {goal_id}: {name}").strip()
    judge._git(root, "update-ref", ref(goal_id, name), commit)
    return {"commit": commit, "tree": tree}


def keeping_applied(root: Path, commit: str, base: str) -> str:
    """Where going back to the sandbox's checkpoint `commit` takes its files (19.3): the checkpoint's tree, but the
    files the user applied to the project since - those that changed from the sandbox's base then (the commit's
    parent) to `base`, its base now - as `base` has them: going back must not offer to take back what the project
    has now. A checkpoint with no parent (in the project itself): its own tree."""
    tree = judge._git(root, "rev-parse", f"{commit}^{{tree}}").strip()
    then = judge._git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if not then or then[0] == base:
        return tree
    found = judge._git(root, "diff", "--raw", "--no-abbrev", "--no-renames", "-z", then[0], base).split("\0")
    lines = []
    for info, path in zip(found[0::2], found[1::2], strict=False):
        if info and path:
            _old, mode, _was, blob = info.lstrip(":").split()[:4]
            lines.append(f"{mode} {blob}\t{path}" if mode != "000000" else f"0 {'0' * len(blob)}\t{path}")
    if not lines:
        return tree
    with tempfile.TemporaryDirectory() as tmp:  # an index of its own: the sandbox's stays as it is
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        judge._git(root, "read-tree", tree, env=env)
        try:
            done = projects.git(root, "update-index", "-z", "--index-info", input="\0".join([*lines, ""]).encode(),
                                timeout=judge.GIT_TIMEOUT, env=env)  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JudgeError(f"git did not run: {exc}") from exc
        if done.returncode != 0:
            raise JudgeError(done.stderr.decode("utf-8", "replace").strip()[:300] or "git update-index failed")
        return judge._git(root, "write-tree", env=env).strip()


def plan(root: Path, tree: str) -> dict[str, list[str]]:
    """What going back to `tree` does: the files written back as they were (changed or removed since), and the ones
    removed (added since)."""
    now = judge.snapshot(root)
    found = judge._git(root, "diff", "--name-status", "--no-renames", "-z", tree, now).split("\0")
    restore, delete = [], []
    for status, path in zip(found[0::2], found[1::2], strict=False):
        if status and path:
            (delete if status.startswith("A") else restore).append(path)
    return {"restore": sorted(restore), "delete": sorted(delete)}


def _blob(root: Path, tree: str, path: str, *, checkout: bool) -> bytes:
    """A file of `tree` as git keeps it - or, `checkout`, as its checkout writes it here (core.autocrlf, attributes)."""
    args = ("cat-file", "--filters", f"{tree}:{path}") if checkout else ("cat-file", "blob", f"{tree}:{path}")
    try:
        done = projects.git(root, *args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"git did not run: {exc}") from exc
    if done.returncode != 0:
        raise JudgeError(done.stderr.decode("utf-8", "replace").strip()[:300] or "git cat-file failed")
    return done.stdout


def restore(root: Path, tree: str) -> dict[str, list[str]]:
    """The working files back as `tree` has them (what plan() says); returns what was done. Every file is read
    first: one git cannot give stops it all before anything is written."""
    planned = plan(root, tree)
    written = []
    for path in planned["restore"]:
        target = projects.resolve_inside(root, path)
        now = target.read_bytes() if target.is_file() else None
        data = _blob(root, tree, path, checkout=now is None)
        if now is not None and b"\r\n" in now and b"\0" not in data and b"\r\n" not in data:
            data = data.replace(b"\n", b"\r\n")  # its line ends as it has them now
        written.append((target, data))
    for target, data in written:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(target, data)
    for path in planned["delete"]:
        target = projects.resolve_inside(root, path)
        target.unlink(missing_ok=True)
    return planned


def prune(root: Path, live: set[str]) -> list[str]:
    """The checkpoints' refs of goals not in `live`, removed (their objects become git's to collect)."""
    if not judge.is_git(root):
        return []
    try:
        found = judge._git(root, "for-each-ref", "--format=%(refname)", REFS).split()
    except JudgeError:
        return []
    gone = [r for r in found if r.removeprefix(REFS + "/").split("/", 1)[0] not in live]
    for r in gone:
        judge._git(root, "update-ref", "-d", r)
    return sorted(gone)
