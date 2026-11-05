"""The symbol index (stage 3, D26): where things are defined in a project's code, found by tree-sitter.

Each language's grammar comes with a tags query (the one code navigation on GitHub uses): it marks definitions -
functions, classes, methods, interfaces, types, modules - and their names. The hub parses the files the project
shows (what git shows, without caches; no secret files; up to 1 MB each) and keeps what it found in memory, per
file, with the file's size and modification time: a later query re-reads only files that changed, so only the first
one in a big project is slow - and it stops after a time budget, saying the index is not complete yet. All on the
processor, no embeddings (D26).

A symbol: name, kind, its lines, its container ("ChatService" for the class's methods) and its first line as the
signature. Each one also gets a row in the meaning index (hub/meaning.py, 18.3, D52): its name as words and what is
said around it - so several words find a definition by what it does.
"""

from __future__ import annotations

import bisect
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from importlib import import_module, resources
from pathlib import Path
from typing import Any, NamedTuple

from . import search
from .meaning import FILE, VALUE_KINDS, MeaningIndex, a_test, about, notes
from .projects import Project, linked_secret, private, private_part
from .secret_filter import is_secret_file

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 20_000
REFRESH_BUDGET_S = 20.0
FRESH_S = 2.0  # a query this soon after the last look at the files trusts it
SIGNATURE_CHARS = 160
FIND_LIMIT = 50

# language: the grammar's module and its function; TypeScript's tags build on JavaScript's
GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "java": ("tree_sitter_java", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
}
QUERIES = {"typescript": ("tree_sitter_javascript", "tree_sitter_typescript"),
           "tsx": ("tree_sitter_javascript", "tree_sitter_typescript")}  # fmt: skip
SUFFIXES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp", ".c": "c", ".h": "cpp", ".cc": "cpp",
    ".cpp": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp",
}  # fmt: skip
# what the tags queries leave out: Rust's impl blocks hold the methods of a type, TypeScript's namespaces
EXTRA_PATTERNS = {
    "rust": "(impl_item type: (_) @name) @definition.impl",
    "typescript": "(internal_module name: (_) @name) @definition.module",
    "tsx": "(internal_module name: (_) @name) @definition.module",
}
CONTAINER_KINDS = {"class", "interface", "module", "type", "impl"}


@dataclass
class _Grammar:
    language: Any
    query: Any


class Symbol(NamedTuple):
    """One definition (a tuple: a big project has hundreds of thousands of them)."""

    name: str
    kind: str
    line: int
    end_line: int
    container: str
    signature: str


@dataclass
class _File:
    size: int
    mtime_ns: int
    symbols: list[Symbol]


@dataclass
class _Project:
    files: dict[str, _File] = field(default_factory=dict)
    checked: float = 0.0  # when the files were last compared with the disk (monotonic)
    complete: bool = False  # every file the index reads is read (the time budget did not run out)
    left_out: int = 0  # the project's files past MAX_FILES: never read, so the index is never complete (D46)
    lock: threading.Lock = field(default_factory=threading.Lock)


_grammars: dict[str, _Grammar | None] = {}
_grammars_lock = threading.Lock()


def _tags(module: str) -> str:
    base = Path(str(resources.files(module)))
    # tree-sitter-c-sharp installs its queries beside the package, under the distribution's name
    for folder in (base, base.parent / base.name.replace("_c_sharp", "_c-sharp")):
        path = folder / "queries" / "tags.scm"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no tags query in {module}")


def grammar(language: str) -> _Grammar | None:
    """The parser's language and tags query, loaded once; None when the grammar is not installed."""
    with _grammars_lock:
        if language not in _grammars:
            try:
                from tree_sitter import Language, Query

                module, function = GRAMMARS[language]
                lang = Language(getattr(import_module(module), function)())
                queries = [_tags(m) for m in QUERIES.get(language, (module,))]
                text = "\n".join([*queries, EXTRA_PATTERNS.get(language, "")])
                _grammars[language] = _Grammar(lang, Query(lang, text))
            except ImportError, OSError, ValueError, AttributeError:
                _grammars[language] = None
        return _grammars[language]


def language_of(path: str) -> str | None:
    return SUFFIXES.get(os.path.splitext(path)[1].lower())


