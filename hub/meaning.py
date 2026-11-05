"""Search by meaning without vectors (D52, step 18.3): each definition with the words around it, in a full-text index.

An idea («where are rights checked?») is seldom a name in the code, but often a word near one: in a docstring, in the
comment above a function, in its decorators, in a part of its name (`requireRole` is «require» and «role»). The
symbol index (hub/symbols.py) gives each definition's place; here each one gets a row in SQLite's FTS5 - its name
and its container split into words, what is said around it (`notes`), its file's path as words - with English stems
(the porter tokenizer: «permissions», «permitted» and «permission» meet). find_symbol with several words asks it, and
BM25 ranks: a word in the name counts most, in what is said next, in the path least; then what does something
(functions, classes) comes before values (constants), and tests last - measured on AFO's own code (tests/stress). A
file's own first words (its module docstring, the comment at its top) are a row of their own (`about`). No GPU, no
embeddings (D26); the rows live in memory with the index and follow the files as it does.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .symbols import Symbol

NOTE_CHARS = 300  # what is said around a definition, at most
ABOUT_CHARS = 600  # what a file says of itself, at most: one row a file, and most of AFO's explaining is there
ABOVE_LINES = 12  # comment lines above a definition, at most
DOC_LINES = 12  # docstring lines, at most
WEIGHTS = (10.0, 4.0, 1.0)  # BM25 per column: name, notes, place
SMALL_WORDS = frozenset(("a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "how", "in",
                         "is", "it", "its", "of", "on", "or", "our", "the", "this", "to", "we", "what", "when",
                         "where", "which", "who", "why", "with",
                         # words about the search itself, not what it looks for: «find programs installed» is about
                         # programs - with «find» in it, AFO's fifty `find` functions came first (the measurement)
                         "find", "locate", "code", "function", "functions", "method", "methods", "class", "classes",
                         "defined", "definition", "implemented", "implementation", "logic"))  # fmt: skip
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]*$|_test\.[^/]+$|\.(test|spec)\.[^/]+$", re.I)
VALUE_KINDS = frozenset(("constant", "variable", "field", "property"))  # a value names a thing, seldom does one
PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
WORD = re.compile(r"[^\W_]+")
DOCSTRING = re.compile(r'^[rRbBuU]?("""|\'\'\')')
# a comment line, by language: Python's # (in C it is the preprocessor), the C family's // /* * */
COMMENT = {"python": re.compile(r"^#")}
C_COMMENT = re.compile(r"^(//|/\*|\*)")
# what stands above a definition and says something of it: decorators, annotations, attributes
MARKS = {"python": "@", "javascript": "@", "typescript": "@", "tsx": "@", "java": "@", "csharp": "[", "rust": "#["}
DOCUMENTED = frozenset(("function", "method", "class"))  # what a Python docstring follows
FILE = -1  # a row's place for its file's own words (`about`), not a definition
MARKERS = re.compile(r"^(#+|//+!?|/\*+!?|\*+/?)\s?|\s*\*+/$")


def words(name: str) -> str:
    """An identifier, a dotted name or a path as words in lower case: `requirePermission` - «require permission»."""
    return " ".join(p.lower() for chunk in WORD.findall(name) for p in PART.findall(chunk))


def _comment(text: str, language: str) -> bool:
    return bool(COMMENT.get(language, C_COMMENT).match(text))


def _docstring(lines: list[str], line: int) -> list[str]:
    """A Python definition's docstring: the first statement after its header (which may run over several lines)."""
    at = line - 1
    while at < len(lines) and at < line - 1 + DOC_LINES and not lines[at].split("#", 1)[0].rstrip().endswith(":"):
        at += 1
    at += 1
    while at < len(lines) and not lines[at].strip():
        at += 1
    if at >= len(lines) or not (opened := DOCSTRING.match(lines[at].strip())):
        return []
    quote = opened.group(1)
    first = lines[at].strip()[opened.end() :]
    if quote in first:
        return [first.split(quote, 1)[0]]
    said = [first]
    for text in lines[at + 1 : at + DOC_LINES]:
        if quote in text:
            said.append(text.split(quote, 1)[0])
            break
        said.append(text)
    return said


def notes(source: bytes, symbols: list[Symbol], language: str) -> list[str]:
    """What is said around each definition: the comments and decorators right above it, a Python docstring - as one
    line of words, within NOTE_CHARS; "" when nothing is."""
    lines = source.decode("utf-8", "replace").splitlines()
    mark = MARKS.get(language)
    out = []
    for symbol in symbols:
        above: list[str] = []
        at = symbol.line - 2
        while at >= 0 and len(above) < ABOVE_LINES:
            text = lines[at].strip()
            if not text or not (_comment(text, language) or (mark and text.startswith(mark))):
                break
            above.append(text)
            at -= 1
        said = [MARKERS.sub("", t) for t in reversed(above)]
        if language == "python" and symbol.kind in DOCUMENTED:  # a constant has none: the next def's is not its
            said += _docstring(lines, symbol.line)
        out.append(_line(said))
    return out


