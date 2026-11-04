"""The catalog's keys, as the code uses them (step 10.5): tr(), N_() and plural() in Python, qsTr() in QML - and the
Russian texts in the code no one marked. `python -m common.i18n_catalog` says what each catalog lacks, keeps for
nothing or got wrong; `--sync` adds what is missing (empty: to be translated) and drops what nothing uses."""

from __future__ import annotations

import argparse
import ast
import json
import re
import string
from collections.abc import Iterable
from pathlib import Path

from . import i18n

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("app", "hub", "common")
KEEP = "i18n: keep"  # on a line whose Russian text stays as written: a pattern for Russian input, a name in its words
RUSSIAN = re.compile("[А-Яа-яЁё]")  # i18n: keep
MARKS = {"tr", "N_"}
LOGS = {"debug", "info", "warning", "error", "exception", "critical"}  # log.info(...): for whoever reads the log
QML_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.S)


def _name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _text(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _python(path: Path) -> tuple[dict[str, list[int]], list[int]]:
    """The keys a Python file marks, with their lines, and the lines of the Russian texts no one marked."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    keys: dict[str, list[int]] = {}
    done: set[int] = set()  # the ids of the texts accounted for: keys, docstrings, log messages
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and _text(first.value) is not None:
                done.add(id(first.value))
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        if name in MARKS and node.args and _text(node.args[0]) is not None:
            keys.setdefault(node.args[0].value, []).append(node.lineno)
            done.add(id(node.args[0]))
        elif name == "plural" and len(node.args) == 4 and all(_text(a) is not None for a in node.args[1:]):
            keys.setdefault("|".join(a.value for a in node.args[1:]), []).append(node.lineno)
            done.update(id(a) for a in node.args[1:])
        elif name in LOGS and isinstance(node.func, ast.Attribute):
            done.update(id(part) for part in ast.walk(node))
    left = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = [p for p in node.values if _text(p) is not None]
            done.update(id(p) for p in parts)
            russian = any(RUSSIAN.search(p.value) for p in parts)
        else:
            russian = id(node) not in done and RUSSIAN.search(_text(node) or "") is not None
        kept = russian and any(KEEP in lines[i - 1] for i in range(node.lineno, node.end_lineno + 1))
        if russian and id(node) not in done and not kept:
            left.append(node.lineno)
    return keys, sorted(left)


def _unquote(token: str) -> str:
    body = token[1:-1]
    if token[0] == "'":
        body = body.replace("\\'", "'").replace('"', '\\"')
    try:
        return json.loads(f'"{body}"')
    except json.JSONDecodeError:
        return body


def _qml(path: Path) -> tuple[dict[str, list[int]], list[int]]:
    """The keys a QML file marks (qsTr), with their lines, and the lines of the Russian texts no one marked."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    keys: dict[str, list[int]] = {}
    left = []
    for match in QML_TOKEN.finditer(text):
        token = match.group()
        if token.startswith(("//", "/*")) or not RUSSIAN.search(token):
            continue
        line = text.count("\n", 0, match.start()) + 1
        if text[max(0, match.start() - 40) : match.start()].rstrip().endswith("qsTr(") and token[0] != "`":
            keys.setdefault(_unquote(token), []).append(line)
        elif KEEP not in lines[line - 1]:
            left.append(line)
    return keys, left


def _files(roots: Iterable[Path]) -> list[Path]:
    found = []
    for root in (Path(r).resolve() for r in roots):
        if root.is_file():
            found.append(root)
            continue
        found += [p for p in sorted(root.rglob("*")) if p.suffix in (".py", ".qml") and "__pycache__" not in p.parts]
    return found


def _place(path: Path, line: int) -> str:
    return f"{path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path}:{line}"


def _read(path: Path) -> tuple[dict[str, list[int]], list[int]]:
    return _python(path) if path.suffix == ".py" else _qml(path)


def keys(roots: Iterable[Path] | None = None) -> dict[str, list[str]]:
    """Every key the code marks, with the places that use it."""
    found: dict[str, list[str]] = {}
    for path in _files(roots if roots is not None else [ROOT / s for s in SOURCES]):
        for key, lines in _read(path)[0].items():
            found.setdefault(key, []).extend(_place(path, line) for line in lines)
    return found


def unmarked(paths: Iterable[Path]) -> list[str]:
    """The places of the Russian texts no one marked for translation (nor as kept: `# i18n: keep`)."""
    return [_place(path, line) for path in _files(paths) for line in _read(path)[1]]


def fields(text: str) -> set[str]:
    """str.format's fields (Python) and %1, %2 (QML's arg())."""
    try:
        python = {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError:
        python = set()
    return python | set(re.findall(r"%\d+", text))


def problems(found: dict[str, str]) -> list[str]:
    """What is wrong in a catalog: an empty or Russian translation, fields lost or added, plural forms not two."""
    said = []
    for key, value in found.items():
        if not value:
            said.append(f"{key}: not translated")
        elif RUSSIAN.search(value):
            said.append(f"{key}: Russian left")
        elif key.count("|") == 2:
            if value.count("|") != 1:
                said.append(f"{key}: not 2 forms")
        elif fields(key) != fields(value):
            said.append(f"{key}: fields {sorted(fields(key))} ≠ {sorted(fields(value))}")
    return said


def load(code: str) -> dict[str, str]:
    path = i18n.LOCALE / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save(code: str, found: dict[str, str]) -> None:
    text = json.dumps(dict(sorted(found.items())), ensure_ascii=False, indent=1) + "\n"
    (i18n.LOCALE / f"{code}.json").write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m common.i18n_catalog", description=__doc__)
    parser.add_argument("--sync", action="store_true", help="add the missing keys (empty) and drop the unused")
    args = parser.parse_args(argv)
    used = keys()
    bad = 0
    for code in i18n.LANGUAGES:
        if code == i18n.SOURCE:
            continue
        found = load(code)
        missing, unused = sorted(set(used) - set(found)), sorted(set(found) - set(used))
        print(f"{code}: {len(used)} texts, {len(missing)} missing, {len(unused)} unused")
        for key in missing[:30]:
            print(f"  missing {key!r} ({used[key][0]})")
        if args.sync:
            save(code, {**{k: v for k, v in found.items() if k in used}, **dict.fromkeys(missing, "")})
            found = load(code)
        wrong = problems(found)
        for line in wrong[:30]:
            print("  " + line)
        bad += len(missing) + len(unused) + len(wrong)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