def _line_starts(source: bytes) -> list[int]:
    starts = [0]
    at = source.find(b"\n")
    while at != -1:
        starts.append(at + 1)
        at = source.find(b"\n", at + 1)
    return starts


def parse(source: bytes, language: str) -> list[Symbol]:
    """The definitions in one file's source, in order.

    Only byte offsets are read from the tree: Node.start_point and Node.text crash the process now and then under
    Python 3.14 (tree-sitter 0.26.0), so lines are counted here and containers found by nesting of byte ranges."""
    found = grammar(language)
    if found is None:
        return []
    from tree_sitter import Parser, QueryCursor

    tree = Parser(found.language).parse(source)
    definitions: dict[tuple[int, int], tuple[str, str]] = {}  # (start, end byte) -> kind, name
    for _pattern, captures in QueryCursor(found.query).matches(tree.root_node):
        names = captures.get("name")
        if not names:
            continue
        name = source[names[0].start_byte : names[0].end_byte].decode("utf-8", "replace")
        for capture, nodes in captures.items():
            if capture.startswith("definition."):
                definitions.setdefault((nodes[0].start_byte, nodes[0].end_byte), (capture[11:], name))
    starts = _line_starts(source)
    symbols = []
    seen = set()
    open_: list[tuple[int, str, str]] = []  # enclosing definitions, outermost first: (end byte, name, kind)
    for (start, end), (kind, name) in sorted(definitions.items(), key=lambda item: (item[0][0], -item[0][1])):
        while open_ and open_[-1][0] < end:
            open_.pop()  # it ended before this one does: not a container
        line = bisect.bisect_right(starts, start)
        if (name, line) in seen:
            continue  # two patterns marked the same thing (an exported function and the function)
        seen.add((name, line))
        nearest = open_[-1][2] if open_ else ""
        container = sys.intern(".".join(n for _end, n, _kind in open_))
        open_.append((end, name, kind))
        if kind == "function" and nearest in CONTAINER_KINDS:
            kind = "method"  # a function nested in a function stays a function
        line_end = source.find(b"\n", start)
        first = source[start : line_end if line_end != -1 else len(source)].decode("utf-8", "replace").strip()
        end_line = bisect.bisect_right(starts, max(start, end - 1))
        symbols.append(Symbol(name, sys.intern(kind), line, end_line, container, first[:SIGNATURE_CHARS]))
    return symbols


