"""Projects (stage 2): the registry, a project's tree and files, and its git state.

Everything stays inside the project's folder: paths are resolved (symlinks and '..' included) and must land under
the project's root, so nothing outside it can be read through the hub (D17). Git is the system `git`, read-only:
status, log and diff never take the index lock (GIT_OPTIONAL_LOCKS=0), so they do not get in the user's way.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .db import utc_now
from .secret_filter import is_secret_file

# folders that are never worth watching, in any project
CACHES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".next", ".idea", ".vs",
}  # fmt: skip
# folders that are never shown in a tree that is not a git repository (in a repository, .gitignore decides)
NOISE = CACHES | {"dist", "build", "target"}
MAX_READ_BYTES = 2 * 1024 * 1024
# the hub's data folder, resolved, set as the hub starts: never served through a project
HUB_DATA: Path | None = None
BINARY_SNIFF = 8192
GIT_TIMEOUT_S = 15
MAX_DIFF_BYTES = 400_000
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class ProjectError(Exception):
    """Something about a project or a path in it is wrong; the message is fit to show."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: str
    description: str
    cloud_ok: bool
    created_at: str
    updated_at: str

    @property
    def root(self) -> Path:
        return Path(self.path)

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "exists": self.root.is_dir()}


def _row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        description=row["description"],
        cloud_ok=bool(row["cloud_ok"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# --- the registry ---


def normalize_root(path: str) -> Path:
    try:
        root = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProjectError(f"no such folder: {path}") from exc
    if not root.is_dir():
        raise ProjectError(f"not a folder: {path}")
    if root.parent == root:
        raise ProjectError("a whole drive cannot be a project; choose its folder")
    return root


def add_project(conn: sqlite3.Connection, path: str, name: str = "") -> Project:
    root = normalize_root(path)
    if conn.execute("SELECT 1 FROM projects WHERE path = ? COLLATE NOCASE", (str(root),)).fetchone():
        raise ProjectError(f"already a project: {root}")
    project_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO projects (id, name, path) VALUES (?, ?, ?)", (project_id, name.strip() or root.name, str(root))
    )
    return get_project(conn, project_id)


def list_projects(conn: sqlite3.Connection) -> list[Project]:
    """The user's projects - not the proving ground's copies (D45: they live in their runs, by id)."""
    return [_row(r) for r in conn.execute(
        "SELECT * FROM projects WHERE scratch = 0 ORDER BY name COLLATE NOCASE").fetchall()]  # fmt: skip


def get_project(conn: sqlite3.Connection, project_id: str) -> Project:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ProjectError(f"no such project: {project_id}")
    return _row(row)


def update_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    cloud_ok: bool | None = None,
) -> Project:
    project = get_project(conn, project_id)
    conn.execute(
        "UPDATE projects SET name = ?, description = ?, cloud_ok = ?, updated_at = ? WHERE id = ?",
        (
            (name.strip() or project.name) if name is not None else project.name,
            description if description is not None else project.description,
            int(cloud_ok) if cloud_ok is not None else int(project.cloud_ok),
            utc_now(),
            project_id,
        ),
    )
    return get_project(conn, project_id)


def set_path(conn: sqlite3.Connection, project_id: str, path: Path) -> Project:
    """The project's folder moved (renamed on the same disk, hub/watch.py found it): its new place."""
    conn.execute("UPDATE projects SET path = ?, updated_at = ? WHERE id = ?",
                 (str(path.resolve()), utc_now(), project_id))  # fmt: skip
    return get_project(conn, project_id)


def remove_project(conn: sqlite3.Connection, project_id: str) -> bool:
    return bool(conn.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount)


# --- inside a project ---


def resolve_inside(root: Path, rel: str) -> Path:
    """`rel` (a path relative to the project) as an absolute path, refused if it would leave the project or reach
    into the hub's own data."""
    target = (root / rel.replace("\\", "/").lstrip("/")).resolve()
    if not target.is_relative_to(root):
        raise ProjectError("the path leaves the project")
    if private(root, target):
        raise ProjectError("the hub's own data (its database, tokens, logs) is not read or written through a project")
    return target


