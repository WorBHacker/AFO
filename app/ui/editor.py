"""The window's code editor (D47): files in tabs (14.4), each one's text in a TextEdit of its own (CodeEditor.qml,
its document and its undo history kept while another tab is shown), saved through the hub (PUT .../file, step 14.1) -
the version the editing began from checked, the file's encoding and line ends kept by the hub.

The text lives in the TextEdit's own QTextDocument; this side reads it from there (no copy of the file on every
key) and edits it there too - Enter keeping the indent, Tab and Shift+Tab over a selection - through a QTextCursor,
so that each is one undo step. A file another program, a model or a command changes while it is open: without
edits it is read again in place (the cursor stays); with edits the bar «файл изменился на диске» asks - take the
disk's, compare, keep mine (the next save writes over the disk's). Taking the disk's is an undo step too.

A tab with unsaved edits is not let go without asking - closing it, another project, the window closing: «Сохранить»,
«Не сохранять», «Отмена». Within DRAFT_MS of an edit its text goes to a draft in <data>/editor/drafts/; a window that
went without closing its tabs (a crash) finds them there: the tabs open again, «восстановлено».

Not edited here: a file larger than EDIT_LIMIT (1 MB - step 13.7 measured its colouring), a binary or cut file, one
with characters a TextEdit would turn into line ends (U+2028, U+2029, a lone CR) - saving would change them.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor, QTextDocument

from common import invisible

from ..hub_client import HubError
from ..presenter import plural
from . import project_rows
from .completions import Completer
from .finder import Finder
from .highlighter import Highlighter
from .models import RowModel

if TYPE_CHECKING:
    from .controller import Controller

EDIT_LIMIT = 1024 * 1024  # bytes: a bigger file opens in the viewer only (D47; 1 MB is coloured in 1.1 s, 13.7)
OPENERS = (":", "{", "[", "(")  # a line ending so opens a block: the next line goes one step further in
ENCODINGS = {"utf-8": "UTF-8", "utf-8 BOM": "UTF-8 с BOM", "cp1251": "Windows-1251", "utf-16": "UTF-16"}
REASONS = {"changed": "Файл изменился на диске, пока был открыт.", "gone": "Файл удалён с диска.",
           "exists": "Такой файл на диске уже есть."}  # fmt: skip
DRAFT_MS = 2000  # an edit's draft is on the disk within this long (14.4)
TAB_ROLES = ["key", "projectId", "path", "name", "folder", "dirty", "restored"]

log = logging.getLogger("app.editor")


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
    if "\u2028" in text or "\u2029" in text or "\r" in text.replace("\r\n", ""):
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


def draft_name(key: str) -> str:
    """A tab's draft file: by its project and path, hashed (a path may hold what a file name may not)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json"