class SymbolIndex:
    """What is defined where, per project, kept in memory and brought up to date on each query."""

    def __init__(self, budget_s: float = REFRESH_BUDGET_S, fresh_s: float = FRESH_S):
        self._projects: dict[str, _Project] = {}
        self._guard = threading.Lock()
        self._budget_s = budget_s
        self._fresh_s = fresh_s
        self.meaning = MeaningIndex()  # what each definition says of itself, for several words (18.3)

    def _state(self, project_id: str) -> _Project:
        with self._guard:
            return self._projects.setdefault(project_id, _Project())

    def forget(self, project_id: str) -> None:
        with self._guard:
            self._projects.pop(project_id, None)
        self.meaning.drop(project_id)

    def refresh(self, project: Project) -> _Project:
        """Compares the files with the disk and parses the new and changed ones (within the time budget)."""
        state = self._state(project.id)
        with state.lock:
            if state.complete and time.monotonic() - state.checked < self._fresh_s:
                return state
            root = project.root
            deadline = time.monotonic() + self._budget_s
            present: set[str] = set()
            complete = True
            own = private_part(root)  # the hub's data folder, when this project holds it
            files = search.project_files(root)
            state.left_out = max(0, len(files) - MAX_FILES)
            for rel in files[:MAX_FILES]:
                language = language_of(rel)
                if language is None or is_secret_file(rel) or (own and rel.startswith(own)):
                    continue
                try:
                    st = (root / rel).stat()
                except OSError:
                    continue
                if st.st_size > MAX_FILE_BYTES or (st.st_nlink > 1 and linked_secret(root / rel, st.st_nlink)):
                    continue
                present.add(rel)
                known = state.files.get(rel)
                if known and known.size == st.st_size and known.mtime_ns == st.st_mtime_ns:
                    continue
                if time.monotonic() > deadline:
                    complete = False  # the rest on the next query
                    continue
                try:
                    source = (root / rel).read_bytes()
                except OSError:
                    continue
                found = parse(source, language)
                state.files[rel] = _File(st.st_size, st.st_mtime_ns, found)
                self.meaning.put(project.id, rel, found, notes(source, found, language), about(source, language))
            for rel in set(state.files) - present:
                del state.files[rel]
                self.meaning.drop(project.id, rel)
            state.checked = time.monotonic()
            state.complete = complete
            return state

    def find(self, project: Project, query: str, kind: str = "", limit: int = FIND_LIMIT) -> dict[str, Any]:
        """Definitions whose name matches: exact first, then by prefix, then containing it. `Class.method` matches
        a method of that class. {symbols: [... with path], total, complete, left_out}: `left_out` files of a project
        bigger than MAX_FILES are never read. Several words (18.3): definitions by what they say of themselves -
        their names as words, comments, docstrings (hub/meaning.py), best first, each with `why`; `by`: "meaning"."""
        if len(query.split()) > 1:
            return self._by_meaning(project, query, kind, limit)
        state = self.refresh(project)
        query = query.strip()
        container_part, _, name_part = query.rpartition(".")
        wanted = name_part.casefold()
        ranked = []
        for path, file in state.files.items():
            for symbol in file.symbols:
                if kind and symbol.kind != kind:
                    continue
                if container_part and not symbol.container.casefold().endswith(container_part.casefold()):
                    continue
                name = symbol.name
                folded = name.casefold()
                if name == name_part:
                    rank = 0
                elif folded == wanted:
                    rank = 1
                elif folded.startswith(wanted):
                    rank = 2
                elif wanted in folded:
                    rank = 3
                else:
                    continue
                ranked.append((rank, len(name), path, symbol.line, symbol))
        ranked.sort(key=lambda item: item[:4])
        found = [{**symbol._asdict(), "path": path} for _r, _n, path, _l, symbol in ranked[:limit]]
        return {"symbols": found, "total": len(ranked), "complete": state.complete and not state.left_out,
                "left_out": state.left_out}  # fmt: skip

    def _by_meaning(self, project: Project, query: str, kind: str, limit: int) -> dict[str, Any]:
        state = self.refresh(project)
        found = []
        rows, total = self.meaning.search(project.id, query, FIND_LIMIT * 4)
        for path, n, why in rows:
            file = state.files.get(path)
            if n == FILE and file is not None and not kind:  # the file's own words
                name = path.rsplit("/", 1)[-1]
                found.append({"name": name, "kind": "file", "line": 1, "end_line": 1, "container": "",
                              "signature": "", "path": path, "why": why})  # fmt: skip
                continue
            if file is None or not 0 <= n < len(file.symbols) or (kind and file.symbols[n].kind != kind):
                continue
            found.append({**file.symbols[n]._asdict(), "path": path, "why": why})
        # by the words first; then what does something before values, tests last (stable: words order each group)
        found.sort(key=lambda s: (a_test(s["path"]), s["kind"] in VALUE_KINDS))
        return {
            "symbols": found[:limit],
            "total": total if not kind else len(found),
            "complete": state.complete and not state.left_out,
            "left_out": state.left_out,
            "by": "meaning",
        }

    def definitions(self, project: Project) -> dict[str, list[Symbol]]:
        """Every file the index reads, with its definitions (18.2: what a card's mechanisms are written from)."""
        state = self.refresh(project)
        return {path: list(file.symbols) for path, file in list(state.files.items())}

    def outline(self, project: Project, rel: str) -> list[dict[str, Any]] | None:
        """The definitions of one file in order; None when it is not code the index reads."""
        rel = rel.replace("\\", "/").strip("/")
        if language_of(rel) is None or is_secret_file(rel):
            return None
        state = self.refresh(project)
        file = state.files.get(rel)
        if file is None:
            # not among the project's files (ignored by git, say): read it directly, without keeping it
            path = project.root / rel
            try:
                real = path.resolve()
                inside = real.is_relative_to(project.root.resolve()) and not private(project.root, real)
                if not inside or path.stat().st_size > MAX_FILE_BYTES:
                    return None
                return [s._asdict() for s in parse(path.read_bytes(), language_of(rel) or "")]
            except OSError:
                return None
        return [s._asdict() for s in file.symbols]
