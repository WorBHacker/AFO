"""Focus by mention (stage 3, D27): `@project` and `@project/file` in a message, and projects named in passing.

`@project` attaches the project to the chat when the message goes out, `@project/path` also pins the file into the
chat's context. A project named without @ is only suggested: the window offers to attach it. No Qt here.
"""

from __future__ import annotations

import re
from typing import Any

MAX_CHOICES = 8
MIN_NAME = 3  # shorter project names are too common as words to suggest
TRAILING = ".,;:!?)»\"'"
OPENING = "([«\"'"  # an @ right after these still starts a mention


def from_utf16(text: str, position: int) -> int:
    """A QML text position as Python's index. QML counts UTF-16 units (an emoji is two), Python characters; and QML
    may report the old cursor of a text just cleared - the index never passes the end."""
    return len(text.encode("utf-16-le")[: max(position, 0) * 2].decode("utf-16-le", "ignore"))


def to_utf16(text: str, index: int) -> int:
    """Python's index in the text as a QML position."""
    return len(text[:index].encode("utf-16-le")) // 2


def at_word(text: str, cursor: int) -> tuple[int, str] | None:
    """The @-mention being typed at the cursor (Python's index): (where its @ is, what follows the @), or None."""
    start = cursor = min(max(cursor, 0), len(text))
    while start > 0 and not text[start - 1].isspace() and text[start - 1] != "@":
        start -= 1
    if start == 0 or text[start - 1] != "@":
        return None
    at = start - 1
    if at > 0 and not text[at - 1].isspace() and text[at - 1] not in OPENING:
        return None  # name@host, not a mention
    return at, text[start:cursor]


def project_choices(query: str, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Projects for `@query`: names starting with it first, then containing it."""
    wanted = query.casefold()
    ranked = []
    for p in projects:
        name = p["name"].casefold()
        if wanted and wanted not in name:
            continue
        ranked.append((0 if name.startswith(wanted) else 1, name, p))
    ranked.sort(key=lambda item: item[:2])
    return [
        {"kind": "project", "name": p["name"], "note": p.get("description") or p.get("path", ""),
         "insert": f"@{p['name']} "}
        for _rank, _name, p in ranked[:MAX_CHOICES]
    ]  # fmt: skip


def file_query(query: str, projects: list[dict[str, Any]]) -> tuple[dict[str, Any], str] | None:
    """For `@project/part`: the project and the part of a file's path typed so far."""
    for p in sorted(projects, key=lambda p: -len(p["name"])):
        prefix = p["name"] + "/"
        if query.casefold().startswith(prefix.casefold()):
            return p, query[len(prefix) :]
    return None


def file_choices(project: dict[str, Any], files: list[str]) -> list[dict[str, Any]]:
    return [
        {"kind": "file", "name": path, "note": project["name"], "insert": f"@{project['name']}/{path} "}
        for path in files[:MAX_CHOICES]
    ]


def _boundary_after(text: str, at: int) -> bool:
    return at >= len(text) or text[at].isspace() or text[at] in TRAILING or text[at] == "/"


def mentioned(text: str, projects: list[dict[str, Any]]) -> tuple[list[str], list[tuple[str, str]]]:
    """What a message mentions with @: project ids, and (project id, path) of files - in the order written."""
    ids: list[str] = []
    files: list[tuple[str, str]] = []
    by_length = sorted(projects, key=lambda p: -len(p["name"]))
    for at, char in enumerate(text):
        if char != "@" or (at > 0 and not text[at - 1].isspace() and text[at - 1] not in OPENING):
            continue
        rest = text[at + 1 :]
        for p in by_length:
            name = p["name"]
            if not rest.casefold().startswith(name.casefold()) or not _boundary_after(rest, len(name)):
                continue
            if p["id"] not in ids:
                ids.append(p["id"])
            if rest[len(name) : len(name) + 1] == "/":
                path = re.split(r"\s", rest[len(name) + 1 :], maxsplit=1)[0].rstrip(TRAILING)
                if path and (p["id"], path) not in files:
                    files.append((p["id"], path))
            break
    return ids, files


def named(text: str, projects: list[dict[str, Any]], skip: set[str]) -> list[dict[str, Any]]:
    """Projects the text names as a word, without @, that are not in `skip`: those the window offers to attach."""
    found = []
    for p in projects:
        name = p["name"]
        if p["id"] in skip or len(name) < MIN_NAME:
            continue
        # a whole word: not part of a path, an address or a file name ("localchat.py"), the end of a sentence is fine
        if re.search(rf"(?<![\w@/.\-]){re.escape(name)}(?![\w\-]|\.\w)", text, re.IGNORECASE):
            found.append({"id": p["id"], "name": name})
    return found
