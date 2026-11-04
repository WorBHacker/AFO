"""Exporting a project's card into the project (stage 4, D23): CLAUDE.md, AGENTS.md or .clinerules, on the user's
button only.

Cards and notes live in the hub; the usual way for external agents is the hub's MCP (D32). Some read only the files
in the project, so the user can write the card there: the confirmed card, the notes for models, and a line about the
hub's MCP, as one block between AFO's markers. The rest of the file is the user's and stays as it is: a file
without the block gets it at the end, a file with it gets the block replaced (one written under the old name,
LocalChat, too), a missing file is created. A .clinerules folder (Cline reads every file in it) gets a file of its
own, afo.md (localchat.md before 10.4b becomes it). The file as it was is kept in data/snapshots (by its hash), and the
export goes into the feed (`card.exported`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .changes import keep
from .projects import Project
from .secret_filter import clean

TARGETS = {"claude": "CLAUDE.md", "agents": "AGENTS.md", "cline": ".clinerules"}
START = "<!-- AFO: начало карточки проекта"
BEGIN = START + ". Её обновляет кнопка «В проект» в AFO — правки внутри блока будут заменены -->"
END = "<!-- AFO: конец карточки проекта -->"
# the block is found by its markers, whatever follows the first; as written before the name AFO (10.4b) too
BLOCK = re.compile(r"<!-- (?:AFO|LocalChat): начало карточки проекта.*?"
                   r"<!-- (?:AFO|LocalChat): конец карточки проекта -->", re.DOTALL)  # fmt: skip
CLINE_FILE = "afo.md"
OLD_CLINE_FILE = "localchat.md"  # before 10.4b: the next export moves it to CLINE_FILE
HUB_LINE = (
    "Этот проект знает хаб AFO на этом компьютере. Его MCP-сервер (`http://127.0.0.1:8765/mcp`, у Claude Code "
    "из AFO — `localchat`) даёт карту всех проектов и их связей, карточки связанных проектов, их код, git, "
    "терминалы и процессы."
)


class ExportError(Exception):
    """The message is fit to show."""


@dataclass(frozen=True)
class Plan:
    """What an export would do to one file: create it, add the block at its end, replace the block, or nothing;
    `moved` - Cline's file under the old name, which `path` takes the place of."""

    target: str
    path: Path
    mode: str  # create | append | replace | unchanged
    before: str | None
    after: str
    moved: Path | None = None

    def to_json(self, root: Path) -> dict:
        return {"target": self.target, "path": self.path.relative_to(root).as_posix(), "mode": self.mode,
                "exists": self.before is not None}  # fmt: skip


def block(project: Project, card: str, notes: list[dict]) -> str:
    """The card (its headings one level down, under the block's own), the notes, the hub line - through the secret
    filter, since the file may be committed."""
    body = re.sub(r"^(#{1,5}) ", r"#\1 ", card.strip(), flags=re.MULTILINE)
    parts = [BEGIN, f"## Карточка проекта «{project.name}» (AFO)", body]
    if notes:
        parts.append("### Заметки для моделей\n" + "\n".join(f"- {n['text']}" for n in notes))
    parts += [HUB_LINE, END]
    return clean("\n\n".join(parts)) + "\n"


def target_path(project: Project, target: str) -> Path:
    if target not in TARGETS:
        raise ExportError(f"unknown target {target!r}: {', '.join(TARGETS)}")
    path = project.root / TARGETS[target]
    if target == "cline" and path.is_dir():
        path = path / CLINE_FILE
    resolved = path.resolve()
    if not resolved.is_relative_to(project.root.resolve()):
        raise ExportError(f"{TARGETS[target]} leads outside the project")
    return path


def plan(project: Project, target: str, text: str) -> Plan:
    path = target_path(project, target)
    moved = path.with_name(OLD_CLINE_FILE) if path.name == CLINE_FILE and not path.exists() else None
    moved = moved if moved is not None and moved.is_file() else None
    source = moved or path
    try:
        before = source.read_text(encoding="utf-8", newline="") if source.exists() else None  # line ends as they are
    except UnicodeDecodeError as exc:
        raise ExportError(f"{source.name} is not UTF-8 text: the hub will not change it") from exc
    except OSError as exc:
        raise ExportError(f"{source.name} cannot be read: {exc}") from exc
    if before is None:
        return Plan(target, path, "create", None, text)
    crlf = "\r\n" in before
    ours = text.replace("\n", "\r\n") if crlf else text
    if path.name == CLINE_FILE:  # a file of its own in .clinerules/
        after = ours
    elif BLOCK.search(before):
        after = BLOCK.sub(lambda _m: ours.rstrip("\r\n"), before, count=1)
    else:
        newline = "\r\n" if crlf else "\n"
        after = before.rstrip("\r\n") + newline * 2 + ours if before.strip() else ours
    if after == before and moved is None:
        return Plan(target, path, "unchanged", before, after)
    return Plan(target, path, "replace" if BLOCK.search(before) or path.name == CLINE_FILE else "append", before,
                after, moved)  # fmt: skip


def write(item: Plan, snapshots: Path) -> str | None:
    """Writes the planned file; returns the snapshot of what was there."""
    kept = keep(snapshots, item.before.encode("utf-8")) if item.before is not None else None
    if item.mode != "unchanged":
        item.path.parent.mkdir(parents=True, exist_ok=True)
        item.path.write_bytes(item.after.encode("utf-8"))
    if item.moved is not None:
        item.moved.unlink(missing_ok=True)
    return kept