def keep_private(folder: Path) -> None:
    """The hub's data folder: from now on no path through a project reaches into it."""
    global HUB_DATA
    HUB_DATA = folder.resolve()


def secret_file(root: Path, rel: str) -> bool:
    """A secret file (D10) by the name asked for, or by the file it really is: a link named notes.txt that leads to
    .env is .env. A path that leads nowhere is not one here - reading it says why."""
    if is_secret_file(rel):
        return True
    try:
        return is_secret_file(resolve_inside(root, rel).name)
    except ProjectError, OSError:
        return False


def private(root: Path, target: Path) -> bool:
    """`target` lies in the hub's data folder while the project does not (a checkout of LocalChat keeps it inside:
    tokens, the database, logs, snapshots - D46). A sandbox's or the proving ground's copy lives in that folder and
    is a project of its own: inside it, everything is its."""
    return HUB_DATA is not None and target.is_relative_to(HUB_DATA) and not root.resolve().is_relative_to(HUB_DATA)


def private_part(root: Path) -> str:
    """The hub's data folder as a path inside this project ("data/"), when the project holds it; else ""."""
    if HUB_DATA is None:
        return ""
    real = root.resolve()
    return HUB_DATA.relative_to(real).as_posix() + "/" if real != HUB_DATA and HUB_DATA.is_relative_to(real) else ""


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path != root else ""


def git(
    root: Path, *args: str, timeout: float = GIT_TIMEOUT_S, input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"}
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input,
        capture_output=True,
        timeout=timeout,
        env=env,
        creationflags=_NO_WINDOW,
        check=False,
    )


def is_repo(root: Path) -> bool:
    try:
        result = git(root, "rev-parse", "--is-inside-work-tree")
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and result.stdout.strip() == b"true"


def git_ignored(root: Path, paths: set[str]) -> set[str]:
    """Which of `paths` (relative, posix) git ignores; nothing outside a repository. A tracked file is never
    ignored, whatever .gitignore says."""
    if not paths or not is_repo(root):
        return set()
    result = git(root, "check-ignore", "--stdin", "-z", input="\0".join(sorted(paths)).encode("utf-8"))
    if result.returncode not in (0, 1):  # 1: none of them is ignored
        return set()
    return {p for p in result.stdout.decode("utf-8", "replace").split("\0") if p}


def _git_visible(root: Path) -> set[str] | None:
    """Files git does not ignore (tracked or not), as posix paths; None outside a repository."""
    if not is_repo(root):
        return None
    result = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode != 0:
        return None
    return {p for p in result.stdout.decode("utf-8", "replace").split("\0") if p}


def tree(root: Path, rel: str = "") -> list[dict[str, Any]]:
    """One folder's entries, folders first: name, path (relative, posix), dir, size. What .gitignore hides is left
    out; outside git, well-known noise (node_modules, .venv, caches) is."""
    folder = resolve_inside(root, rel)
    if not folder.is_dir():
        raise ProjectError(f"not a folder: {rel or '.'}")
    visible = _git_visible(root)
    prefix = _rel(root, folder)
    prefix = f"{prefix}/" if prefix else ""
    shown_dirs: set[str] = set()  # sub-folders that hold at least one file git shows
    for visible_path in visible or ():
        if visible_path.startswith(prefix):
            head, slash, _ = visible_path[len(prefix) :].partition("/")
            if slash:
                shown_dirs.add(head)
    entries = []
    with os.scandir(folder) as found:
        for entry in found:
            path = f"{prefix}{entry.name}"
            is_dir = entry.is_dir(follow_symlinks=False)
            if visible is not None:
                if (is_dir and entry.name not in shown_dirs) or (not is_dir and path not in visible):
                    continue
            elif entry.name in NOISE:
                continue
            if is_dir and private(root, Path(entry.path)):  # the hub's data folder in a checkout of LocalChat
                continue
            try:
                size = 0 if is_dir else entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            secret = not is_dir and is_secret_file(path)  # models never get its content (D10)
            entries.append({"name": entry.name, "path": path, "dir": is_dir, "size": size, "secret": secret})
    entries.sort(key=lambda e: (not e["dir"], e["name"].casefold()))
    return entries


