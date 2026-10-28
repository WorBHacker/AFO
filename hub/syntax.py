"""Do the changed files still parse? (stage 11.2, D44) - part of a goal's first contour: a file the worker left with a
syntax error goes back to it before any model judges the work.

Python files are read by Python's own parser; the other languages of the symbol index (hub/symbols.py) by
tree-sitter. tree-sitter 0.26 under Python 3.14 now and then crashes the whole process when a node's points or text
are read (5.4), so this runs in a process of its own: `python -m hub.syntax` takes the files' paths as JSON on stdin
and writes the errors as JSON on stdout - a crash costs one check, never the hub. Only node types and byte offsets
are read; the line is counted from the bytes.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024
TIMEOUT_S = 60
ROOT = Path(__file__).resolve().parent.parent  # where `hub` is importable from


class SyntaxCheckError(Exception):
    """The check itself did not run (its process died or hung): nothing is known about the files."""


def _line(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _first_error(root: Any) -> tuple[int, str]:
    """The first ERROR or MISSING node, depth first, going down only where an error is."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_missing:
            return node.start_byte, f"missing {node.type}"
        if node.type == "ERROR":
            return node.start_byte, "unexpected text"
        if node.has_error:
            stack.extend(reversed(node.children))
    return root.start_byte, "syntax error"


def check_file(path: Path) -> dict[str, Any] | None:
    """{line, error} for a file that does not parse; None when it does, or its language is not known here."""
    from .symbols import SUFFIXES, grammar

    language = SUFFIXES.get(path.suffix.lower())
    if language is None or not path.is_file() or path.stat().st_size > MAX_BYTES:
        return None
    data = path.read_bytes()
    if language == "python":
        try:
            ast.parse(data, filename=str(path))
        except SyntaxError as exc:
            return {"line": exc.lineno or 1, "error": exc.msg}
        except ValueError as exc:  # null bytes
            return {"line": 1, "error": str(exc)}
        return None
    found = grammar(language)
    if found is None:
        return None
    from tree_sitter import Parser

    root = Parser(found.language).parse(data).root_node
    if not root.has_error:
        return None
    offset, what = _first_error(root)
    return {"line": _line(data, offset), "error": what}


def check(paths: list[Path]) -> list[dict[str, Any]]:
    """The files that do not parse: [{path, line, error}], checked in a process of its own (blocking)."""
    if not paths:
        return []
    try:
        done = subprocess.run([sys.executable, "-m", "hub.syntax"], input=json.dumps([str(p) for p in paths]).encode(),
                              capture_output=True, timeout=TIMEOUT_S, cwd=ROOT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyntaxCheckError(f"the syntax check did not run: {exc}") from exc
    if done.returncode != 0:
        raise SyntaxCheckError(f"the syntax check ended with code {done.returncode}")
    return json.loads(done.stdout or b"[]")


def main() -> None:
    out = []
    for name in json.loads(sys.stdin.buffer.read() or b"[]"):
        try:
            found = check_file(Path(name))
        except OSError:
            continue
        if found:
            out.append({"path": name, **found})
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
