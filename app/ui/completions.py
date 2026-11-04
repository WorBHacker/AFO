"""Completions in the editor, and its way to a definition - without a model (D47, step 14.5).

The list under the cursor comes after two letters of a name, or at Ctrl+Space. It offers the language's keywords
and built-in names (Python's from `keyword` and `builtins`; the other languages the symbol index reads: the lists
below), the words of the open tabs, the definitions in the tab's own text, and the project's definitions from the
hub's symbol index (asked by their first two letters, kept until the project's files change). Those that begin with
what was typed come first, then those that hold its letters in order. Tab or Enter puts the chosen one in - one
undo step; Esc closes it.

F12 and Ctrl+click: the definition of the name under the cursor - in the tab's own text first (unsaved too: the
window parses the open text itself, with the hub's tree-sitter parse, hub/symbols.py), else in the hub's index; a
list when there are several.

The open texts are read off the window's thread: a tab's words and definitions after typing pauses (WORDS_MS), a
1 MB file in ~0.1 s.
"""

from __future__ import annotations

import builtins
import keyword
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor

from common.i18n import tr
from hub.symbols import language_of, parse

if TYPE_CHECKING:
    from .controller import Controller
    from .editor import Buffer, EditorView

SHOWN = 40  # names in the list at most
MIN_WORD = 3  # shorter words of the open files are not offered
WORDS_MS = 500  # after typing pauses, the tab's words and definitions are read again
SYMBOLS_LIMIT = 500  # the project's definitions asked for by two letters
NAME = re.compile(r"[^\W\d]\w*")
_JS = ("break case catch class const continue debugger default delete do else export extends false finally for "
       "function if import in instanceof let new null return super switch this throw true try typeof undefined var "
       "void while with yield async await of static get set console Object Array String Number Boolean Promise Map "
       "Set JSON Math Date RegExp Error Symbol parseInt parseFloat setTimeout setInterval clearTimeout require "
       "module exports document window")  # fmt: skip
_TS = _JS + (" interface type enum implements namespace declare readonly private protected public abstract as keyof "
             "never unknown any string number boolean")  # fmt: skip
_C = ("auto break case char const continue default do double else enum extern float for goto if inline int long "
      "register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while "
      "NULL printf malloc free size_t include define")  # fmt: skip
_CPP = _C + (" bool catch class constexpr delete false friend namespace new nullptr operator private protected public "
             "template this throw true try typename using virtual std string vector cout endl")  # fmt: skip
_GO = ("break case chan const continue default defer else fallthrough for func go goto if import interface map "
       "package range return select struct switch type var append cap close complex copy delete imag len make new "
       "panic print println real recover bool byte error float32 float64 int int8 int16 int32 int64 rune string "
       "uint uint8 uint16 uint32 uint64 uintptr true false nil iota")  # fmt: skip
_RUST = ("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod "
         "move mut pub ref return self Self static struct super trait true type unsafe use where while Option Some "
         "None Result Ok Err Vec String Box println format vec")  # fmt: skip
_JAVA = ("abstract assert boolean break byte case catch char class const continue default do double else enum extends "
         "final finally float for if implements import instanceof int interface long native new package private "
         "protected public return short static strictfp super switch synchronized this throw throws transient try "
         "void volatile while true false null String System Object Integer List Map ArrayList HashMap "
         "Override")  # fmt: skip
_CSHARP = ("abstract as base bool break byte case catch char checked class const continue decimal default delegate do "
           "double else enum event explicit extern false finally fixed float for foreach goto if implicit in int "
           "interface internal is lock long namespace new null object operator out override params private protected "
           "public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw "
           "true try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while async await "
           "Console List Dictionary Task")  # fmt: skip
KEYWORDS: dict[str, list[str]] = {
    "python": sorted(n for n in {*keyword.kwlist, *keyword.softkwlist, *dir(builtins)} if not n.startswith("_")),
    **{language: words.split() for language, words in (
        ("javascript", _JS), ("typescript", _TS), ("tsx", _TS), ("go", _GO), ("rust", _RUST), ("java", _JAVA),
        ("csharp", _CSHARP), ("c", _C), ("cpp", _CPP))},
}  # fmt: skip


def words(text: str) -> set[str]:
    return {w for w in NAME.findall(text) if len(w) >= MIN_WORD}


@dataclass
class Read:
    """What a tab's text held when last read: its words - by their lower case, and all of those in one string, a
    line each, for one expression to search per letter typed (a 1 MB file's 10 000 words, looked at one by one in
    Python, took 46 ms a letter) - and its definitions."""

    revision: int = -1
    lows: dict[str, list[str]] = field(default_factory=dict)
    joined: str = ""
    definitions: list[Any] = field(default_factory=list)

    @classmethod
    def of(cls, revision: int, text: str, language: str | None) -> Read:
        lows: dict[str, list[str]] = {}
        for word in words(text):
            lows.setdefault(word.casefold(), []).append(word)
        return cls(revision, lows, "\n".join(lows), parse(text.encode("utf-8"), language) if language else [])

    def having(self, letters: re.Pattern[str]) -> list[str]:
        """The words holding the typed letters in order."""
        return [word for found in letters.finditer(self.joined) for word in self.lows[found.group(0)]]


