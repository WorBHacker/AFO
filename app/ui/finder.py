"""Find and replace in the editor's file (D47, step 14.3): Ctrl+F and Ctrl+H, case and regular expressions, «3 из 17»,
every match on screen marked, «Заменить все» as one undo step.

The whole text is searched on a pool thread (`total` 19 000 times in 1 MB: 44 ms) - after the query changes at once,
after typing in the text once it pauses; meanwhile the matches already found move with the edit, and those it
touched are dropped. An expression that backtracks without end holds a pool thread, not the window; «Все» waits for
the count. Positions are Qt's (UTF-16 units: an emoji counts two), as the TextEdit's cursor has them. The window
draws the matches itself, behind the text, only those on screen (CodeEditor.qml): the colours of the highlighter
(14.2b) stay as they are.

Replacing goes through a QTextCursor on the TextEdit's document: one match, or all of them in one edit block - Qt
sends one contentsChange for the block, and one Ctrl+Z undoes it (19 000 replacements in 1 MB: 0.45 s, undone in
0.26 s - the stand, tests/stress/test_editor_typing.py).
"""

from __future__ import annotations

import contextlib
import re
from bisect import bisect_left
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor, QTextDocument

from common.i18n import tr

from ..presenter import plural

if TYPE_CHECKING:
    from .controller import Controller

LIMIT = 10_000  # matches counted and marked; past this «10 000+» (Replace all replaces every one)
REFIND_MS = 300  # after the last key in the text, the whole text is searched again
ON_SCREEN = 2_000  # matches marked on screen at most
WIDE = re.compile("[\U00010000-\U0010ffff]")  # a character Qt counts twice (UTF-16)
DOLLAR = re.compile(r"\$(\$|&|\d+)")  # $1, $& - as VS Code writes groups in a replacement

Span = tuple[int, int]


def pattern(query: str, case: bool, regex: bool) -> re.Pattern[str]:
    """What is looked for: the text as typed, or a regular expression (Python's; ^ and $ at each line). Raises
    re.error for one that does not compile."""
    flags = re.MULTILINE | (0 if case else re.IGNORECASE)
    return re.compile(query if regex else re.escape(query), flags)


class Units:
    """Python's characters against Qt's UTF-16 units: one each, but two for a character past U+FFFF (an emoji)."""

    def __init__(self, text: str):
        self._wide = [m.start() for m in WIDE.finditer(text)]
        self._wide_units = [index + n for n, index in enumerate(self._wide)]

    def units(self, index: int) -> int:
        return index + bisect_left(self._wide, index)

    def index(self, unit: int) -> int:
        return unit - bisect_left(self._wide_units, unit)


def find_all(text: str, found: re.Pattern[str], limit: int = LIMIT) -> tuple[list[Span], bool]:
    """The matches in Qt's units, at most `limit`; True when there are more."""
    units, spans = Units(text), []
    for match in found.finditer(text):
        if len(spans) == limit:
            return spans, True
        spans.append((units.units(match.start()), units.units(match.end())))
    return spans, False


def replacement(match: re.Match[str], template: str, regex: bool) -> str:
    """What replaces a match: the text as typed, or with a regular expression its groups - $1 or \\1, $& or \\g<0> for
    the whole match, $$ for a dollar. Raises re.error for a template that does not fit the match."""
    if not regex:
        return template
    return match.expand(DOLLAR.sub(_group, template))


def _group(dollar: re.Match[str]) -> str:
    name = dollar.group(1)
    return "$" if name == "$" else rf"\g<{0 if name == '&' else name}>"


