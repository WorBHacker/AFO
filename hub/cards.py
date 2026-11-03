"""Project cards (stage 3, D23, D24): what a model should know about a project before it reads any of it.

A model writes a draft from what the hub gathers - the README, the dependency files, the folder tree (as git
shows it), the project's own notes for agents (CLAUDE.md, AGENTS.md...), recent commits. A local model by default;
a cloud model only for a project that allows the cloud (D10). The user edits the draft and confirms it; every
confirmed version is kept and any can be brought back. The confirmed card is the project's L1 layer (D22) in place
of the automatic summary. A card that names a file or folder that is gone says so where it is used.

Each confirmed version remembers what it was confirmed against - git HEAD, the top-level folders, the dependency
files. When the project has moved on noticeably since, the hub says the card may be outdated, and a model can draft
an update that rewrites only the sections the changes touch (D24). Noticeably (Q3, 2026-10-28): SIGNIFICANT_COMMITS
commits or more; a top-level folder came or went (as git shows the tree: an ignored `build/` does not count); in a
dependency or build file, what a card speaks of changed (`facts`: which packages - not their versions -, which
commands and entry points; in a Dockerfile, a compose file or a Makefile, anything but blank lines and comments).

Cards live in the hub; nothing is written into the project (D23 - an export comes later, on request).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import tomllib
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
NO_CHANGES = "БЕЗ ИЗМЕНЕНИЙ"
UPDATE_INSTRUCTIONS = (
    "В теге <card> - карточка проекта, которую подтвердил пользователь; в теге <changes> - что изменилось в проекте "
    "с тех пор. Выведи только те разделы карточки, которые из-за этих изменений стали неверными или неполными: "
    "каждый целиком, с его заголовком ## как в карточке и исправленным текстом. Разделы, которых изменения не "
    "касаются, не выводи. Исправляй только по фактам из <changes>, ничего не выдумывай. Без тегов и пояснений. "
    f"Если менять нечего, выведи: {NO_CHANGES}"
)
SIGNIFICANT_COMMITS = 20  # this many commits since the card was confirmed make it worth a look
REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")  # a package's name at the start of a requirement
PACKAGE_JSON_DEPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
PACKAGE_JSON_SAYS = ("scripts", "bin", "main", "type", "workspaces", "engines")  # how it is run and built
PATH_IN_TEXT = re.compile(r"`([A-Za-z0-9_.\-/\\]+/?)`")


class CardError(Exception):
    """The message is fit to show."""


# --- what the draft is made from ---


def _file_head(root, rel: str, limit: int) -> str:
    if projects.secret_file(root, rel):  # a link or a hard link of a secret file (D46)
        return ""
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


# --- what the card was confirmed against, and what changed since (D24) ---


def _requirement(text: str) -> str:
    found = REQUIREMENT.match(text)
    return re.sub(r"[-_.]+", "-", found.group(1)).lower() if found else text.strip()


def _pyproject(data: dict[str, Any]) -> dict[str, Any]:
    project, poetry = data.get("project", {}), data.get("tool", {}).get("poetry", {})
    deps = {_requirement(r) for r in project.get("dependencies", [])}
    for extra in project.get("optional-dependencies", {}).values():
        deps |= {_requirement(r) for r in extra}
    for group in data.get("dependency-groups", {}).values():
        deps |= {_requirement(r) for r in group if isinstance(r, str)}
    deps |= set(poetry.get("dependencies", {})) | set(poetry.get("dev-dependencies", {}))
    for group in poetry.get("group", {}).values():
        deps |= set(group.get("dependencies", {}))
    return {"deps": sorted(deps), "scripts": {**poetry.get("scripts", {}), **project.get("scripts", {})},
            "gui": project.get("gui-scripts", {}), "python": project.get("requires-python"),
            "build": data.get("build-system", {}).get("build-backend"),
            "workspace": data.get("tool", {}).get("uv", {}).get("workspace")}  # fmt: skip


def facts(name: str, data: bytes) -> Any:
    """What a card may say about a dependency or build file: which packages (not their versions - a version bump
    is not worth a look at the card), which commands and entry points; for the rest (a Dockerfile, a compose file,
    a Makefile), its lines without blanks and comments. The file's text when it does not parse."""
    text = data.decode("utf-8", "replace")
    try:
        match name:
            case "package.json":
                d = json.loads(text)
                deps = sorted({k for key in PACKAGE_JSON_DEPS for k in d.get(key) or {}})
                return {"deps": deps, **{k: d.get(k) for k in PACKAGE_JSON_SAYS}}
            case "composer.json":
                d = json.loads(text)
                return {"deps": sorted({*(d.get("require") or {}), *(d.get("require-dev") or {})}),
                        "scripts": d.get("scripts"), "autoload": d.get("autoload")}  # fmt: skip
            case "pyproject.toml":
                return _pyproject(tomllib.loads(text))
            case "Cargo.toml":
                d = tomllib.loads(text)
                deps = {k: sorted(d.get(k) or {}) for k in ("dependencies", "dev-dependencies", "build-dependencies")}
                return {**deps, "bin": d.get("bin"), "workspace": (d.get("workspace") or {}).get("members")}
            case "requirements.txt":
                lines = (line.strip() for line in text.splitlines())
                return sorted({_requirement(line) for line in lines if line and not line.startswith(("#", "-"))})
            case "go.mod":
                return sorted(set(re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v\d", text, re.M)))
            case "Gemfile":
                return sorted(set(re.findall(r"^\s*gem\s+[\"']([^\"']+)", text, re.M)))
    except ValueError:  # JSONDecodeError and TOMLDecodeError are both
        return text
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith(("#", "//"))]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _head_sha(root: Path) -> str | None:
    result = projects.git(root, "rev-parse", "HEAD")
    return result.stdout.decode().strip() if result.returncode == 0 else None


def snapshot(project: Project) -> dict[str, Any]:
    """The project as the card is confirmed: git HEAD, top-level folders, digests of the dependency files - of their
    bytes (`deps`; what versions confirmed before 2026-10-28 are compared by) and of their `facts`."""
    root = project.root
    if not root.is_dir():
        return {}
    try:
        top = projects.tree(root)
    except ProjectError, OSError:
        top = []
    deps, said = {}, {}
    for name in DEPENDENCY_FILES:
        try:
            data = (root / name).read_bytes()
        except OSError:
            continue
        deps[name], said[name] = hashlib.sha256(data).hexdigest()[:16], _digest(facts(name, data))
    folders = sorted(e["name"] for e in top if e["dir"])
    return {"head": _head_sha(root), "folders": folders, "deps": deps, "facts": said}


def changes_since(project: Project, baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    """What changed since the card was confirmed: {commits, new_folders, gone_folders, dependencies, significant};
    None when the version has no baseline (confirmed before baselines were kept) or the folder is gone."""
    if not baseline or not project.root.is_dir():
        return None
    now = snapshot(project)
    commits = None
    if baseline.get("head") and now.get("head"):
        if baseline["head"] == now["head"]:
            commits = 0
        else:
            result = projects.git(project.root, "rev-list", "--count", f"{baseline['head']}..HEAD")
            commits = int(result.stdout.decode().strip()) if result.returncode == 0 else None  # rewritten history
    before, after = set(baseline.get("folders", [])), set(now["folders"])
    kind = "facts" if "facts" in baseline else "deps"  # a baseline kept before 2026-10-28: bytes
    old_deps, new_deps = baseline.get(kind, {}), now[kind]
    changed = sorted(name for name in set(old_deps) | set(new_deps) if old_deps.get(name) != new_deps.get(name))
    found = {"commits": commits, "new_folders": sorted(after - before), "gone_folders": sorted(before - after),
             "dependencies": changed}  # fmt: skip
    found["significant"] = bool(
        found["new_folders"] or found["gone_folders"] or changed or (commits or 0) >= SIGNIFICANT_COMMITS
    )
    return found


def update_prompt(project: Project, card: str, baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    """For a draft that brings a confirmed card up to date: the card, what changed, and the material it touches."""
    root = project.root
    if not root.is_dir():
        raise CardError(f"the project's folder is missing: {project.path}")
    changes = changes_since(project, baseline)
    parts = []
    if changes is None:  # no baseline: what the project is now, in full
        parts.append("## Проект сейчас\n" + gather(project))
    else:
        said = []
        if changes["commits"]:
            said.append(f"коммитов: {changes['commits']}")
        if changes["new_folders"]:
            said.append("новые папки: " + ", ".join(changes["new_folders"]))
        if changes["gone_folders"]:
            said.append("исчезли папки: " + ", ".join(changes["gone_folders"]))
        if changes["dependencies"]:
            said.append("изменились файлы зависимостей и сборки: " + ", ".join(changes["dependencies"]))
        parts.append("## Что изменилось с подтверждения\n" + ("; ".join(said) or "заметных изменений нет"))
        if baseline and baseline.get("head") and changes["commits"]:
            log = projects.git(root, "log", "--format=%h %s", "-n", "40", f"{baseline['head']}..HEAD")
            if log.returncode == 0:
                parts.append("## Коммиты с подтверждения\n" + log.stdout.decode("utf-8", "replace").strip())
        tree = _tree(root, max_depth=2)
        if tree:
            parts.append("## Дерево папок сейчас\n" + "\n".join(tree))
        for name in changes["dependencies"]:
            if (root / name).is_file() and not is_secret_file(name):
                parts.append(f"## {name} (сейчас)\n{_file_head(root, name, 6_000)}")
    material = clean("\n\n".join(parts))
    if len(material) > SOURCE_BUDGET_CHARS:
        material = material[:SOURCE_BUDGET_CHARS] + "\n[... материалы обрезаны]"
    # the card last, in its own tag: a model copies what it has just read more faithfully, and does not take the
    # material for a part of the card (a local model did, with both under ## headings)
    text = f'<changes project="{project.name}">\n{material}\n</changes>\n\n<card>\n{clean(card)}\n</card>'
    return [{"role": "system", "content": UPDATE_INSTRUCTIONS}, {"role": "user", "content": text}]


def tidy_update(text: str) -> str:
    """A model's answer without what some models wrap it in: the <card> tag, a code fence."""
    text = text.strip()
    inside = re.search(r"<card>\s*(.*?)\s*(?:</card>|$)", text, re.S)
    if inside:
        text = inside.group(1)
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S)
    return (fenced.group(1) if fenced else text).strip()


def _split(text: str) -> list[tuple[str, list[str]]]:
    """(heading, lines) per ## section, in order; the text before the first heading under ""."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            sections[-1][1].append(line)
    return sections


def merge_update(card: str, answer: str) -> str | None:
    """The confirmed card with the sections the model rewrote put in place, the rest left exactly as they were -
    a model asked to copy them garbles some (a local one did, in Russian); a heading it misspelt is matched to the
    card's closest one. None when the model found nothing to change."""
    answer = tidy_update(answer)
    rewritten = [(title, lines) for title, lines in _split(answer)[1:] if title]
    if not rewritten:
        return None  # NO_CHANGES, or no sections at all
    sections = _split(card.strip())
    titles = [title for title, _lines in sections[1:]]
    for title, lines in rewritten:
        body = "\n".join(lines).strip("\n")
        match = next((t for t in titles if t.casefold() == title.casefold()), None) or next(
            iter(difflib.get_close_matches(title, titles, n=1, cutoff=0.75)), None
        )
        if match is None:
            sections.append((title, ["", body, ""]))  # a section the card did not have
            continue
        at = 1 + titles.index(match)
        sections[at] = (match, [body, ""] if body else [""])
    parts = ["\n".join(sections[0][1]).strip("\n")] if "".join(sections[0][1]).strip() else []
    for title, lines in sections[1:]:
        parts.append(f"## {title}\n" + "\n".join(lines).strip("\n"))
    return "\n\n".join(parts).strip()


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


def baseline(conn: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    """What the latest confirmed version was confirmed against (see snapshot), if it was kept."""
    row = conn.execute(
        "SELECT baseline FROM project_cards WHERE project_id = ? AND status = 'confirmed' ORDER BY version DESC "
        "LIMIT 1",
        (project_id,),
    ).fetchone()
    return json.loads(row["baseline"]) if row and row["baseline"] else None


def confirm(
    conn: sqlite3.Connection, project_id: str, text: str, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The user's text becomes the next confirmed version, with `state` (snapshot) as what it was confirmed
    against; the draft (if any) is done with."""
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
        "INSERT INTO project_cards (project_id, version, status, text, model_id, baseline) "
        "VALUES (?, ?, 'confirmed', ?, ?, ?)",
        (project_id, last + 1, text, draft["model_id"] if draft else None, json.dumps(state) if state else None),
    )
    return card_state(conn, project_id)


def restore(
    conn: sqlite3.Connection, project_id: str, version: int, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT text FROM project_cards WHERE project_id = ? AND status = 'confirmed' AND version = ?",
        (project_id, version),
    ).fetchone()
    if row is None:
        raise CardError(f"no version {version} of this card")
    return confirm(conn, project_id, row["text"], state)


def drop_draft(conn: sqlite3.Connection, project_id: str) -> bool:
    return bool(
        conn.execute("DELETE FROM project_cards WHERE project_id = ? AND status = 'draft'", (project_id,)).rowcount
    )