def in_order(typed: str) -> re.Pattern[str]:
    """A whole line holding these letters in order (lower case)."""
    return re.compile("^[^\n]*?" + "[^\n]*?".join(re.escape(c) for c in typed.casefold()) + "[^\n]*$", re.MULTILINE)


def name_before(line: str) -> str:
    """The name the text before the cursor ends with ('' - none)."""
    found = re.search(r"[^\W\d]\w*$", line)
    return found.group(0) if found else ""


def name_at(line: str, column: int) -> tuple[int, str]:
    """The name the cursor stands in or right after: its first column and itself ((column, '') - none)."""
    for found in NAME.finditer(line):
        if found.start() <= column <= found.end():
            return found.start(), found.group(0)
    return column, ""


def fuzzy(typed: str, name: str) -> bool:
    """The typed letters in the name in order (prt - print)."""
    at = 0
    for char in typed:
        at = name.find(char, at) + 1
        if not at:
            return False
    return True


def ranked(typed: str, names: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Those beginning with what was typed (the same case first, then any), then those holding its letters in order;
    shorter first within each. The typed name itself is left out: nothing to complete."""
    folded = typed.casefold()
    found = []
    for name, item in names.items():
        if name == typed:
            continue
        low = name.casefold()
        rank = 0 if name.startswith(typed) else 1 if low.startswith(folded) else 2 if fuzzy(folded, low) else -1
        if rank >= 0:
            found.append((rank, len(name), low, item))
    found.sort(key=lambda r: r[:3])
    return [item for *_order, item in found[:SHOWN]]


class Completer(QObject):
    changed = Signal()
    placesChanged = Signal()

    def __init__(self, app: Controller, editor: EditorView):
        super().__init__(app)
        self.app = app
        self.editor = editor
        self._key = ""
        self._items: list[dict[str, Any]] = []
        self._index = 0
        self._start = self._position = 0
        self._typed = ""
        self._places: list[dict[str, Any]] = []  # several definitions: the list to pick one from
        self._read: dict[str, Read] = {}  # tab key -> what its text held when last read
        self._reading: set[str] = set()
        self._symbols: dict[tuple[str, str], list[dict[str, Any]]] = {}  # (project, two letters) -> definitions
        self._asking: set[tuple[str, str]] = set()
        self._timer = QTimer(self, singleShot=True, interval=WORDS_MS)
        self._timer.timeout.connect(self.read_tabs)

    # ---------------------------------------------------------------- what the window shows

    @Property(bool, notify=changed)
    def open(self) -> bool:
        return bool(self._items)

    @Property(str, notify=changed)
    def key(self) -> str:
        return self._key

    @Property(list, notify=changed)
    def items(self) -> list:
        return self._items

    @Property(int, notify=changed)
    def index(self) -> int:
        return self._index

    @Property(int, notify=changed)
    def start(self) -> int:
        return self._start

    @Property(list, notify=placesChanged)
    def places(self) -> list:
        return self._places

    # ---------------------------------------------------------------- the list

    @Slot(str, int, bool)
    def update(self, key: str, position: int, asked: bool) -> None:
        """Typed (or Ctrl+Space - `asked`): the list for the name before the cursor - two letters at least, unless
        asked; closed when there is none, or nothing fits."""
        b = self.editor.buffer(key)
        if b is None or b.document is None:
            self.close()
            return
        block = b.document.findBlock(position)
        typed = name_before(block.text()[: position - block.position()])
        if not asked and len(typed) < 2:
            self.close()
            return
        self._key, self._typed, self._position = key, typed, position
        self._start = position - len(typed)
        self._items = ranked(typed, self._names(b, typed))
        self._index = 0
        self.changed.emit()

    def _names(self, b: Buffer, typed: str) -> dict[str, dict[str, Any]]:
        """What may be offered, by name: the project's definitions, then the open tabs' words, the tab's own
        definitions, the language's keywords - a later source tells a name's kind over an earlier."""
        names: dict[str, dict[str, Any]] = {}
        if len(typed) >= 2:
            for symbol in self._project_symbols(b.project_id, typed[:2]):
                names[symbol["name"]] = {"name": symbol["name"], "kind": symbol.get("kind", ""),
                                         "detail": symbol.get("path", "")}  # fmt: skip
        if typed:
            letters = in_order(typed)
            for other in self.editor.buffers():
                for word in self._read.get(other.key, Read()).having(letters):
                    names.setdefault(word, {"name": word, "kind": tr("слово"),
                                            "detail": other.path.rsplit("/", 1)[-1]})  # fmt: skip
        for symbol in self._read.get(b.key, Read()).definitions:
            names[symbol.name] = {"name": symbol.name, "kind": symbol.kind, "detail": tr("в этом файле")}
        for word in KEYWORDS.get(language_of(b.path) or "", []):
            names[word] = {"name": word, "kind": tr("язык"), "detail": ""}
        if b.key not in self._read:
            self.read_tabs()
        return names

    def _project_symbols(self, project_id: str, letters: str) -> list[dict[str, Any]]:
        """The hub's definitions with these two letters in their names: kept; asked once, the list grows when the
        answer comes."""
        key = (project_id, letters.casefold())
        if key in self._symbols:
            return self._symbols[key]
        if key not in self._asking and self.app.client is not None:
            self._asking.add(key)

            def done(found: dict[str, Any]) -> None:
                self._asking.discard(key)
                self._symbols[key] = list(found.get("symbols") or [])
                if self._items or self._key:
                    self._again()

            def failed(_error: Exception) -> None:
                self._asking.discard(key)

            self.app._call(lambda: self.app.client.symbols(project_id, q=letters, limit=SYMBOLS_LIMIT), done, failed)
        return []

    def _again(self) -> None:
        b = self.editor.buffer(self._key)
        if self._items and b is not None and b.document is not None:
            self.update(self._key, self._position, True)

    @Slot(int)
    def move(self, step: int) -> None:
        if self._items:
            self._index = (self._index + step) % len(self._items)
            self.changed.emit()

    @Slot(result=int)
    def accept(self) -> int:
        """Tab or Enter: the chosen name in place of what was typed of it, one undo step; where the cursor goes."""
        b = self.editor.buffer(self._key)
        if not self._items or b is None or b.document is None:
            self.close()
            return self._position
        name = self._items[self._index]["name"]
        cursor = QTextCursor(b.document)
        cursor.setPosition(self._start)
        cursor.setPosition(self._position, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(name)
        self.close()
        return cursor.position()

    @Slot()
    def close(self) -> None:
        if self._items or self._key:
            self._items, self._key = [], ""
            self.changed.emit()

    # ---------------------------------------------------------------- what the open tabs hold

    def edited(self) -> None:
        """A tab's text changed: its words and definitions are read again once typing pauses."""
        self._timer.start()

    def read_tabs(self) -> None:
        """Each tab whose text changed since: its words and its definitions, off the window's thread."""
        for b in self.editor.buffers():
            if b.document is None or b.key in self._reading:
                continue
            revision = b.document.revision()
            if self._read.get(b.key, Read()).revision == revision:
                continue
            self._reading.add(b.key)
            text, language = b.text(), language_of(b.path)

            def done(found: Read, key=b.key) -> None:
                self._reading.discard(key)
                if self.editor.buffer(key) is not None:
                    self._read[key] = found

            def failed(_error: Exception, key=b.key) -> None:
                self._reading.discard(key)

            self.app.bg.run(lambda revision=revision, text=text, language=language: Read.of(revision, text, language),
                            done, failed)  # fmt: skip

    def forget(self, key: str) -> None:
        self._read.pop(key, None)
        if self._key == key:
            self.close()

    def files_changed(self, project_id: str) -> None:
        """The project's files changed: its definitions are asked for again when next needed."""
        for kept in [k for k in self._symbols if k[0] == project_id]:
            del self._symbols[kept]

    # ---------------------------------------------------------------- to the definition

    @Slot(str, int)
    def define(self, key: str, position: int) -> None:
        """F12, Ctrl+click: to the definition of the name at `position` - the tab's own first, then the project's;
        several: `places` lists them."""
        b = self.editor.buffer(key)
        if b is None or b.document is None:
            return
        block = b.document.findBlock(position)
        _column, name = name_at(block.text(), position - block.position())
        if not name:
            return
        own = [{"path": b.path, "line": s.line, "kind": s.kind, "signature": s.signature}
               for s in self._read.get(key, Read()).definitions if s.name == name]  # fmt: skip
        if own:
            self._go_or_list(b, name, own)
            return

        def done(found: dict[str, Any]) -> None:
            places = [{"path": s["path"], "line": s["line"], "kind": s.get("kind", ""),
                       "signature": s.get("signature", "")}
                      for s in found.get("symbols") or [] if s.get("name") == name]  # fmt: skip
            if places:
                self._go_or_list(b, name, places)
            else:
                self.app.flash(tr("Определение «{name}» не найдено в индексе проекта", name=name))

        self.app._call(lambda: self.app.client.symbols(b.project_id, q=name, limit=50), done,
                       lambda e: self.app.show_error(tr("Определение не найдено: {error}", error=e)))  # fmt: skip

    def _go_or_list(self, b: Buffer, name: str, places: list[dict[str, Any]]) -> None:
        self._places = [{**p, "projectId": b.project_id, "name": name} for p in places]
        if len(places) == 1:
            self.goTo(0)
        else:
            self.placesChanged.emit()

    @Slot(int)
    def goTo(self, index: int) -> None:
        if not 0 <= index < len(self._places):
            return
        place = self._places[index]
        self._places = []
        self.placesChanged.emit()
        self.app.projects.openFileAt(place["path"], int(place["line"]))

    @Slot()
    def closePlaces(self) -> None:
        self._places = []
        self.placesChanged.emit()
