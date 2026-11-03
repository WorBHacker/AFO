"""The window's code editor, one file at a time (D47, step 14.2): the open file's text in a TextEdit
(CodeEditor.qml), saved through the hub (PUT .../file, step 14.1) - the version the editing began from checked, the
file's encoding and line ends kept by the hub.

The text lives in the TextEdit's own QTextDocument; this side reads it from there (no copy of the file on every
key) and edits it there too - Enter keeping the indent, Tab and Shift+Tab over a selection - through a QTextCursor,
so that each is one undo step. A file another program, a model or a command changes while it is open: without
edits it is read again in place (the cursor stays); with edits the bar «файл изменился на диске» asks - take the
disk's, compare, keep mine (the next save writes over the disk's). Taking the disk's is an undo step too.

Not edited here: a file larger than EDIT_LIMIT (1 MB - step 13.7 measured its colouring), a binary or cut file, one
with characters a TextEdit would turn into line ends (U+2028, U+2029, a lone CR) - saving would change them.
"""

from __future__ import annotations

import difflib
from collections import Counter
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QTextCursor, QTextDocument

from common import invisible

from ..hub_client import HubError
from . import project_rows

if TYPE_CHECKING:
    from .controller import Controller

EDIT_LIMIT = 1024 * 1024  # bytes: a bigger file opens in the viewer only (D47; 1 MB is coloured in 1.1 s, 13.7)
OPENERS = (":", "{", "[", "(")  # a line ending so opens a block: the next line goes one step further in
ENCODINGS = {"utf-8": "UTF-8", "utf-8 BOM": "UTF-8 с BOM", "cp1251": "Windows-1251", "utf-16": "UTF-16"}
REASONS = {"changed": "Файл изменился на диске, пока был открыт.", "gone": "Файл удалён с диска.",
           "exists": "Такой файл на диске уже есть."}  # fmt: skip


def why_not(data: dict[str, Any]) -> str:
    """Why a file the hub gave (GET .../file) is not edited here; "" when it is."""
    text = data.get("text", "")
    if data.get("missing"):
        return "файла нет на диске"
    if data.get("binary"):
        return "двоичный файл"
    if data.get("truncated") or data.get("size", 0) > EDIT_LIMIT:
        return "больше 1 МБ: только просмотр (правьте в VS Code)"
    if data.get("encoding") not in ENCODINGS:
        return "незнакомая кодировка"
    if " " in text or " " in text or "\r" in text.replace("\r\n", ""):
        return "в файле есть разделители строк, которые редактор поменял бы (U+2028, U+2029, одиночный CR)"
    return ""


def indent_unit(text: str) -> str:
    """The file's own step of indentation: a tab when most indented lines begin with one, else the commonest step
    between neighbouring indents (2, 4 or 8 spaces); 4 spaces for a file that does not tell."""
    tabs = spaced = previous = 0
    steps: Counter[int] = Counter()
    for line in text.split("\n", 5000)[:5000]:
        if not line.strip():
            continue
        if line.startswith("\t"):
            tabs += 1
            continue
        width = len(line) - len(line.lstrip(" "))
        spaced += width > 0
        if width > previous:
            steps[width - previous] += 1
        previous = width
    if tabs > spaced:
        return "\t"
    return " " * next((step for step, _n in steps.most_common() if step in (2, 4, 8)), 4)


def next_indent(before: str, unit: str) -> str:
    """What a new line starts with after `before` (its line up to the cursor): the same indent, one step more
    after an opening `:`, `{`, `[` or `(`."""
    indent = before[: len(before) - len(before.lstrip(" \t"))]
    return indent + unit if before.rstrip().endswith(OPENERS) else indent


def newline(document: QTextDocument, start: int, end: int, unit: str) -> int:
    """Enter: the selection (if any) replaced by a line end and the next line's indent, one undo step; the cursor's
    place after it."""
    block = document.findBlock(start)
    cursor = QTextCursor(document)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.beginEditBlock()
    cursor.insertText("\n" + next_indent(block.text()[: start - block.position()], unit))
    cursor.endEditBlock()
    return cursor.position()


