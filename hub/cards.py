"""Project cards (stage 3, D23, D24): what a model should know about a project before it reads any of it.

A model writes a draft from what the hub gathers - the README, the dependency files, the folder tree (as git
shows it), the project's own notes for agents (CLAUDE.md, AGENTS.md...), recent commits. A local model by default;
a cloud model only for a project that allows the cloud (D10). The user edits the draft and confirms it; every
confirmed version is kept and any can be brought back. The confirmed card is the project's L1 layer (D22) in place
of the automatic summary. A card that names a file or folder that is gone says so where it is used.

Cards live in the hub; nothing is written into the project (D23 - an export comes later, on request).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import projects
from .projects import NOISE, Project, ProjectError
from .secret_filter import clean, is_secret_file

SOURCE_BUDGET_CHARS = 24_000  # ~6K tokens of material for the draft
DEPENDENCY_FILES = (
    "pyproject.toml", "requirements.txt", "setup.cfg", "package.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "Gemfile", "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile", "Makefile", "justfile", "tsconfig.json",
)  # fmt: skip
NOTES = ("README.md", "README.rst", "README.txt", "README", "CLAUDE.md", "AGENTS.md", ".clinerules", "CONTRIBUTING.md")
SECTIONS = (
    "Назначение", "Устройство", "Ключевые папки и файлы", "Как запускать, тестировать, собирать", "Соглашения",
    "Известные проблемы",
)  # fmt: skip
INSTRUCTIONS = (
    "Ты пишешь карточку проекта для других моделей: короткое, точное описание, которое они получат перед работой с "
    "проектом. Пиши по-русски, в Markdown, только по фактам из материалов ниже - ничего не выдумывай; где данных нет, "
    "так и напиши. Разделы (заголовки ##): " + "; ".join(SECTIONS) + ". Пути к файлам и папкам - в `обратных "
    "кавычках`, как в проекте. До 600 слов. Выведи только карточку, без вступления."
)
PATH_IN_TEXT = re.compile(r"`([A-Za-z0-9_.\-/\\]+/?)`")


class CardError(Exception):
    """The message is fit to show."""


# --- what the draft is made from ---


def _file_head(root, rel: str, limit: int) -> str:
    try:
        data = projects.read_file(root, rel, max_bytes=limit)
    except ProjectError:
        return ""
    if data["binary"]:
        return ""
    return data["text"] + ("\n[... cut]" if data["truncated"] else "")


def _tree(root, folder: str = "", depth: int = 0, max_depth: int = 3, out: list[str] | None = None) -> list[str]:
    out = out if out is not None else []
    try:
        entries = projects.tree(root, folder)
    except ProjectError, OSError:
        return out
    for entry in entries:
        if len(out) >= 250 or entry.get("secret"):
            continue
        out.append("  " * depth + entry["name"] + ("/" if entry["dir"] else ""))
        if entry["dir"] and depth + 1 < max_depth:
            _tree(root, entry["path"], depth + 1, max_depth, out)
    return out


def gather(project: Project) -> str:
    """The material for a draft card, secret-filtered and within SOURCE_BUDGET_CHARS."""
    root = project.root
    if not root.is_dir():
        raise CardError(f"the project's folder is missing: {project.path}")
    parts = [
        f"# Проект «{project.name}»",
        f"Папка: {project.path}",
        f"Описание от пользователя: {project.description or 'нет'}",
    ]
    tree = _tree(root)
    if tree:
        parts.append("## Дерево папок (как его показывает git)\n" + "\n".join(tree))
    names = {e["name"] for e in projects.tree(root)}
    for name in NOTES:
        if name in names:
            parts.append(f"## {name}\n{_file_head(root, name, 12_000)}")
    for name in DEPENDENCY_FILES:
        if name in names and not is_secret_file(name):
            parts.append(f"## {name}\n{_file_head(root, name, 6_000)}")
    log = projects.git_log(root, 15)
    if log:
        parts.append(
            "## Последние коммиты\n" + "\n".join(f"- {c['sha']} {c['subject']} ({c['when'][:10]})" for c in log)
        )
    text = clean("\n\n".join(parts))
    return text if len(text) <= SOURCE_BUDGET_CHARS else text[:SOURCE_BUDGET_CHARS] + "\n[... материалы обрезаны]"


def prompt(project: Project) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": gather(project)},
    ]


def _known_paths(project: Project) -> tuple[set[str], set[str]]:
    """The project's files and folders (posix, relative), as git shows them or, outside git, without noise."""
    files = projects._git_visible(project.root)
    if files is None:
        files = set()
        for folder, dirs, names in os.walk(project.root):
            dirs[:] = [d for d in dirs if d not in NOISE]
            rel = Path(folder).relative_to(project.root).as_posix()
            files |= {f"{rel}/{n}" if rel != "." else n for n in names}
            if len(files) > 20_000:
                break
    folders = {path.rsplit("/", i)[0] for path in files for i in range(1, path.count("/") + 1)}
    return files, folders


