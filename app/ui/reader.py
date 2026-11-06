"""The viewer's text (24.1): a file or a diff in one read-only TextEdit (CodeView.qml) - selected through the whole of
it - coloured the way the editor colours (app/ui/highlighter.py, 14.2b): the lines on screen first, the rest when the
screen gets there. Each CodeView has its name here: the project's file ("file"), the editor's «Сравнить» ("compare").
A file over the editor's limit is shown in pieces, plain (CodePieces.qml): it has no colours here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot

from .highlighter import Highlighter, lexer_named

if TYPE_CHECKING:
    from .controller import Controller


class Readers(QObject):
    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._colours: dict[str, Highlighter] = {}
        self._quick: dict[str, QObject] = {}  # PySide lets a QTextDocument's wrapper go with its QQuickTextDocument
        self._seen: dict[str, tuple[str, int]] = {}  # the lexer each one is coloured by, and its text's revision then
        app.theme.changed.connect(self._restyle)

    @Slot(str, QObject)
    def attach(self, name: str, quick_document: QObject) -> None:
        """A CodeView's TextEdit is there: its document gets the colours."""
        document = quick_document.textDocument() if hasattr(quick_document, "textDocument") else None
        if document is None:
            return
        if name not in self._colours:
            self._colours[name] = Highlighter(self.app, typing=False)
        self._quick[name] = quick_document
        self._seen.pop(name, None)
        self._colours[name].attach(document)

    @Slot(str, str, int, int)
    def shown(self, name: str, lexer: str, first: int, count: int) -> None:
        """A CodeView's text is in, these lines on screen: its colours by `lexer` (Pygments' name, "diff", "" - none).
        The same text by the same lexer keeps the colours it has."""
        colours = self._colours.get(name)
        if colours is None or colours.document is None:
            return
        colours.screen(first, count)
        seen = (lexer, colours.document.revision())
        if self._seen.get(name) != seen:
            self._seen[name] = seen
            colours.begin(lexer_named(lexer))

    @Slot(str)
    def hidden(self, name: str) -> None:
        """A CodeView out of sight (the editor over it, another view): its colours stop, the lexing in the background
        too - the editor's own would wait for it; shown again, they begin anew."""
        if (colours := self._colours.get(name)) is not None:
            colours.stop()
            self._seen.pop(name, None)

    @Slot(str, int, int)
    def screen(self, name: str, first: int, count: int) -> None:
        """A CodeView scrolled: the colours of the lines now on screen come first."""
        if (colours := self._colours.get(name)) is not None:
            colours.screen(first, count)

    @Slot(str, int, result=int)
    def lineStart(self, name: str, line: int) -> int:
        """Where line `line` (from 1) begins in a CodeView's text, in Qt's units (an emoji counts two); -1 - none."""
        colours = self._colours.get(name)
        if colours is None or colours.document is None or line < 1:
            return -1
        block = colours.document.findBlockByNumber(line - 1)
        return block.position() if block.isValid() else -1

    def _restyle(self) -> None:
        for colours in self._colours.values():
            colours.again()