def tab(document: QTextDocument, start: int, end: int, unit: str, outdent: bool) -> tuple[int, int]:
    """Tab and Shift+Tab, one undo step. Without a selection Tab puts one step at the cursor (spaces up to the next
    stop); with one, every line it touches goes one step in - empty lines stay empty - and Shift+Tab takes up to
    one step off each. The selection after it: the cursor's place, or the lines from start to end."""
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    try:
        if start == end and not outdent:
            column = start - document.findBlock(start).position()
            cursor.setPosition(start)
            cursor.insertText(unit if unit == "\t" else " " * (len(unit) - column % len(unit)))
            return cursor.position(), cursor.position()
        first = document.findBlock(start)
        last = document.findBlock(end - 1 if end > start else end)
        block = first
        while block.isValid():
            text = block.text()
            cursor.setPosition(block.position())
            if outdent:
                width = len(unit) if unit != "\t" else 4
                cut = 1 if text.startswith("\t") else min(width, len(text) - len(text.lstrip(" ")))
                cursor.setPosition(block.position() + cut, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            elif text.strip():
                cursor.insertText(unit)
            if block == last:
                break
            block = block.next()
        return first.position(), last.position() + len(last.text())
    finally:
        cursor.endEditBlock()


def where(document: QTextDocument, position: int) -> str:
    block = document.findBlock(position)
    return f"Строка {block.blockNumber() + 1}, столбец {position - block.position() + 1}"


def status(data: dict[str, Any], unit: str) -> str:
    """'UTF-8 · CRLF · пробелы: 4' for the editor's bottom line; a file of mixed line ends is told how it is saved."""
    eol = data.get("eol", "")
    if eol == "CRLF+LF":
        crlf = data.get("text", "").count("\r\n")
        eol = f"CRLF и LF — сохранится как {'CRLF' if crlf > data.get('text', '').count(chr(10)) - crlf else 'LF'}"
    step = "табуляция" if unit == "\t" else f"пробелы: {len(unit)}"
    return " · ".join(s for s in (ENCODINGS.get(data.get("encoding", ""), ""), eol, step) if s)


class EditorView(QObject):
    changed = Signal()
    moved = Signal()  # the cursor moved: only «строка, столбец» follows it, on every key
    loaded = Signal(str)  # a file opened: the editor takes this text (the undo history starts there)
    placed = Signal(int)  # the text was replaced in place (the disk's version): the cursor goes back here

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._quick: QObject | None = None
        self._document: QTextDocument | None = None
        self._clear()

    def _clear(self) -> None:
        self._project_id = self._path = self._base = self._saved = ""
        self._data: dict[str, Any] = {}  # the file as GET .../file gave it when the editing began or last saved
        self._unit = "    "
        self._dirty = self._busy = False
        self._conflict: dict[str, str] = {}  # {reason, message} while the disk and the editor disagree
        self._disk: dict[str, Any] | None = None  # the disk's version then (None: gone)
        self._compare: dict[str, Any] = {}
        self._where = ""
        self._cursor = 0
        self._hidden = ""

    # ---------------------------------------------------------------- what the window shows

    @Property(bool, notify=changed)
    def editing(self) -> bool:
        return bool(self._path)

    @Property(str, notify=changed)
    def projectId(self) -> str:
        return self._project_id

    @Property(str, notify=changed)
    def path(self) -> str:
        return self._path

    @Property(bool, notify=changed)
    def dirty(self) -> bool:
        return self._dirty

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property("QVariantMap", notify=changed)
    def conflict(self) -> dict:
        return self._conflict

    @Property("QVariantMap", notify=changed)
    def compareView(self) -> dict:
        return self._compare

    @Property(str, notify=changed)
    def status(self) -> str:
        return status(self._data, self._unit) if self._path else ""

    @Property(str, notify=moved)
    def position(self) -> str:
        return self._where

    @Property(str, notify=changed)
    def hidden(self) -> str:
        return self._hidden

    @Property(str, notify=changed)
    def unit(self) -> str:
        return self._unit

    # ---------------------------------------------------------------- opening and leaving

    @Slot(QObject)
    def attach(self, quick_document: QObject) -> None:
        """The TextEdit's document (its textDocument): where the text is read and edited. The QQuickTextDocument is
        kept too: PySide lets the QTextDocument's wrapper go with it."""
        self._quick = quick_document
        self._document = quick_document.textDocument() if hasattr(quick_document, "textDocument") else None

    @Slot(str, str)
    def open(self, project_id: str, path: str) -> None:
        if self._busy or (self._path == path and self._project_id == project_id):
            return

        def done(data: dict[str, Any]) -> None:
            self._busy = False
            if reason := why_not(data):
                self.changed.emit()
                self.app.show_error(f"Файл не правится здесь: {reason}")
                return
            text = data["text"].replace("\r\n", "\n")
            self._clear()
            self._project_id, self._path, self._data = project_id, path, data
            self._base, self._saved, self._unit = data["sha256"], text, indent_unit(text)
            self._hidden = invisible.summary(text)
            self.changed.emit()
            self.loaded.emit(text)

        def failed(error: Exception) -> None:
            self._busy = False
            self.changed.emit()
            self.app.show_error(f"Файл не открылся для правки: {error}")

        self._busy = True
        self.changed.emit()
        self.app.call_or_fail(lambda: self.app.client.file(project_id, path), done, failed)

    def leave(self, project_id: str, path: str, then) -> bool:
        """Before the window shows another file or project: True when it may go on now (no edits, or the same file);
        with edits it asks, and `then` runs once the user lets them go."""
        if not self._path or (project_id == self._project_id and path == self._path):
            return True
        if not self._dirty:
            self._stop()
            return True

        def decide(yes: bool) -> None:
            if yes:
                self._stop()
                then()

        self.app._ask(f"Не сохранять правки в {self._path.rsplit('/', 1)[-1]}?",
                      "Правки этого файла пропадут. Чтобы их оставить, отмените и сохраните (Ctrl+S).", "Не сохранять",
                      decide)  # fmt: skip
        return False

    @Slot()
    def stop(self) -> None:
        """«Готово»: back to the viewer - with unsaved edits, after asking."""
        if self.leave("", "", lambda: None):
            self._stop()

    def _stop(self) -> None:
        self._clear()
        self.changed.emit()

    # ---------------------------------------------------------------- the text

    def _text(self) -> str:
        if self._document is None:
            return self._saved
        return self._document.toRawText().replace(" ", "\n").replace(" ", "\n")

    @Slot()
    def check(self) -> None:
        """After typing pauses: whether the text differs from the saved one (an undo back to it is clean again)."""
        if not self._path:
            return
        text = self._text()
        dirty, hidden = text != self._saved, invisible.summary(text)
        if (dirty, hidden) != (self._dirty, self._hidden):
            self._dirty, self._hidden = dirty, hidden
            self.changed.emit()

    @Slot(int)
    def locate(self, position: int) -> None:
        """The cursor moved: the bottom line's «строка, столбец»; and where it goes back after a replacement."""
        if self._document is not None and self._path:
            self._cursor, self._where = position, where(self._document, position)
            self.moved.emit()

    @Slot(int, int, result=int)
    def newline(self, start: int, end: int) -> int:
        if self._document is None:
            return start
        return newline(self._document, start, end, self._unit)

    @Slot(int, int, bool, result=list)
    def tab(self, start: int, end: int, outdent: bool) -> list:
        if self._document is None:
            return [start, end]
        return list(tab(self._document, start, end, self._unit, outdent))

    # ---------------------------------------------------------------- saving

    @Slot()
    def save(self) -> None:
        if self._busy or not self._path:
            return
        text = self._text()
        pid, path, base, encoding = self._project_id, self._path, self._base, self._data.get("encoding", "utf-8")

        def done(result: dict[str, Any]) -> None:
            self._busy = False
            if self._path != path:
                return
            self._base, self._saved, self._conflict, self._disk = result["sha256"], text, {}, None
            self._data = {**self._data, **{k: result[k] for k in ("sha256", "size", "encoding", "eol")},
                          "text": text}  # fmt: skip
            self._dirty = self._text() != text
            self.changed.emit()
            self.app.flash(f"Сохранено: {path.rsplit('/', 1)[-1]}")

        def failed(error: Exception) -> None:
            self._busy = False
            reason = error.data.get("reason") if isinstance(error, HubError) else None
            if reason and self._path == path:
                self._disagree(reason, error.data.get("file"))
            else:
                self.changed.emit()
                self.app.show_error(f"Не сохранено: {error}")

        self._busy = True
        self.changed.emit()
        self.app.call_or_fail(lambda: self.app.client.save_file(pid, path, text, encoding, base), done, failed)

    # ---------------------------------------------------------------- the disk

    def files_changed(self, project_id: str, paths: list[str], everything: bool) -> None:
        """The hub's watcher: files of a project changed. The open one is read again: our own save (the same sha256)
        is nothing; without edits the editor takes it in place; with edits the bar asks."""
        if not self._path or project_id != self._project_id or not (everything or self._path in paths):
            return
        pid, path = self._project_id, self._path

        def show(data: dict[str, Any] | None) -> None:
            if self._path != path or self._project_id != pid:
                return
            if data is not None and data.get("sha256") == self._base:
                return
            if data is not None and not self._dirty and not why_not(data):
                self._take(data)
            else:
                self._disagree("gone" if data is None else "changed", data)

        def fetch() -> dict[str, Any] | None:
            try:
                return self.app.client.file(pid, path)
            except HubError as exc:
                if str(exc).startswith("not a file"):
                    return None  # gone from the disk
                raise  # the hub did not answer: nothing is known, nothing changes

        self.app._call(fetch, show, lambda _e: None)

    def _disagree(self, reason: str, disk: dict[str, Any] | None) -> None:
        self._conflict = {"reason": reason, "message": REASONS.get(reason, reason)}
        self._disk = disk if disk and not disk.get("missing") else None
        self._compare = {}
        self.changed.emit()

    def _take(self, data: dict[str, Any]) -> None:
        """The disk's version into the editor, in place: one undo step (Ctrl+Z gives the replaced text back)."""
        text = data["text"].replace("\r\n", "\n")
        place = self._replace(text)
        self._data, self._base, self._saved = data, data["sha256"], text
        self._conflict, self._disk, self._compare = {}, None, {}
        self._dirty, self._hidden = False, invisible.summary(text)
        self.changed.emit()
        self.placed.emit(place)

    def _replace(self, text: str) -> int:
        """The whole text replaced; where the cursor goes back (where it was, or the end of a shorter text)."""
        if self._document is None:
            return 0
        cursor = QTextCursor(self._document)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        return min(self._cursor, len(text))

    @Slot()
    def takeDisk(self) -> None:
        """«Взять с диска»: the disk's version replaces the edits (an undo step: Ctrl+Z gives them back)."""
        if self._disk is not None and not why_not(self._disk):
            self._take(self._disk)

    @Slot()
    def keepMine(self) -> None:
        """«Оставить мой»: the editor's text stays, and the next save writes over the disk's (or makes the file
        again)."""
        if not self._conflict:
            return
        self._base = self._disk["sha256"] if self._disk else ""
        self._conflict, self._compare, self._dirty = {}, {}, True
        self.changed.emit()

    @Slot()
    def compare(self) -> None:
        """«Сравнить»: what saving would change on the disk - the disk's version against the editor's."""
        if not self._conflict:
            return
        disk = (self._disk or {}).get("text", "").replace("\r\n", "\n")
        name = self._path.rsplit("/", 1)[-1]
        text = "".join(difflib.unified_diff(disk.splitlines(keepends=True), self._text().splitlines(keepends=True),
                                            fromfile=f"{name} (на диске)", tofile=f"{name} (у вас)"))  # fmt: skip
        self._compare = {**project_rows.diff_view(text), "key": f"compare:{self._path}", "digits": 0,
                         "empty": not text}  # fmt: skip
        self.changed.emit()