def missing_paths(project: Project, text: str) -> list[str]:
    """Paths the card names in `backticks` that are not in the project any more (D24: the card is outdated there).
    Only names with a folder in them count - a bare `chat.py` or `app.ui` may be a name in a list under a folder's
    heading, or a setting - and a path is there if it is under the root or ends any path of the project (`api/`
    written under the heading of `hub/`)."""
    candidates = sorted({t.replace("\\", "/").strip("/") for t in PATH_IN_TEXT.findall(text)})
    candidates = [c for c in candidates if "/" in c and ".." not in c]
    if not candidates or not project.root.is_dir():
        return []
    files, folders = _known_paths(project)
    known = files | folders
    return [c for c in candidates if c not in known and not any(k.endswith("/" + c) for k in known)]


# --- storing ---


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "version", "status", "text", "model_id", "created_at")}


def card_state(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    """{confirmed: the latest confirmed version or None, draft: the draft or None, versions: [{version, created_at,
    model_id}] newest first}."""
    rows = conn.execute(
        "SELECT * FROM project_cards WHERE project_id = ? ORDER BY status, version DESC", (project_id,)
    ).fetchall()
    confirmed = [_row(r) for r in rows if r["status"] == "confirmed"]
    draft = next((_row(r) for r in rows if r["status"] == "draft"), None)
    return {
        "confirmed": confirmed[0] if confirmed else None,
        "draft": draft,
        "versions": [{k: c[k] for k in ("version", "created_at", "model_id")} for c in confirmed],
    }


def confirmed_text(conn: sqlite3.Connection, project_id: str) -> str | None:
    row = conn.execute(
        "SELECT text FROM project_cards WHERE project_id = ? AND status = 'confirmed' ORDER BY version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return row["text"] if row else None


def save_draft(conn: sqlite3.Connection, project_id: str, text: str, model_id: str) -> None:
    conn.execute("DELETE FROM project_cards WHERE project_id = ? AND status = 'draft'", (project_id,))
    conn.execute(
        "INSERT INTO project_cards (project_id, version, status, text, model_id) VALUES (?, 0, 'draft', ?, ?)",
        (project_id, text, model_id),
    )


def confirm(conn: sqlite3.Connection, project_id: str, text: str) -> dict[str, Any]:
    """The user's text becomes the next confirmed version; the draft (if any) is done with."""
    text = text.strip()
    if not text:
        raise CardError("an empty card cannot be confirmed")
    draft = conn.execute(
        "SELECT model_id FROM project_cards WHERE project_id = ? AND status = 'draft'", (project_id,)
    ).fetchone()
    last = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM project_cards WHERE project_id = ? AND status = 'confirmed'",
        (project_id,),
    ).fetchone()[0]
    conn.execute("DELETE FROM project_cards WHERE project_id = ? AND status = 'draft'", (project_id,))
    conn.execute(
        "INSERT INTO project_cards (project_id, version, status, text, model_id) VALUES (?, ?, 'confirmed', ?, ?)",
        (project_id, last + 1, text, draft["model_id"] if draft else None),
    )
    return card_state(conn, project_id)


def restore(conn: sqlite3.Connection, project_id: str, version: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT text FROM project_cards WHERE project_id = ? AND status = 'confirmed' AND version = ?",
        (project_id, version),
    ).fetchone()
    if row is None:
        raise CardError(f"no version {version} of this card")
    return confirm(conn, project_id, row["text"])


def drop_draft(conn: sqlite3.Connection, project_id: str) -> bool:
    return bool(
        conn.execute("DELETE FROM project_cards WHERE project_id = ? AND status = 'draft'", (project_id,)).rowcount
    )