def read_file(root: Path, rel: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    """A file's text (UTF-8, else the Windows code page), or a note that it is binary or too big."""
    path = resolve_inside(root, rel)
    if not path.is_file():
        raise ProjectError(f"not a file: {rel}")
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(max_bytes)
    result: dict[str, Any] = {"path": _rel(root, path), "size": size, "truncated": size > len(data)}
    if b"\0" in data[:BINARY_SNIFF]:
        return {**result, "binary": True, "text": "", "encoding": ""}
    for encoding in ("utf-8", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text, encoding = data.decode("utf-8", "replace"), "utf-8"
    return {**result, "binary": False, "text": text, "encoding": encoding}


# --- git ---


def git_status(root: Path) -> dict[str, Any]:
    """Branch, ahead / behind, and changed files ({path, index, worktree}); `repo: false` outside git."""
    if not is_repo(root):
        return {"repo": False, "branch": "", "ahead": 0, "behind": 0, "changes": []}
    result = git(root, "status", "--porcelain=v1", "--branch", "-z", "--untracked-files=all")
    if result.returncode != 0:
        raise ProjectError("git status failed: " + result.stderr.decode("utf-8", "replace")[:200])
    parts = result.stdout.decode("utf-8", "replace").split("\0")
    branch, ahead, behind, changes = "", 0, 0, []
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        if item.startswith("## "):
            head = item[3:]
            branch = head.split("...", 1)[0].removeprefix("No commits yet on ")
            if "[" in head:
                for token in head[head.index("[") + 1 : head.rindex("]")].split(", "):
                    word, _, number = token.partition(" ")
                    if word == "ahead":
                        ahead = int(number)
                    elif word == "behind":
                        behind = int(number)
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":  # a rename is followed by its old name
            i += 1
        changes.append({"path": path, "index": code[0], "worktree": code[1]})
    return {"repo": True, "branch": branch, "ahead": ahead, "behind": behind, "changes": changes}


def git_log(root: Path, limit: int = 20) -> list[dict[str, str]]:
    """Recent commits: sha, author, when (ISO 8601 - absolute, so the same history always reads the same), subject."""
    if not is_repo(root):
        return []
    result = git(root, "log", f"-n{limit}", "--pretty=format:%h%x1f%an%x1f%aI%x1f%s")
    if result.returncode != 0:  # no commits yet
        return []
    commits = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        sha, author, when, subject = [*line.split("\x1f"), "", "", ""][:4]
        commits.append({"sha": sha, "author": author, "when": when, "subject": subject})
    return commits


def git_diff(root: Path, rel: str = "", *, staged: bool = False) -> dict[str, Any]:
    """The unified diff of the working tree (or the index) against git, for one file or the whole project.
    An untracked file is shown as all new."""
    if not is_repo(root):
        raise ProjectError("not a git repository")
    target = _rel(root, resolve_inside(root, rel)) if rel else ""
    args = (
        ["diff", "--no-color", "--no-ext-diff"] + (["--cached"] if staged else []) + (["--", target] if target else [])
    )
    text = git(root, *args).stdout.decode("utf-8", "replace")
    if not text and target and not staged:
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", target).stdout.strip()
        if untracked:
            text = git(root, "diff", "--no-color", "--no-index", "--", os.devnull, target).stdout.decode(
                "utf-8", "replace"
            )
    return {"path": target, "staged": staged, "diff": text[:MAX_DIFF_BYTES], "truncated": len(text) > MAX_DIFF_BYTES}
