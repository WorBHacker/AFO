"""Colours in the window's editor (D47, step 14.2b): Pygments - the viewer's lexer for the file, the theme's code
style - lexes the whole text off the window's thread, and the window's thread only lays the colours on: the lines
the screen reaches (on it and AROUND it), in slices of at most SLICE_MS, so a 1 MB file keeps the window answering;
the rest when the screen gets there (`_lay` says why not earlier). A line whose colours did not change is not
touched again (its colours' mark is the block's userState). While the whole text is being lexed, the screen's reach
is coloured at once on its own (`_at_once`).

Why the whole text, not from the changed line on: many of Pygments' lexers match a comment or a string over
several lines in one expression - lexed a line at a time, they colour it wrong. A line being typed in is coloured
at once on its own (as a line from the top state: right for nearly every line of code), and the whole text again
once typing pauses (RELEX_MS); its result is laid on only if the text has not changed meanwhile.

The colours go where QSyntaxHighlighter puts them - the block's layout formats, then markContentsDirty - with our
own order and pace instead of its pass over the whole document at once.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from pygments.lexers import get_lexer_by_name
from pygments.styles import get_all_styles, get_style_by_name
from pygments.util import ClassNotFound
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextDocument, QTextLayout

from .. import palette
from . import project_rows

if TYPE_CHECKING:
    from .controller import Controller

SLICE_MS = 8.0  # the window's thread lays colours on for no longer than this at a time
RELEX_MS = 150  # after the last key, the whole text is lexed again
AROUND = 200  # lines above and below the screen that get their colours with it
WIDE = re.compile("[\U00010000-\U0010ffff]")  # a character Qt counts twice (UTF-16)

Ranges = tuple[tuple[int, int, Any], ...]  # (start, length, token type) in a line, in UTF-16 units


def lexer_for(path: str):
    """The viewer's lexer for the file, with the text taken as it is; None for a file it does not colour."""
    lang = project_rows.language_of(path)
    try:
        return get_lexer_by_name(lang, stripnl=False, stripall=False, ensurenl=False) if lang else None
    except ClassNotFound:
        return None


def mark(ranges: Ranges) -> int:
    """A line's colours as a block's userState: a non-negative number (-1 - not coloured yet)."""
    return hash(ranges) & 0x3FFFFFFF


def lex_lines(lexer, text: str) -> tuple[list[Ranges], list[int]]:
    """The whole text lexed once: each line's coloured ranges, and their marks (off the window's thread both)."""
    rows = text.split("\n")
    lines: list[list[tuple[int, int, Any]]] = [[] for _ in rows]
    line = start = 0
    for index, token, value in lexer.get_tokens_unprocessed(text):
        first, *rest = value.split("\n")
        if first and line < len(lines):
            lines[line].append((index - start, len(first), token))
        index += len(first)
        for piece in rest:
            index += 1
            line, start = line + 1, index
            if piece and line < len(lines):
                lines[line].append((0, len(piece), token))
            index += len(piece)
    out = []
    for row, ranges in zip(rows, lines, strict=True):
        if WIDE.search(row):  # Qt counts in UTF-16: an emoji before a range moves it by one more
            ranges = [(_units(row, s), _units(row, s + n) - _units(row, s), t) for s, n, t in ranges]
        out.append(tuple(ranges))
    return out, [mark(r) for r in out]


def _units(line: str, index: int) -> int:
    return index + len(WIDE.findall(line, 0, index))