def _line(said: list[str], limit: int = NOTE_CHARS) -> str:
    text = " ".join(" ".join(t.split()) for t in said if t.strip())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _preamble(line: str) -> bool:
    """A line before what a file says of itself: blank, a shebang, a coding line."""
    return not line.strip() or line.startswith("#!") or (line.startswith("#") and "coding" in line[:30])


def about(source: bytes, language: str) -> str:
    """What a file says of itself in its first words: a Python module's docstring, or the comment at its top (after
    a shebang or a coding line) - AFO's own files say most of what they are for there. "" when it says nothing."""
    lines = source.decode("utf-8", "replace").splitlines()
    at = 0
    while at < len(lines) and _preamble(lines[at]):
        at += 1
    if at >= len(lines):
        return ""
    if language == "python" and (opened := DOCSTRING.match(lines[at].strip())):
        quote, first = opened.group(1), lines[at].strip()[opened.end() :]
        if quote in first:
            return _line([first.split(quote, 1)[0]], ABOUT_CHARS)
        said = [first]
        for text in lines[at + 1 : at + 1 + ABOVE_LINES * 4]:
            if quote in text:
                said.append(text.split(quote, 1)[0])
                break
            said.append(text)
        return _line(said, ABOUT_CHARS)
    said = []
    for text in lines[at : at + ABOVE_LINES * 4]:
        if not _comment(text.strip(), language):
            break
        said.append(MARKERS.sub("", text.strip()))
    return _line(said, ABOUT_CHARS)


def a_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def terms(query: str) -> list[str]:
    """The words of a query worth looking for: its identifiers split, small words left out, each once."""
    return list(dict.fromkeys(w for w in words(query).split() if w not in SMALL_WORDS))


class MeaningIndex:
    """The rows of every project's definitions, in an in-memory SQLite database of the hub's own."""

    def __init__(self):
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE VIRTUAL TABLE defs USING fts5(name, notes, place, project UNINDEXED, path UNINDEXED, "
            "n UNINDEXED, tokenize = 'porter unicode61 remove_diacritics 2')"
        )

    def put(self, project_id: str, path: str, symbols: list[Symbol], said: list[str], about: str = "") -> None:
        """A file's definitions (in place of what it had): `said` - notes() of each; `about` - what the file says of
        itself, a row of its own (FILE)."""
        place = words(path)
        rows = [(words(f"{s.container}.{s.name}" if s.container else s.name), note, place, project_id, path, n)
                for n, (s, note) in enumerate(zip(symbols, said, strict=True))]  # fmt: skip
        if about:
            rows.append((words(path.rsplit("/", 1)[-1].rsplit(".", 1)[0]), about, place, project_id, path, FILE))
        with self._lock, self._db:
            self._db.execute("DELETE FROM defs WHERE project = ? AND path = ?", (project_id, path))
            self._db.executemany("INSERT INTO defs VALUES (?, ?, ?, ?, ?, ?)", rows)

    def drop(self, project_id: str, path: str | None = None) -> None:
        """A file's rows - or, with no path, the whole project's."""
        with self._lock, self._db:
            if path is None:
                self._db.execute("DELETE FROM defs WHERE project = ?", (project_id,))
            else:
                self._db.execute("DELETE FROM defs WHERE project = ? AND path = ?", (project_id, path))

    def count(self, project_id: str) -> int:
        with self._lock:
            return self._db.execute("SELECT count(*) FROM defs WHERE project = ?", (project_id,)).fetchone()[0]

    def search(self, project_id: str, query: str, limit: int) -> tuple[list[tuple[str, int, str]], int]:
        """([(path, the definition's place in its file, why: the notes with the words found in [ ])] best first,
        how many match in all)."""
        wanted = terms(query)
        if not wanted:
            return [], 0
        match = " OR ".join(f'"{w}"' for w in wanted)
        with self._lock:
            rows = self._db.execute(
                "SELECT path, n, snippet(defs, 1, '[', ']', '…', 16) FROM defs WHERE defs MATCH ? AND project = ? "
                "ORDER BY bm25(defs, ?, ?, ?) LIMIT ?",
                (match, project_id, *WEIGHTS, limit),
            ).fetchall()
            total = self._db.execute("SELECT count(*) FROM defs WHERE defs MATCH ? AND project = ?",
                                     (match, project_id)).fetchone()[0]  # fmt: skip
        return [(path, int(n), why) for path, n, why in rows], total
