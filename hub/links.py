"""Links between projects (stage 3, D25): proposed by the hub from evidence, confirmed by the user one by one.

Evidence the hub looks for, for each pair of projects A and B:
- a path: A's dependency, build or compose files point at B's folder (a local path dependency, `file:../b`, a git
  submodule, `build: ../b` in docker-compose) - A uses B, or they deploy together;
- a package: A depends on the package B builds (the name in B's pyproject.toml / package.json / Cargo.toml);
- a port: something in A's files talks to localhost:P, and a process in B's folder listens on P - A uses B's API;
- the environment: A and B name the same settings (two or more, not the everyday ones like PORT) in their
  `.env.example` or docker-compose files - they share a database, a queue, a config.
Each proposal keeps its evidence as lines a person can check. A confirmed link joins the map (L0) and lets a chat
with A see B's live state too (D25); a rejected one is not proposed again.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import projects, search
from .db import utc_now
from .projects import Project, ProjectError

KINDS = {
    "uses": "использует",
    "provides": "предоставляет",
    "shares": "общее",
    "deploys_with": "развёртываются вместе",
    "same_product": "части одного продукта",
    "other": "связь",
}
DEPENDENCY_FILES = (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "package.json", "Cargo.toml", "go.mod",
    "composer.json", "pom.xml", "build.gradle", "build.gradle.kts",
)  # fmt: skip
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
PATHLIKE = re.compile(r"""(?:file:)?((?:[A-Za-z]:[\\/]|\.\.?[\\/]|/)[^\s"'`,;)\]}]+)""")
PORT_QUERY = r"(localhost|127\.0\.0\.1|0\.0\.0\.0):{port}\b"
MAX_PORT_HITS = 50
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
# where a project names its settings; the real .env is a secret file and is not read
ENV_FILES = (".env.example", ".env.sample", ".env.template", ".env.dist", *COMPOSE_FILES)
ENV_NAME = re.compile(r"^\s*(?:export\s+|-\s*)?([A-Z][A-Z0-9_]{2,})\s*[=:]", re.M)
EVERYDAY = {
    "PORT", "HOST", "DEBUG", "ENV", "NODE_ENV", "APP_ENV", "ENVIRONMENT", "PATH", "HOME", "TZ", "LANG", "LOG_LEVEL",
    "LOGLEVEL", "PYTHONPATH", "PYTHONUNBUFFERED", "VERSION", "SECRET_KEY", "TIMEOUT", "WORKERS",
}  # fmt: skip
MIN_SHARED = 2


class LinkError(Exception):
    """The message is fit to show."""


# --- finding evidence ---


def _read(root: Path, name: str) -> str:
    try:
        data = projects.read_file(root, name, max_bytes=256 * 1024)
    except ProjectError:
        return ""
    return "" if data["binary"] else data["text"]


def package_name(project: Project) -> str | None:
    """The name of the package the project builds, if it says (pyproject.toml, package.json, Cargo.toml)."""
    root = project.root
    text = _read(root, "pyproject.toml")
    match = re.search(r"^\[project\][^\[]*?^name\s*=\s*[\"']([^\"']+)", text, re.M | re.S)
    if match:
        return match.group(1)
    text = _read(root, "package.json")
    if text:
        try:
            name = json.loads(text).get("name")
            if isinstance(name, str) and name:
                return name
        except ValueError, AttributeError:
            pass
    match = re.search(r"^\[package\][^\[]*?^name\s*=\s*\"([^\"]+)\"", _read(root, "Cargo.toml"), re.M | re.S)
    return match.group(1) if match else None


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def path_evidence(a: Project, b: Project) -> list[tuple[str, str]]:
    """(kind, evidence line) for each file of A that points at B's folder."""
    found = []
    for name in (*DEPENDENCY_FILES, *COMPOSE_FILES, ".gitmodules"):
        text = _read(a.root, name)
        for number, line in enumerate(text.splitlines(), 1):
            for raw in PATHLIKE.findall(line):
                target = Path(raw.rstrip("/\\")) if Path(raw).is_absolute() else a.root / raw
                if _inside(target, b.root):
                    kind = "deploys_with" if name in COMPOSE_FILES else "uses"
                    found.append((kind, f"{a.name}/{name}:{number} указывает на папку {b.name}: {line.strip()[:160]}"))
                    break
    return found


def package_evidence(a: Project, b: Project, b_package: str | None) -> list[tuple[str, str]]:
    if not b_package:
        return []
    pattern = re.compile(rf"""(?<![\w.-]){re.escape(b_package)}(?![\w.-])""", re.I)
    found = []
    for name in DEPENDENCY_FILES:
        for number, line in enumerate(_read(a.root, name).splitlines(), 1):
            if pattern.search(line):
                found.append(("uses", f"{a.name}/{name}:{number} зависит от пакета «{b_package}» проекта {b.name}"))
                break
    return found


def port_evidence(a: Project, b: Project, b_ports: dict[int, str]) -> list[tuple[str, str]]:
    found = []
    for port, process in sorted(b_ports.items()):
        try:
            result = search.search(a.root, PORT_QUERY.format(port=port), regex=True, limit=MAX_PORT_HITS)
        except ProjectError:
            continue
        # code that calls the port says more than a document that mentions it
        hits = sorted(result["files"], key=lambda item: item["path"].lower().endswith(DOC_SUFFIXES))
        for item in hits[:1]:
            match = item["matches"][0]
            found.append((
                "uses",
                f"{a.name}/{item['path']}:{match['line']} обращается к :{port} ({match['text'].strip()[:120]}); "
                f"в папке {b.name} этот порт слушает {process}",
            ))  # fmt: skip
    return found


def env_names(project: Project) -> set[str]:
    """The settings a project names in its example env and compose files (names only, never values)."""
    names: set[str] = set()
    for name in ENV_FILES:
        names |= set(ENV_NAME.findall(_read(project.root, name)))
    return names - EVERYDAY


def env_evidence(a: Project, b: Project, a_names: set[str], b_names: set[str]) -> list[tuple[str, str]]:
    shared = sorted(a_names & b_names)
    if len(shared) < MIN_SHARED:
        return []
    listed = ", ".join(shared[:8]) + (f" и ещё {len(shared) - 8}" if len(shared) > 8 else "")
    return [("shares", f"{a.name} и {b.name} называют одни и те же настройки окружения: {listed}")]


def scan(all_projects: list[Project], ports: dict[str, dict[int, str]]) -> list[dict[str, Any]]:
    """Proposals {a_id, b_id, kind, evidence: [lines]} for every pair with evidence. `ports`: project id -> {port:
    process name} of what listens in its folder now."""
    present = [p for p in all_projects if p.root.is_dir()]
    names = {p.id: package_name(p) for p in present}
    settings = {p.id: env_names(p) for p in present}
    proposals: dict[tuple[str, str, str], list[str]] = {}
    for a in present:
        for b in present:
            if a.id == b.id or _inside(a.root, b.root) or _inside(b.root, a.root):
                continue  # nested folders are one tree, not two projects talking
            found = path_evidence(a, b) + package_evidence(a, b, names[b.id]) + port_evidence(a, b, ports.get(b.id, {}))
            if a.id < b.id:  # sharing goes both ways: one proposal per pair
                found += env_evidence(a, b, settings[a.id], settings[b.id])
            for kind, line in found:
                proposals.setdefault((a.id, b.id, kind), []).append(line)
    return [{"a_id": a, "b_id": b, "kind": k, "evidence": lines} for (a, b, k), lines in proposals.items()]


# --- storing ---


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {**{k: row[k] for k in ("id", "a_id", "b_id", "kind", "description", "status", "created_at")},
            "evidence": json.loads(row["evidence"])}  # fmt: skip


def list_links(conn: sqlite3.Connection, *, include_rejected: bool = False) -> list[dict[str, Any]]:
    where = "" if include_rejected else "WHERE status != 'rejected'"
    return [_row(r) for r in conn.execute(f"SELECT * FROM project_links {where} ORDER BY id").fetchall()]


def store_proposals(conn: sqlite3.Connection, proposals: list[dict[str, Any]]) -> int:
    """New proposals are added; a proposed link gets its evidence refreshed; confirmed and rejected ones stay as the
    user left them. Returns how many are new."""
    added = 0
    for p in proposals:
        row = conn.execute(
            "SELECT id, status FROM project_links WHERE a_id = ? AND b_id = ? AND kind = ?",
            (p["a_id"], p["b_id"], p["kind"]),
        ).fetchone()
        evidence = json.dumps(p["evidence"], ensure_ascii=False)
        if row is None:
            conn.execute(
                "INSERT INTO project_links (a_id, b_id, kind, evidence) VALUES (?, ?, ?, ?)",
                (p["a_id"], p["b_id"], p["kind"], evidence),
            )
            added += 1
        elif row["status"] == "proposed":
            conn.execute(
                "UPDATE project_links SET evidence = ?, updated_at = ? WHERE id = ?", (evidence, utc_now(), row["id"])
            )
    return added


def update_link(conn: sqlite3.Connection, link_id: int, **changes: Any) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM project_links WHERE id = ?", (link_id,)).fetchone()
    if row is None:
        raise LinkError("no such link")
    link = _row(row)
    if changes.get("status") not in (None, "proposed", "confirmed", "rejected"):
        raise LinkError("status must be proposed, confirmed or rejected")
    if changes.get("kind") not in (None, *KINDS):
        raise LinkError(f"kind must be one of {', '.join(KINDS)}")
    merged = {**link, **{k: v for k, v in changes.items() if v is not None}}
    try:
        conn.execute(
            "UPDATE project_links SET kind = ?, description = ?, status = ?, updated_at = ? WHERE id = ?",
            (merged["kind"], merged["description"].strip(), merged["status"], utc_now(), link_id),
        )
    except sqlite3.IntegrityError as exc:
        raise LinkError("such a link exists already") from exc
    return _row(conn.execute("SELECT * FROM project_links WHERE id = ?", (link_id,)).fetchone())


def add_link(conn: sqlite3.Connection, a_id: str, b_id: str, kind: str, description: str) -> dict[str, Any]:
    """A link the user states themself: confirmed at once."""
    if a_id == b_id:
        raise LinkError("a project cannot be linked to itself")
    if kind not in KINDS:
        raise LinkError(f"kind must be one of {', '.join(KINDS)}")
    try:
        link_id = conn.execute(
            "INSERT INTO project_links (a_id, b_id, kind, description, status) VALUES (?, ?, ?, ?, 'confirmed')",
            (a_id, b_id, kind, description.strip()),
        ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise LinkError("such a link exists already, or a project is unknown") from exc
    return _row(conn.execute("SELECT * FROM project_links WHERE id = ?", (link_id,)).fetchone())


def delete_link(conn: sqlite3.Connection, link_id: int) -> bool:
    return bool(conn.execute("DELETE FROM project_links WHERE id = ?", (link_id,)).rowcount)


def map_lines(links: list[dict[str, Any]], names: dict[str, str]) -> list[str]:
    """Confirmed links for the ecosystem map (L0): 'frontend -> backend: uses (REST /api, port 8000)'."""
    out = []
    for link in links:
        if link["status"] != "confirmed" or link["a_id"] not in names or link["b_id"] not in names:
            continue
        what = link["kind"].replace("_", " ") + (f" ({link['description']})" if link["description"] else "")
        out.append(f"- {names[link['a_id']]} -> {names[link['b_id']]}: {what}")
    return out