def write_draft(path: Path, draft: dict[str, Any]) -> None:
    """Whole or not at all: written beside, then put in its place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    os.replace(spare, path)


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def read_draft(path: Path) -> dict[str, Any] | None:
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    fields = ("project_id", "path", "text", "base")
    return draft if isinstance(draft, dict) and all(isinstance(draft.get(f), str) for f in fields) else None


class Buffer:
    """One file open in a tab: its document (its TextEdit's own), its colours, the file as the hub gave it, the text
    last saved, and what the disk says when the two disagree."""

    def __init__(self, app: Controller, project_id: str, path: str):
        self.key = f"{project_id}:{path}"
        self.project_id, self.path = project_id, path
        self.quick: QObject | None = None
        self.document: QTextDocument | None = None
        self.highlighter = Highlighter(app)  # colours, the lines on screen first (14.2b)
        self.data: dict[str, Any] = {}  # the file as GET .../file gave it when the editing began or last saved
        self.base = self.saved = ""
        self.unit = "    "
        self.dirty = self.saving = self.restored = False
        self.conflict: dict[str, str] = {}  # {reason, message} while the disk and the editor disagree
        self.disk: dict[str, Any] | None = None  # the disk's version then (None: gone)
        self.compare: dict[str, Any] = {}
        self.where = ""
        self.cursor = 0
        self.hidden = ""
        self.waiting: str | None = None  # the text its TextEdit takes once it is there
        self.drafted = -1  # the document's revision its draft on the disk holds

    def text(self) -> str:
        if self.document is None:
            return self.waiting if self.waiting is not None else self.saved
        return self.document.toRawText().replace(" ", "\n").replace(" ", "\n")

    def row(self) -> dict[str, Any]:
        folder, _, name = self.path.rpartition("/")
        return {"key": self.key, "projectId": self.project_id, "path": self.path, "name": name, "folder": folder,
                "dirty": self.dirty, "restored": self.restored}  # fmt: skip


class EditorView(QObject):
    changed = Signal()
    moved = Signal()  # the cursor moved: only «строка, столбец» follows it, on every key
    loaded = Signal(str, str)  # a tab's text: its editor (by key) takes it - the undo history starts there
    placed = Signal(str, int)  # a tab's text replaced in place (the disk's version): its cursor goes back here
    closeWindow = Signal()  # the edits saved or let go: the window may close now

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._tabs = RowModel(self, TAB_ROLES)
        self._buffers: dict[str, Buffer] = {}
        self._active = ""
        self._opening = False  # a file being read for a new tab
        self._closing = False  # the window's edits are decided: it closes
        self.finder = Finder(app, self._text)  # find and replace (14.3), in the tab shown
        self.completer = Completer(app, self)  # the list under the cursor, F12 (14.5)
        app.theme.changed.connect(self._restyle)
        self._told: list[dict[str, str]] | None = []  # what the hub last heard of the unsaved edits (None: unknown)
        self._telling = False
        self._connections = 0  # the window's events connections so far: a word sent before the last one is stale
        self._drafts_timer = QTimer(self, singleShot=True, interval=DRAFT_MS)
        self._drafts_timer.timeout.connect(self.write_drafts)
        # the drafts' disk work: one worker, so a removal never passes a write of the same draft still on its way
        # (written in the window's thread a 1 MB draft took 60 ms - a stall every 2 s of typing)
        self._disk = ThreadPoolExecutor(1, thread_name_prefix="drafts")
        self.changed.connect(self.tell)

    @property
    def drafts(self) -> Path:
        return self.app.paths.root / "editor" / "drafts"

    def current(self) -> Buffer | None:
        return self._buffers.get(self._active)

    def buffer(self, key: str) -> Buffer | None:
        return self._buffers.get(key)

    def buffers(self) -> list[Buffer]:
        return list(self._buffers.values())

    # ---------------------------------------------------------------- what the window shows

    @Property(QObject, constant=True)
    def tabs(self) -> RowModel:
        return self._tabs

    @Property(str, notify=changed)
    def active(self) -> str:
        return self._active

    @Property(bool, notify=changed)
    def editing(self) -> bool:
        return self.current() is not None

    @Property(str, notify=changed)
    def projectId(self) -> str:
        b = self.current()
        return b.project_id if b else ""

    @Property(str, notify=changed)
    def path(self) -> str:
        b = self.current()
        return b.path if b else ""

    @Property(bool, notify=changed)
    def dirty(self) -> bool:
        b = self.current()
        return bool(b and b.dirty)

    @Property(bool, notify=changed)
    def restored(self) -> bool:
        b = self.current()
        return bool(b and b.restored)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        b = self.current()
        return self._opening or bool(b and b.saving)

    @Property("QVariantMap", notify=changed)
    def conflict(self) -> dict:
        b = self.current()
        return b.conflict if b else {}

    @Property("QVariantMap", notify=changed)
    def compareView(self) -> dict:
        b = self.current()
        return b.compare if b else {}

    @Property(str, notify=changed)
    def status(self) -> str:
        b = self.current()
        return status(b.data, b.unit) if b else ""

    @Property(str, notify=moved)
    def position(self) -> str:
        b = self.current()
        return b.where if b else ""

    @Property(int, notify=moved)
    def line(self) -> int:
        """The cursor's line, from 1 («Открыть в VS Code» goes there)."""
        b = self.current()
        return b.document.findBlock(b.cursor).blockNumber() + 1 if b and b.document else 1

    @Property(str, notify=changed)
    def hidden(self) -> str:
        b = self.current()
        return b.hidden if b else ""

    @Property(str, notify=changed)
    def unit(self) -> str:
        b = self.current()
        return b.unit if b else "    "

    @Slot(str, result=str)
    def unitOf(self, key: str) -> str:
        """A tab's step of indentation: its TextEdit's tab stops."""
        b = self._buffers.get(key)
        return b.unit if b else "    "

    # ---------------------------------------------------------------- opening, showing, closing

    @Slot(str, QObject)
    def attach(self, key: str, quick_document: QObject) -> None:
        """A tab's TextEdit is there: its document (textDocument) is where the text is read and edited. The
        QQuickTextDocument is kept too: PySide lets the QTextDocument's wrapper go with it."""
        b = self._buffers.get(key)
        if b is None:
            return
        b.quick = quick_document
        b.document = quick_document.textDocument() if hasattr(quick_document, "textDocument") else None
        if b.document is None:
            return
        b.highlighter.attach(b.document)
        b.document.contentsChange.connect(self._edited)  # a method, not a lambda: let go when this goes
        if key == self._active:
            self.finder.attach(b.document)
        if b.waiting is not None:
            text, b.waiting = b.waiting, None
            self.loaded.emit(key, text)
            b.highlighter.start(b.path)  # after the text is in: its colours, the top of the file first
            if b.restored and b.cursor:
                self.placed.emit(key, min(b.cursor, len(text)))  # where the cursor was when the window went

    @Slot(str, str)
    def open(self, project_id: str, path: str) -> None:
        """«Править»: the file in a tab of its own - or its tab, when it has one."""
        key = f"{project_id}:{path}"
        if key in self._buffers:
            self._activate(key)
            return
        if self._opening:
            return

        def done(data: dict[str, Any]) -> None:
            self._opening = False
            if reason := why_not(data):
                self.changed.emit()
                self.app.show_error(f"Файл не правится здесь: {reason}")
                return
            self._add(project_id, path, data)

        def failed(error: Exception) -> None:
            self._opening = False
            self.changed.emit()
            self.app.show_error(f"Файл не открылся для правки: {error}")

        self._opening = True
        self.changed.emit()
        self.app.call_or_fail(lambda: self.app.client.file(project_id, path), done, failed)

    def _add(self, project_id: str, path: str, data: dict[str, Any], draft: dict[str, Any] | None = None) -> Buffer:
        """A new tab, shown; its CodeEditor comes with its row and takes the text. `draft`: left by a window that
        went without closing it - its text over the disk's, «восстановлено»."""
        b = Buffer(self.app, project_id, path)
        text = data["text"].replace("\r\n", "\n")
        b.data, b.base, b.saved, b.unit, b.waiting = data, data["sha256"], text, indent_unit(text), text
        if draft is not None:
            b.waiting, b.base, b.cursor, b.restored = draft["text"], draft["base"], int(draft.get("cursor") or 0), True
            b.dirty = draft["text"] != text
            if data["sha256"] != draft["base"]:  # the disk changed since: the bar asks (14.2a)
                b.conflict, b.disk = {"reason": "changed", "message": REASONS["changed"]}, data
        b.hidden = invisible.summary(b.waiting)
        self._buffers[b.key] = b
        self._active = b.key
        self.changed.emit()  # shown first: its CodeEditor comes visible and lays the text out once, not twice
        self._tabs.append(b.row())  # its CodeEditor now: it attaches and takes the text
        return b

    def _activate(self, key: str) -> None:
        if key == self._active or key not in self._buffers:
            return
        self._active = key
        b = self._buffers[key]
        if b.document is not None:
            self.finder.attach(b.document)
        self.changed.emit()
        self.moved.emit()

    def show(self, project_id: str, path: str) -> None:
        """The window shows this file: its tab, when it has one, comes forward."""
        self._activate(f"{project_id}:{path}")

    def has(self, project_id: str, path: str) -> bool:
        return f"{project_id}:{path}" in self._buffers

    def goto(self, project_id: str, path: str, line: int) -> None:
        """A definition, a search result: the file's tab, if it has one, puts its cursor at the line's start."""
        b = self._buffers.get(f"{project_id}:{path}")
        if b is not None and b.document is not None:
            block = b.document.findBlockByNumber(max(0, line - 1))
            if block.isValid():
                self.placed.emit(b.key, block.position())

    def close(self, key: str, then=None) -> None:
        """A tab closed: with unsaved edits, after asking - «Сохранить» (it closes once saved), «Не сохранять»,
        «Отмена». `then(neighbour)`: once it is closed, with the path of the tab beside it in the same project
        ("" - none)."""
        b = self._buffers.get(key)
        if b is None:
            return
        self._check(b)
        if not b.dirty:
            self._drop(b, then)
            return

        def decide(choice: bool | None) -> None:
            if choice is True:
                self._save(b, lambda ok: ok and self._drop(b, then))
            elif choice is None:
                self._drop(b, then)

        self.app._ask(f"Сохранить правки в {b.path.rsplit('/', 1)[-1]}?", "Если не сохранить, правки пропадут.",
                      "Сохранить", decide, other="Не сохранять")  # fmt: skip

    @Slot()
    def stop(self) -> None:
        """«Готово»: the shown tab closed, back to the viewer - with unsaved edits, after asking."""
        if self._active:
            self.close(self._active)

    def _drop(self, b: Buffer, then=None) -> None:
        at = self._tabs.find(b.key)
        rows = self._tabs.rows
        beside = next((r for r in [*rows[at + 1 :], *reversed(rows[:at])] if r["projectId"] == b.project_id), None)
        b.highlighter.detach()
        b.highlighter.deleteLater()  # its tab's: gone with it, not kept by the window till the end
        if self._active == b.key:
            self.finder.detach()
            self._active = ""
        if b.document is not None:
            with contextlib.suppress(RuntimeError, TypeError):  # gone already
                b.document.contentsChange.disconnect(self._edited)
        b.document = b.quick = None  # its CodeEditor's: it goes with it
        self._forget_draft(b)
        self.completer.forget(b.key)
        del self._buffers[b.key]
        self._tabs.remove(at)  # its CodeEditor goes, its document with it
        self.changed.emit()
        if then is not None:
            then(beside["path"] if beside else "")

    def leave(self, then) -> bool:
        """Before another project: the tabs close. True when they could at once (no unsaved edits); otherwise asked
        - «Сохранить» saves them all (and stays if one is refused), «Не сохранять», «Отмена» - and `then` runs once
        they are closed."""
        for b in self.buffers():
            self._check(b)
        dirty = [b for b in self.buffers() if b.dirty]
        if not dirty:
            for b in self.buffers():
                self._drop(b)
            return True

        def closed() -> None:
            for b in self.buffers():
                self._drop(b)
            then()

        def decide(choice: bool | None) -> None:
            if choice is True:
                self._save_all(closed)
            elif choice is None:
                closed()

        self.app._ask(self._question(dirty), "Если не сохранить, правки пропадут.", "Сохранить", decide,
                      other="Не сохранять")  # fmt: skip
        return False

    @Slot(result=bool)
    def mayClose(self) -> bool:
        """The window is closing: True when it may (no unsaved edits, or they are decided); otherwise asked, and
        `closeWindow` comes once they are saved or let go."""
        if self._closing:
            return True
        for b in self.buffers():
            self._check(b)
        dirty = [b for b in self.buffers() if b.dirty]
        if not dirty:
            return True

        def closing() -> None:
            for b in self.buffers():
                self._forget_draft(b)
            self._closing = True
            self.closeWindow.emit()

        def decide(choice: bool | None) -> None:
            if choice is True:
                self._save_all(closing)
            elif choice is None:
                closing()

        self.app._ask(self._question(dirty), "Окно закрывается. Если не сохранить, правки пропадут.", "Сохранить",
                      decide, other="Не сохранять")  # fmt: skip
        return False

    @staticmethod
    def _question(dirty: list[Buffer]) -> str:
        if len(dirty) == 1:
            return f"Сохранить правки в {dirty[0].path.rsplit('/', 1)[-1]}?"
        return f"Сохранить правки в {len(dirty)} {plural(len(dirty), 'файле', 'файлах', 'файлах')}?"

    def _restyle(self) -> None:
        for b in self.buffers():
            b.highlighter.again()

    # ---------------------------------------------------------------- the text

    def _text(self) -> str:
        b = self.current()
        return b.text() if b else ""

    def _check(self, b: Buffer) -> None:
        text = b.text()
        dirty, hidden = text != b.saved, invisible.summary(text)
        if not dirty and b.drafted >= 0 and not b.conflict:
            self._forget_draft(b)  # clean again: nothing to keep
        if (dirty, hidden) != (b.dirty, b.hidden):
            b.dirty, b.hidden = dirty, hidden
            self._tabs.update(self._tabs.find(b.key), dirty=dirty)
            self.changed.emit()

    @Slot(str)
    def check(self, key: str) -> None:
        """After typing pauses: whether the tab's text differs from the saved one (an undo back to it is clean
        again)."""
        if (b := self._buffers.get(key)) is not None:
            self._check(b)

    @Slot(str, int)
    def locate(self, key: str, position: int) -> None:
        """The cursor moved: the bottom line's «строка, столбец»; and where it goes back after a replacement."""
        b = self._buffers.get(key)
        if b is not None and b.document is not None:
            b.cursor, b.where = position, where(b.document, position)
            if key == self._active:
                self.moved.emit()

    @Slot(str, int, int)
    def screen(self, key: str, first: int, count: int) -> None:
        """A tab's editor scrolled: the colours of the lines now on screen come first."""
        if (b := self._buffers.get(key)) is not None:
            b.highlighter.screen(first, count)

    @Slot(int, int, result=int)
    def newline(self, start: int, end: int) -> int:
        b = self.current()
        if b is None or b.document is None:
            return start
        return newline(b.document, start, end, b.unit)

    @Slot(int, int, bool, result=list)
    def tab(self, start: int, end: int, outdent: bool) -> list:
        b = self.current()
        if b is None or b.document is None:
            return [start, end]
        return list(tab(b.document, start, end, b.unit, outdent))

    # ---------------------------------------------------------------- saving

    @Slot()
    def save(self) -> None:
        if (b := self.current()) is not None:
            self._check(b)
            self._save(b)

    def _save(self, b: Buffer, then=None) -> None:
        """Through the hub (14.1). `then(saved)`: True once written, False when refused (the bar says why)."""
        if b.saving:
            return
        text = b.text()
        base, encoding = b.base, b.data.get("encoding", "utf-8")

        def done(result: dict[str, Any]) -> None:
            b.saving = False
            b.base, b.saved, b.conflict, b.disk, b.restored = result["sha256"], text, {}, None, False
            b.data = {**b.data, **{k: result[k] for k in ("sha256", "size", "encoding", "eol")}, "text": text}
            b.dirty = b.text() != text
            if b.key in self._buffers:
                self._tabs.update(self._tabs.find(b.key), dirty=b.dirty, restored=False)
            if not b.dirty:
                self._forget_draft(b)
            self.changed.emit()
            self.app.flash(f"Сохранено: {b.path.rsplit('/', 1)[-1]}")
            if then is not None:
                then(True)

        def failed(error: Exception) -> None:
            b.saving = False
            reason = error.data.get("reason") if isinstance(error, HubError) else None
            if reason and b.key in self._buffers:
                self._disagree(b, reason, error.data.get("file"))
            else:
                self.changed.emit()
                self.app.show_error(f"Не сохранено: {error}")
            if then is not None:
                then(False)

        b.saving = True
        self.changed.emit()
        self.app.call_or_fail(lambda: self.app.client.save_file(b.project_id, b.path, text, encoding, base), done,
                              failed)  # fmt: skip

    def _save_all(self, then) -> None:
        """Every tab with unsaved edits, one after another; `then` once all are written. One refused: it is shown
        with its bar, and the rest wait."""
        dirty = [b for b in self.buffers() if b.dirty]
        if not dirty:
            then()
            return

        def next_one(ok: bool) -> None:
            if not ok:
                self.app.projects.openFile(dirty[0].path, False)
                self.app.show_error(f"Не сохранено: {dirty[0].path.rsplit('/', 1)[-1]} — см. полосу над ним")
                return
            dirty.pop(0)
            if dirty:
                self._save(dirty[0], next_one)
            else:
                then()

        self._save(dirty[0], next_one)

    # ---------------------------------------------------------------- the disk

    def files_changed(self, project_id: str, paths: list[str], everything: bool) -> None:
        """The hub's watcher: files of a project changed. Each open one is read again: our own save (the same
        sha256) is nothing; without edits the tab takes it in place; with edits the bar asks."""
        self.completer.files_changed(project_id)
        for b in self.buffers():
            if b.project_id == project_id and (everything or b.path in paths):
                self._reread(b)

    def _reread(self, b: Buffer) -> None:
        def show(data: dict[str, Any] | None) -> None:
            if self._buffers.get(b.key) is not b:
                return
            if data is not None and data.get("sha256") == b.base:
                return
            self._check(b)
            if data is not None and not b.dirty and not why_not(data):
                self._take(b, data)
            else:
                self._disagree(b, "gone" if data is None else "changed", data)

        def fetch() -> dict[str, Any] | None:
            try:
                return self.app.client.file(b.project_id, b.path)
            except HubError as exc:
                if str(exc).startswith("not a file"):
                    return None  # gone from the disk
                raise  # the hub did not answer: nothing is known, nothing changes

        self.app._call(fetch, show, lambda _e: None)

    def _disagree(self, b: Buffer, reason: str, disk: dict[str, Any] | None) -> None:
        b.conflict = {"reason": reason, "message": REASONS.get(reason, reason)}
        b.disk = disk if disk and not disk.get("missing") else None
        b.compare = {}
        self.changed.emit()

    def _take(self, b: Buffer, data: dict[str, Any]) -> None:
        """The disk's version into the tab, in place: one undo step (Ctrl+Z gives the replaced text back)."""
        text = data["text"].replace("\r\n", "\n")
        place = self._replace(b, text)
        b.data, b.base, b.saved = data, data["sha256"], text
        b.conflict, b.disk, b.compare = {}, None, {}
        b.dirty, b.hidden = False, invisible.summary(text)
        self._tabs.update(self._tabs.find(b.key), dirty=False)
        self._forget_draft(b)
        self.changed.emit()
        self.placed.emit(b.key, place)

    @staticmethod
    def _replace(b: Buffer, text: str) -> int:
        """The whole text replaced; where the cursor goes back (where it was, or the end of a shorter text)."""
        if b.document is None:
            b.waiting = text
            return 0
        cursor = QTextCursor(b.document)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        return min(b.cursor, len(text))

    @Slot()
    def takeDisk(self) -> None:
        """«Взять с диска»: the disk's version replaces the edits (an undo step: Ctrl+Z gives them back)."""
        b = self.current()
        if b is not None and b.disk is not None and not why_not(b.disk):
            self._take(b, b.disk)

    @Slot()
    def keepMine(self) -> None:
        """«Оставить мой»: the tab's text stays, and the next save writes over the disk's (or makes the file again)."""
        b = self.current()
        if b is None or not b.conflict:
            return
        b.base = b.disk["sha256"] if b.disk else ""
        b.conflict, b.compare, b.dirty = {}, {}, True
        self._tabs.update(self._tabs.find(b.key), dirty=True)
        self.changed.emit()

    @Slot()
    def compare(self) -> None:
        """«Сравнить»: what saving would change on the disk - the disk's version against the tab's."""
        b = self.current()
        if b is None or not b.conflict:
            return
        disk = (b.disk or {}).get("text", "").replace("\r\n", "\n")
        name = b.path.rsplit("/", 1)[-1]
        text = "".join(difflib.unified_diff(disk.splitlines(keepends=True), b.text().splitlines(keepends=True),
                                            fromfile=f"{name} (на диске)", tofile=f"{name} (у вас)"))  # fmt: skip
        b.compare = {**project_rows.diff_view(text), "key": f"compare:{b.path}", "digits": 0, "empty": not text}
        self.changed.emit()

    # ---------------------------------------------------------------- drafts (14.4)

    def write_drafts(self) -> None:
        """The tabs with unsaved edits, each to its file in <data>/editor/drafts/ (the window's own files, not the
        hub's database): text, sha256 of the version the editing began from, cursor. Within DRAFT_MS of an edit; a
        tab whose text is the saved one again has none."""
        self._drafts_timer.stop()
        for b in self.buffers():
            if b.document is None or b.document.revision() == b.drafted:
                continue
            b.drafted = b.document.revision()
            text = b.text()
            if text == b.saved and not b.conflict:
                self._forget_draft(b)
                continue
            draft = {"project_id": b.project_id, "path": b.path, "text": text, "base": b.base, "cursor": b.cursor}
            self._disk.submit(self._write, b, self.drafts / draft_name(b.key), draft)

    @staticmethod
    def _write(b: Buffer, path: Path, draft: dict[str, Any]) -> None:
        try:
            write_draft(path, draft)
        except OSError as exc:  # the next edit tries again
            b.drafted = -1
            log.warning("draft of %s not written: %s", b.path, exc)

    def settle(self) -> None:
        """The drafts' disk work done (before the window goes, before drafts are read)."""
        self._disk.submit(lambda: None).result()

    def _edited(self, *_change) -> None:
        if not self._drafts_timer.isActive():
            self._drafts_timer.start()
        self.completer.edited()

    def _forget_draft(self, b: Buffer) -> None:
        b.drafted = -1
        self._disk.submit(_unlink, self.drafts / draft_name(b.key))

    def restore(self, project_id: str) -> None:
        """A project shown: the tabs it had with unsaved edits when the window went without closing them (a crash, a
        session that ended) open again with their text, «восстановлено»; a file the disk changed meanwhile has the
        bar, one gone is offered to be made again."""
        self.settle()
        drafts = []
        for path in sorted(self.drafts.glob("*.json")) if self.drafts.is_dir() else []:
            draft = read_draft(path)
            if draft and draft["project_id"] == project_id and f"{project_id}:{draft['path']}" not in self._buffers:
                drafts.append(draft)
        for draft in drafts:
            self._restore(draft)

    def _restore(self, draft: dict[str, Any]) -> None:
        pid, path = draft["project_id"], draft["path"]

        def fetch() -> dict[str, Any]:
            try:
                return self.app.client.file(pid, path)
            except HubError as exc:
                if str(exc).startswith("not a file"):
                    return {"path": path, "text": "", "sha256": "", "encoding": "utf-8", "eol": "LF", "size": 0,
                            "missing": True}  # fmt: skip
                raise

        def done(data: dict[str, Any]) -> None:
            if f"{pid}:{path}" in self._buffers or self.app.projects.currentId != pid:
                return
            if why_not(data) and not data.get("missing"):
                log.warning("draft of %s kept, not restored: %s", path, why_not(data))
                return
            if data["sha256"] == draft["base"] and data["text"].replace("\r\n", "\n") == draft["text"]:
                self._disk.submit(_unlink, self.drafts / draft_name(f"{pid}:{path}"))
                return  # nothing was lost: it is on the disk
            b = self._add(pid, path, data, draft)
            if data.get("missing"):
                self._disagree(b, "gone", None)
            self.app.projects.openFile(path, False)
            self.app.flash(f"Восстановлены несохранённые правки: {path.rsplit('/', 1)[-1]}")
            self.tell()

        self.app._call(fetch, done, lambda _e: None)

    # ---------------------------------------------------------------- what the hub knows

    def tell(self, again: bool = False) -> None:
        """The hub hears which files the tabs hold with edits not saved (D47, step 14.2c): a model's write into one
        is asked then, a trusted chat's too. Said when that changes, and `again` whenever the window's events
        connect - the hub forgets a window that went away, and a hub that restarted knows nothing. One word at a
        time, so the last one said is the one the hub keeps; one the hub did not hear is said at the next change or
        connection."""
        if again:
            self._connections += 1
            self._told = None
        files = [{"project_id": b.project_id, "path": b.path} for b in self.buffers() if b.dirty]
        if self._telling or files == self._told or self.app.client is None:
            return
        self._telling, connection = True, self._connections

        def done(_result: dict[str, Any]) -> None:
            self._telling = False
            if connection == self._connections:
                self._told = files
            self.tell()  # changed meanwhile: said again

        def failed(_error: Exception) -> None:
            self._telling = False

        self.app._call(lambda: self.app.client.editor_unsaved(files), done, failed)
