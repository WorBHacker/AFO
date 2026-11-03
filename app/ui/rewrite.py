"""The model edits the selection in the editor, or explains it (D47, step 14.7).

Ctrl+I over a selection (or the cursor's line) opens «что сделать»: the person's words go to the hub's model of the
role «Правка в редакторе» (POST /v1/editor/edit) with the selected lines - whole lines - and some code around them.
Its replacement shows as a diff in place of those lines; Ctrl+Enter takes it, one undo step; Esc does not. The file is
the person's to save: no approval card. Lines changed meanwhile under the proposal: it is not taken - asked again.

«Объяснить» and «Спросить в чате» open a new chat of the project with the selection in it - path, lines, the code:
the first sends «объясни» at once, the second leaves the text in the input for the person's question.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QTextCursor

from hub.symbols import language_of

from ..hub_client import HubError

if TYPE_CHECKING:
    from .controller import Controller
    from .editor import Buffer, EditorView

AROUND = 2000  # characters before and after the lines that go with them (the hub keeps as many)
FENCES = {"python": "python", "javascript": "javascript", "typescript": "typescript", "tsx": "tsx", "go": "go",
          "rust": "rust", "java": "java", "csharp": "csharp", "c": "c", "cpp": "cpp"}  # fmt: skip


def lines_of(b: Buffer, start: int, end: int) -> tuple[int, int, int, int]:
    """The whole lines a selection touches: their first and last numbers (from 1) and their span in the text."""
    first = b.document.findBlock(start)
    last = b.document.findBlock(end - 1 if end > start else end)
    return first.blockNumber() + 1, last.blockNumber() + 1, first.position(), last.position() + len(last.text())


def diff_rows(before: str, after: str) -> list[dict[str, str]]:
    """Line by line: kept (" "), taken out ("-"), put in ("+")."""
    rows = []
    for line in difflib.ndiff(before.split("\n"), after.split("\n")):
        if line[:1] in (" ", "-", "+"):
            rows.append({"kind": line[0], "text": line[2:]})
    return rows


class Rewriter(QObject):
    changed = Signal()

    def __init__(self, app: Controller, editor: EditorView):
        super().__init__(app)
        self.app = app
        self.editor = editor
        self._key = ""
        self._start = self._end = 0
        self._original = ""
        self._replacement: str | None = None
        self._rows: list[dict[str, str]] = []
        self._busy = False
        self._error = ""
        self._request = 0

    @Property(bool, notify=changed)
    def open(self) -> bool:
        return bool(self._key)

    @Property(str, notify=changed)
    def key(self) -> str:
        return self._key

    @Property(int, notify=changed)
    def start(self) -> int:
        return self._start

    @Property(int, notify=changed)
    def end(self) -> int:
        return self._end

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def ready(self) -> bool:
        return self._replacement is not None

    @Property(list, notify=changed)
    def rows(self) -> list:
        return self._rows

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot(str, int, int)
    def begin(self, key: str, start: int, end: int) -> None:
        """Ctrl+I: the lines the selection touches (or the cursor's line), and the field for what to do."""
        b = self.editor.buffer(key)
        if b is None or b.document is None:
            return
        _first, _last, self._start, self._end = lines_of(b, start, end)
        self._key, self._original = key, b.text()[self._start : self._end]
        self._replacement, self._rows, self._busy, self._error = None, [], False, ""
        self._request += 1
        self.changed.emit()

    @Slot(str)
    def ask(self, instruction: str) -> None:
        b = self.editor.buffer(self._key)
        if b is None or not instruction.strip() or self._busy:
            return
        text = b.text()
        before, after = text[: self._start][-AROUND:], text[self._end :][:AROUND]
        self._request += 1
        number, original = self._request, self._original

        def done(found: dict[str, Any]) -> None:
            if number != self._request:
                return
            self._busy = False
            self._replacement = str(found.get("replacement", ""))
            self._rows = diff_rows(original, self._replacement)
            self.changed.emit()

        def failed(error: Exception) -> None:
            if number != self._request:
                return
            self._busy = False
            said = error.data.get("message") if isinstance(error, HubError) and error.data else ""
            self._error = said or str(error)
            self.changed.emit()

        self._busy, self._error, self._replacement, self._rows = True, "", None, []
        self.changed.emit()
        self.app.call_or_fail(lambda: self.app.client.editor_edit(b.project_id, b.path, original, before, after,
                                                                  instruction.strip()), done, failed)  # fmt: skip

    @Slot(result=int)
    def accept(self) -> int:
        """Ctrl+Enter: the replacement in place of the lines, one undo step; where the cursor goes (its end)."""
        b = self.editor.buffer(self._key)
        if self._replacement is None or b is None or b.document is None:
            return self._end
        if b.text()[self._start : self._end] != self._original:
            self._error = "Текст под правкой изменился — закройте и попросите снова"
            self.changed.emit()
            return self._end
        cursor = QTextCursor(b.document)
        cursor.beginEditBlock()
        cursor.setPosition(self._start)
        cursor.setPosition(self._end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(self._replacement)
        cursor.endEditBlock()
        self.close()
        return cursor.position()

    @Slot()
    def close(self) -> None:
        self._request += 1
        if self._key:
            self._key, self._replacement, self._rows, self._busy, self._error = "", None, [], False, ""
            self.changed.emit()

    # ---------------------------------------------------------------- to the chat

    def _quoted(self, key: str, start: int, end: int) -> str | None:
        b = self.editor.buffer(key)
        if b is None or b.document is None:
            return None
        first, last, low, high = lines_of(b, start, end)
        where = f"`{b.path}`, " + (f"строка {first}" if first == last else f"строки {first}–{last}")
        fence = FENCES.get(language_of(b.path) or "", "")
        return f"{where}:\n```{fence}\n{b.text()[low:high]}\n```"

    @Slot(str, int, int)
    def explain(self, key: str, start: int, end: int) -> None:
        """«Объяснить»: a new chat of the project, asked at once."""
        quoted = self._quoted(key, start, end)
        if quoted is not None:
            self.close()
            self.app.newProjectChat()
            self.app.send(f"Объясни этот код из {quoted}")

    @Slot(str, int, int)
    def askInChat(self, key: str, start: int, end: int) -> None:
        """«Спросить в чате»: a new chat of the project, the selection in its input for the person's question."""
        quoted = self._quoted(key, start, end)
        if quoted is not None:
            self.close()
            self.app.keep_new_draft(f"Про код из {quoted}\n\n")
            self.app.newProjectChat()
