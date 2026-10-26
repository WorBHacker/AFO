"""Searching a project's files (stage 2, D26): ripgrep when it is there, a plain walk when it is not.

ripgrep lives in tools/ripgrep (fetched from its GitHub release, not kept in the repository) or on PATH. It follows
.gitignore by itself; cache folders (node_modules, .venv, ...) are left out in any project. Without ripgrep the hub
walks the files git shows (or, outside git, everything but noise) and matches lines itself - slower, same results.

Results are grouped by file: line number, the line (cut to MAX_LINE characters around the first match) and where
the matches are in it, in characters, for highlighting. At most `limit` matches in all and PER_FILE in one file.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from common.paths import PROJECT_ROOT

from . import projects
from .projects import CACHES, NOISE, ProjectError

RIPGREP = PROJECT_ROOT / "tools" / "ripgrep" / "rg.exe"
MAX_RESULTS = 500
PER_FILE = 50
MAX_LINE = 300
TIMEOUT_S = 15.0
WALK_MAX_BYTES = 2 * 1024 * 1024
CASES = ("smart", "sensitive", "insensitive")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def ripgrep() -> str | None:
    return str(RIPGREP) if RIPGREP.is_file() else shutil.which("rg")


def search(
    root: Path,
    text: str,
    *,
    regex: bool = False,
    case: str = "smart",
    glob: str = "",
    limit: int = MAX_RESULTS,
    use_ripgrep: bool = True,
) -> dict[str, Any]:
    """{engine, files: [{path, matches: [{line, text, ranges}]}], matches, truncated, elapsed_ms}."""
    if not text.strip():
        raise ProjectError("the search is empty")
    if case not in CASES:
        raise ProjectError(f"case must be one of {', '.join(CASES)}")
    limit = max(1, min(limit, 5000))
    started = time.monotonic()
    rg = ripgrep() if use_ripgrep else None
    if rg:
        files, total, truncated = _ripgrep(rg, root, text, regex, case, glob, limit)
    else:
        files, total, truncated = _walk(root, text, regex, case, glob, limit)
    return {
        "engine": "ripgrep" if rg else "walk",
        "files": files,
        "matches": total,
        "truncated": truncated,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def _trim(line: str, ranges: list[list[int]]) -> tuple[str, list[list[int]]]:
    """A long line cut around its first match (with … at the cuts); the ranges move with it."""
    line = line.rstrip("\r\n")
    if len(line) <= MAX_LINE:
        return line, ranges
    start = max(0, (ranges[0][0] if ranges else 0) - 80)
    cut = line[start : start + MAX_LINE]
    shift = start - (1 if start else 0)
    text = ("…" if start else "") + cut + ("…" if start + MAX_LINE < len(line) else "")
    moved = [[a - shift, b - shift] for a, b in ranges if a >= start and b <= start + MAX_LINE]
    return text, moved


def _add(found: dict[str, list[dict[str, Any]]], path: str, number: int, line: str, ranges: list[list[int]]) -> None:
    text, ranges = _trim(line, ranges)
    found.setdefault(path, []).append({"line": number, "text": text, "ranges": ranges})


def _grouped(found: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [{"path": path, "matches": matches} for path, matches in sorted(found.items(), key=lambda kv: kv[0].lower())]


# --- ripgrep ---


def _ripgrep(rg: str, root: Path, text: str, regex: bool, case: str, glob: str, limit: int):
    args = [rg, "--json", "--no-config", "--max-count", str(PER_FILE), "--max-filesize", "10M"]
    args.append({"smart": "--smart-case", "sensitive": "--case-sensitive", "insensitive": "--ignore-case"}[case])
    if not regex:
        args.append("--fixed-strings")
    for folder in sorted(CACHES):
        args += ["--glob", f"!{folder}/"]
    if glob:
        args += ["--glob", glob]
    args += ["--regexp", text, "."]
    process = subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    found: dict[str, list[dict[str, Any]]] = {}
    total, truncated = 0, False
    deadline = time.monotonic() + TIMEOUT_S
    try:
        for raw in process.stdout:
            if time.monotonic() > deadline:
                truncated = True
                break
            item = json.loads(raw)
            if item.get("type") != "match":
                continue
            data = item["data"]
            line_bytes = _bytes(data["lines"])
            if line_bytes is None:
                continue
            path = _bytes(data["path"]).decode("utf-8", "replace").replace("\\", "/").removeprefix("./")
            line = line_bytes.decode("utf-8", "replace")
            ranges = [
                [
                    len(line_bytes[: m["start"]].decode("utf-8", "replace")),
                    len(line_bytes[: m["end"]].decode("utf-8", "replace")),
                ]
                for m in data.get("submatches", [])
            ]
            _add(found, path, data["line_number"], line, ranges)
            total += 1
            if total >= limit:
                truncated = True
                break
    finally:
        if process.poll() is None:
            process.kill()
        error = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr else ""
        process.wait()
    if process.returncode == 2 and not total and error:
        raise ProjectError("the search failed: " + error.splitlines()[-1][:300])
    return _grouped(found), total, truncated


def _bytes(field: dict[str, Any]) -> bytes | None:
    if "text" in field:
        return field["text"].encode("utf-8")
    if "bytes" in field:  # not UTF-8: ripgrep sends base64
        return base64.b64decode(field["bytes"])
    return None


# --- without ripgrep ---


def _pattern(text: str, regex: bool, case: str) -> re.Pattern[str]:
    ignore = case == "insensitive" or (case == "smart" and text == text.lower())
    try:
        return re.compile(text if regex else re.escape(text), re.IGNORECASE if ignore else 0)
    except re.error as exc:
        raise ProjectError(f"the search failed: {exc}") from exc


def _files(root: Path, glob: str) -> list[str]:
    visible = projects._git_visible(root)
    if visible is not None:
        paths = sorted(p for p in visible if not any(part in CACHES for part in p.split("/")))
    else:
        paths = []
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in NOISE]
            rel = Path(folder).relative_to(root).as_posix()
            paths += [f"{rel}/{n}" if rel != "." else n for n in names]
    if glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, glob) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], glob)]
    return paths


def _walk(root: Path, text: str, regex: bool, case: str, glob: str, limit: int):
    pattern = _pattern(text, regex, case)
    found: dict[str, list[dict[str, Any]]] = {}
    total, truncated = 0, False
    deadline = time.monotonic() + TIMEOUT_S
    for path in _files(root, glob):
        if time.monotonic() > deadline:
            truncated = True
            break
        try:
            data = (root / path).read_bytes()
        except OSError:
            continue
        if len(data) > WALK_MAX_BYTES or b"\0" in data[:8192]:
            continue
        for encoding in ("utf-8", "cp1251"):
            try:
                content = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        in_file = 0
        for number, line in enumerate(content.splitlines(), 1):
            ranges = [[m.start(), m.end()] for m in pattern.finditer(line) if m.end() > m.start()]
            if not ranges:
                continue
            _add(found, path, number, line, ranges)
            total += 1
            in_file += 1
            if total >= limit:
                return _grouped(found), total, True
            if in_file >= PER_FILE:
                break
    return _grouped(found), total, truncated