class Finder(QObject):
    changed = Signal()
    fresh = Signal()  # the matches of a new query (or case, regex) found: the window goes to the nearest one

    def __init__(self, app: Controller, text):
        super().__init__(app)
        self.app = app
        self._text = text  # the editor's: the document's text with "\n" between lines
        self._document: QTextDocument | None = None
        self._active = self._replacing = self._case = self._regex = False
        self._query = ""
        self._pattern: re.Pattern[str] | None = None
        self._error = ""
        self._spans: list[Span] = []
        self._more = False
        self._ready = False  # the spans are the whole text's, as it is now
        self._generation = 0
        self._revision = 0  # bumped on every change: what QML binds the count and the marks to
        self._timer = QTimer(self, singleShot=True, interval=REFIND_MS)
        self._timer.timeout.connect(self._find)

    def attach(self, document: QTextDocument) -> None:
        """The document searched: the shown tab's (14.4). Another tab shown with the bar open: searched at once."""
        if document is self._document:
            return
        self.detach()
        self._document = document
        document.contentsChange.connect(self._edited)
        document.destroyed.connect(self._gone)
        if self._active:
            self._search()

    def _gone(self) -> None:
        self._document = None
        self._timer.stop()

    def detach(self) -> None:
        """The tab searched closed, or another shown: its document let go, its signals too."""
        if self._document is not None:
            with contextlib.suppress(RuntimeError, TypeError):  # gone already
                self._document.contentsChange.disconnect(self._edited)
                self._document.destroyed.disconnect(self._gone)
        self._gone()

    # ---------------------------------------------------------------- what the window shows

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return self._active

    @Property(bool, notify=changed)
    def replacing(self) -> bool:
        return self._replacing

    @Property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @Property(bool, notify=changed)
    def caseSensitive(self) -> bool:
        return self._case

    @Property(bool, notify=changed)
    def regex(self) -> bool:
        return self._regex

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._spans)

    @Property(bool, notify=changed)
    def ready(self) -> bool:
        return self._ready

    @Property(int, notify=changed)
    def revision(self) -> int:
        return self._revision

    @Slot(int, int, int, result=str)
    def label(self, start: int, end: int, _revision: int = 0) -> str:
        """«3 из 17» when the selection is a match, «17 совпадений» when it is not, «Нет совпадений»."""
        if not self._query or self._error:
            return ""
        if not self._spans:
            return tr("Нет совпадений") if self._ready else ""
        total = f"{len(self._spans):,}".replace(",", " ") + ("+" if self._more else "")
        at = self._at(start, end)
        if at >= 0:
            return tr("{at} из {total}", at=at + 1, total=total)
        return f"{total} {plural(len(self._spans), 'совпадение', 'совпадения', 'совпадений')}"

    @Slot(int, int, int, int, int, result="QVariantList")
    def marks(self, first: int, count: int, start: int, end: int, _revision: int = 0) -> list:
        """The matches on the lines on screen, a line's piece each: [start, end, 1 for the selected match or 0]."""
        if not self._active or not self._spans or self._document is None:
            return []
        top = self._document.findBlockByNumber(max(0, first))
        bottom = self._document.findBlockByNumber(first + count)
        low = top.position()
        high = bottom.position() + bottom.length() if bottom.isValid() else self._document.characterCount()
        found = []
        at = max(0, bisect_left(self._spans, (low, low)) - 1)  # one before: it may reach onto the screen
        while at < len(self._spans) and self._spans[at][0] <= high and len(found) < ON_SCREEN:
            s, e = self._spans[at]
            chosen = int(s == start and e == end)
            block = self._document.findBlock(s)
            while block.isValid() and block.position() <= max(s, e - 1):  # a match over lines: a piece on each
                piece = (max(s, block.position()), min(e, block.position() + block.length() - 1))
                if piece[1] >= low and piece[0] <= high:
                    found.append([*piece, chosen])
                if e <= block.position() + block.length() - 1:
                    break
                block = block.next()
            at += 1
        return found

    # ---------------------------------------------------------------- the bar

    @Slot(bool, str)
    def open(self, replacing: bool, selected: str) -> None:
        """Ctrl+F (`replacing` off) or Ctrl+H: the bar; a selection on one line becomes the query."""
        self._active, self._replacing = True, replacing
        if selected and "\n" not in selected and "\u2029" not in selected:
            self._query = re.escape(selected) if self._regex else selected
        self._search()

    @Slot()
    def close(self) -> None:
        if self._active:
            self._active = False
            self._timer.stop()
            self._generation += 1
            self._spans, self._ready = [], False
            self._changed()

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self._search()

    @Slot(bool)
    def setCase(self, case: bool) -> None:
        if case != self._case:
            self._case = case
            self._search()

    @Slot(bool)
    def setRegex(self, regex: bool) -> None:
        if regex != self._regex:
            self._regex = regex
            self._search()

    @Slot(bool)
    def setReplacing(self, replacing: bool) -> None:
        self._replacing = replacing
        self._changed()

    def _search(self) -> None:
        """A new query, case or regex: compiled, then the whole text searched; the window goes to the nearest."""
        self._error, self._pattern = "", None
        if self._query:
            try:
                self._pattern = pattern(self._query, self._case, self._regex)
            except re.error as exc:
                self._error = tr("Ошибка в выражении: {error}", error=exc.msg)
        self._find(fresh=True)

    def _find(self, fresh: bool = False) -> None:
        self._timer.stop()
        self._generation += 1
        found, generation = self._pattern, self._generation
        if not self._active or found is None or self._document is None:
            self._spans, self._more, self._ready = [], False, not self._query or bool(self._error)
            self._changed()
            return
        self._ready = False
        self._changed()
        revision, text = self._document.revision(), self._text()

        def done(result: tuple[list[Span], bool]) -> None:
            if generation != self._generation or self._document is None:
                return
            if self._document.revision() != revision:  # typed meanwhile: once more, after the pause
                self._timer.start()
                return
            self._spans, self._more = result
            self._ready = True
            self._changed()
            if fresh:
                self.fresh.emit()

        self.app.bg.run(lambda: find_all(text, found, LIMIT), done, lambda _e: None)

    def _edited(self, position: int, removed: int, added: int) -> None:
        """Typing (or a replacement): the matches after the edit move with it, the ones it touched are dropped; the
        whole text is searched again after the pause."""
        if not self._active or not self._query:
            return
        if self._spans:
            shift, end = added - removed, position + removed
            self._spans = [(s + shift, e + shift) if s >= end and e > position else (s, e) for s, e in self._spans
                           if e <= position or s >= end]  # fmt: skip
        self._ready = False
        self._timer.start()
        self._changed()

    def _changed(self) -> None:
        self._revision += 1
        self.changed.emit()

    # ---------------------------------------------------------------- going and replacing

    def _at(self, start: int, end: int) -> int:
        at = bisect_left(self._spans, (start, end))
        return at if at < len(self._spans) and self._spans[at] == (start, end) else -1

    @Slot(int, result="QVariantList")
    def seek(self, position: int) -> list:
        """The first match from `position` on (round to the top past the end): [start, end], or [] - none."""
        if not self._spans:
            return []
        at = bisect_left(self._spans, (position, position))
        return list(self._spans[at % len(self._spans)])

    @Slot(int, int, bool, result="QVariantList")
    def step(self, start: int, end: int, backwards: bool) -> list:
        """The next match after the selection (F3, Enter), or the one before it (Shift+F3, Shift+Enter); round."""
        if not self._spans:
            return []
        if backwards:
            at = bisect_left(self._spans, (start, end)) - 1
        else:
            at = bisect_left(self._spans, (start, end))
            at += at < len(self._spans) and self._spans[at] <= (start, end)
            at %= len(self._spans)
        return list(self._spans[at])

    @Slot(int, int, str, result="QVariantList")
    def replace(self, start: int, end: int, template: str) -> list:
        """«Заменить»: the selected match replaced - one undo step - and the next one returned to select; when the
        selection is not a match, only the next one."""
        at = self._at(start, end)
        if at < 0 or self._pattern is None or self._document is None:
            return self.step(start, end, False)
        text = self._text()
        units = Units(text)
        match = self._pattern.match(text, units.index(start))
        if match is None or units.units(match.end()) != end:
            return self.step(start, end, False)
        try:
            new = replacement(match, template, self._regex)
        except re.error as exc:
            self.app.show_error(tr("Не заменено: {error}", error=exc.msg))
            return [start, end]
        del self._spans[at]  # replaced: not a match any more (an empty one, ^, would stay where the text went)
        cursor = QTextCursor(self._document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(new)
        return self.seek(start + len(new.encode("utf-16-le")) // 2)

    @Slot(str, result=int)
    def replaceAll(self, template: str) -> int:
        """«Заменить все»: every match in the text (not only the counted ones), in one edit block - one Ctrl+Z."""
        if self._pattern is None or self._document is None:
            return 0
        text = self._text()
        units = Units(text)
        try:
            planned = [(units.units(m.start()), units.units(m.end()), replacement(m, template, self._regex))
                       for m in self._pattern.finditer(text)]  # fmt: skip
        except re.error as exc:
            self.app.show_error(tr("Не заменено: {error}", error=exc.msg))
            return 0
        if not planned:
            return 0
        cursor = QTextCursor(self._document)
        cursor.beginEditBlock()
        for start, end, new in reversed(planned):  # from the end: the earlier positions stay where they are
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(new)
        cursor.endEditBlock()
        self.app.flash(tr("Заменено: {count}", count=len(planned)))
        return len(planned)