class Highlighter(QObject):
    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._document: QTextDocument | None = None
        self._lexer = None
        self._formats: dict[Any, QTextCharFormat | None] = {}
        self._theme = ""
        self._lines: list[Ranges] = []  # the last lexing's colours, line by line
        self._marks: list[int] = []
        self._pending: list[int] = []  # lines of the screen's reach still to lay on, the screen's own first
        self._screen = (0, 60)  # the first line on screen, how many
        self._revision = -1  # the document's revision the lexing's colours belong to
        self._generation = 0
        self._laying = False
        self._lay_timer = QTimer(self, singleShot=True, interval=0)
        self._lay_timer.timeout.connect(self._lay)
        self._lex_timer = QTimer(self, singleShot=True, interval=RELEX_MS)
        self._lex_timer.timeout.connect(self.relex)
        self.slices: list[float] = []  # each slice's milliseconds (the stress stand reads them)

    def attach(self, document: QTextDocument) -> None:
        """The editor's document. Qt sends contentsChange only once a document has its layout - a TextEdit's has."""
        if self._document is not document:
            self._document = document
            document.contentsChange.connect(self._edited)
            document.destroyed.connect(self._gone)

    def _gone(self) -> None:
        self.stop()
        self._document = None

    def start(self, path: str) -> None:
        """A file opened in the editor: its lexer, and its colours anew."""
        self._lexer = lexer_for(path)
        self.again()

    def stop(self) -> None:
        self._lexer = None
        self._generation += 1
        self._pending, self._lines, self._marks = [], [], []
        self._lex_timer.stop()
        self._lay_timer.stop()

    def again(self) -> None:
        """Every line coloured anew (a file opened, the theme changed)."""
        if self._document is None:
            return
        block = self._document.firstBlock()
        while block.isValid():
            block.setUserState(-1)
            block = block.next()
        self._formats, self._theme, self._lines, self._marks = {}, "", [], []
        self._at_once()
        self.relex()

    def _reach(self) -> tuple[int, int]:
        """The lines that get their colours: those on screen and AROUND them."""
        first, count = self._screen
        return max(0, first - AROUND), first + count + AROUND

    def _at_once(self) -> None:
        """The screen's reach coloured now, lexed on its own from the top state - exact at the top of a file; in the
        middle of a long string the whole text's lexing corrects it in a moment."""
        if self._lexer is None or self._document is None:
            return
        low, high = self._reach()
        block = self._document.findBlockByNumber(low)
        shown = []
        while block.isValid() and block.blockNumber() <= high:
            shown.append(block)
            block = block.next()
        if shown:
            lines, marks = lex_lines(self._lexer, "\n".join(b.text() for b in shown))
            for one, ranges, marked in zip(shown, lines, marks, strict=False):
                self._set(one, ranges, marked)
            self._mark(shown[0].position(), shown[-1].position() + shown[-1].length())

    def screen(self, first: int, count: int) -> None:
        """The editor scrolled: the lines it now reaches get their colours (those on screen first)."""
        self._screen = (max(0, first), max(1, count))
        self._queue()

    # ---------------------------------------------------------------- lexing

    def relex(self) -> None:
        if self._lexer is None or self._document is None:
            return
        self._lex_timer.stop()
        self._generation += 1
        generation, revision, lexer = self._generation, self._document.revision(), self._lexer
        text = self._document.toRawText().replace("\u2029", "\n").replace("\u2028", "\n")

        def done(found: tuple[list[Ranges], list[int]]) -> None:
            if generation != self._generation or self._document is None:
                return
            if self._document.revision() != revision:  # typed meanwhile: once more, after the pause
                self._lex_timer.start()
                return
            self._lines, self._marks = found
            self._revision = revision
            self._queue()

        self.app._call(lambda: lex_lines(lexer, text), done, lambda _e: None)

    def _edited(self, position: int, _removed: int, added: int) -> None:
        """Typing: the touched lines coloured at once on their own, the whole text after the pause. Inside
        contentsChange the marking only joins the edit's own layout - it costs nothing more."""
        if self._laying or self._lexer is None or self._document is None:
            return
        self._pending, self._lines, self._marks = [], [], []  # the lines may have moved: the next lexing says
        first, last = self._document.findBlock(position), self._document.findBlock(position + added)
        if last.blockNumber() - first.blockNumber() <= 3:
            block = first
            while block.isValid():
                text = block.text()
                ranges = lex_lines(self._lexer, text)[0][0] if text else ()
                self._set(block, ranges, mark(ranges))
                self._mark(block.position(), block.position() + block.length())
                if block == last:
                    break
                block = block.next()
        self._lex_timer.start()

    # ---------------------------------------------------------------- laying the colours on

    def _queue(self) -> None:
        """The screen's reach, as far as the last lexing knows it, to be laid on: its own lines first."""
        if not self._lines or self._document is None or self._document.revision() != self._revision:
            return
        first, count = self._screen
        low, high = self._reach()
        high = min(high, len(self._lines) - 1)
        shown = range(first, min(first + count, high) + 1)
        self._pending = [*shown, *(n for n in range(low, high + 1) if n not in shown)]
        self._lay_timer.start()

    def _lay(self) -> None:
        """One slice of at most SLICE_MS: the lines' colours set on their layouts, then the stretch they lie in
        marked for layout - once. Setting the colours drops a line's layout (QTextEngine::setFormats), and a marking
        lays the text out again from its first line to the end: the dropped lines anew (~25 us each), the rest only
        moved (~5 ms in the middle of 30 000 lines). So colours follow the screen: lines laid on far ahead would cost
        their whole layout again at the first marking before them (0.4 s at the middle of 1 MB). A line whose colours
        did not change is passed over (its mark is the block's userState)."""
        if self._document is None or self._document.revision() != self._revision:
            self._pending = []
            return
        began = time.perf_counter()
        done = 0
        stretch: list[int] = []
        for number in self._pending:
            done += 1
            block = self._document.findBlockByNumber(number)
            if block.isValid() and block.userState() != self._marks[number]:
                self._set(block, self._lines[number], self._marks[number])
                start, end = block.position(), block.position() + block.length()
                stretch = [min(stretch[0], start), max(stretch[1], end)] if stretch else [start, end]
            if (time.perf_counter() - began) * 1000 >= SLICE_MS * 0.5:  # the other half: the marking
                break
        del self._pending[:done]
        if stretch:
            self._mark(*stretch)
        self.slices.append((time.perf_counter() - began) * 1000)
        if self._pending:
            self._lay_timer.start()

    def _set(self, block, ranges: Ranges, marked: int) -> None:
        formats = []
        for start, length, token in ranges:
            look = self._format(token)
            if look is not None:
                one = QTextLayout.FormatRange()
                one.start, one.length, one.format = start, length, look
                formats.append(one)
        block.layout().setFormats(formats)
        block.setUserState(marked)

    def _mark(self, start: int, end: int) -> None:
        self._laying = True  # markContentsDirty is no edit of the user's
        try:
            self._document.markContentsDirty(start, end - start)
        finally:
            self._laying = False

    def _format(self, token) -> QTextCharFormat | None:
        theme = palette.current().name
        if theme != self._theme:
            self._formats, self._theme = {}, theme
        if token not in self._formats:
            found = _style(theme).style_for_token(token)
            look = None
            if found.get("color") or found.get("bold") or found.get("italic"):
                look = QTextCharFormat()
                if found.get("color"):
                    look.setForeground(QColor("#" + found["color"]))
                if found.get("bold"):
                    look.setFontWeight(QFont.Weight.Bold)
                if found.get("italic"):
                    look.setFontItalic(True)
            self._formats[token] = look
        return self._formats[token]


_STYLES: dict[str, Any] = {}


def _style(theme: str):
    """The theme's Pygments style - the viewer's (render._formatter): the first of the palette's that is installed."""
    if theme not in _STYLES:
        installed = set(get_all_styles())
        _STYLES[theme] = get_style_by_name(next((s for s in palette.current().code_styles if s in installed),
                                                "default"))  # fmt: skip
    return _STYLES[theme]
