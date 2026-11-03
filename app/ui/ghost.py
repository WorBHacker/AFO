"""The model's grey text after the cursor in the editor, like Copilot (D47, step 14.6).

Once typing pauses (IDLE_MS) at the end of a line - only closing brackets, quotes or spaces after the cursor - the
hub's completion model (the role «Дописывание кода», hub/completion.py) is asked with the code around the cursor;
what it sends comes in as grey text, on several lines too. Tab takes it all (one undo step), Ctrl+→ a word, Esc
puts it away; typing what it begins with keeps the rest. The next key cancels a request still on its way: its stream
is closed, the hub cancels its own to llama.cpp.

A pause the hub reports (no model chosen, a local one not loaded beside the chat's, a cloud one for a local-only
project, a secret file) is said in the editor's bottom line; «Загрузить модель» starts a local one when the person
says so - it may push the chat's model out of the graphics card. On or off: everywhere, and per project (the
window's settings).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor

if TYPE_CHECKING:
    from .controller import Controller
    from .editor import EditorView

IDLE_MS = 300
BEFORE, AFTER = 3000, 1000  # characters around the cursor sent (the hub keeps the same)
LINE_END = re.compile(r"[\s)\]}\"',;:]*")  # what may follow the cursor for grey text to be asked
WORD = re.compile(r"\s*(\w+|[^\w\s]+)")  # Ctrl+→ takes this much


class Ghost(QObject):
    changed = Signal()
    piece = Signal(int, str)  # from the worker: (request, its next text)

    def __init__(self, app: Controller, editor: EditorView):
        super().__init__(app)
        self.app = app
        self.editor = editor
        self._text = ""
        self._key = ""
        self._position = 0
        self._state = ""  # a pause, as the hub said it
        self._paused = ""  # its reason
        self._request = 0  # bumped by every key: a request whose number differs is left to end
        self._load = False
        self._timer = QTimer(self, singleShot=True, interval=IDLE_MS)
        self._timer.timeout.connect(self._ask)
        self.piece.connect(self._got)

    # ---------------------------------------------------------------- what the window shows

    @Property(str, notify=changed)
    def text(self) -> str:
        return self._text

    @Property(str, notify=changed)
    def key(self) -> str:
        return self._key

    @Property(int, notify=changed)
    def position(self) -> int:
        return self._position

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(str, notify=changed)
    def paused(self) -> str:
        return self._paused

    @Property(bool, notify=changed)
    def everywhere(self) -> bool:
        return str(self.app.settings.value("editor/complete", "on")) != "off"

    @Slot(str, result=bool)
    def inProject(self, project_id: str) -> bool:
        return project_id not in self._off_projects()

    def _off_projects(self) -> list[str]:
        found = self.app.settings.value("editor/complete_off", [])
        return [found] if isinstance(found, str) and found else list(found or [])

    def on(self, project_id: str) -> bool:
        return self.everywhere and self.inProject(project_id)

    @Slot(bool)
    def setEverywhere(self, on: bool) -> None:
        self.app.settings.setValue("editor/complete", "on" if on else "off")
        self.dismiss()
        self.changed.emit()

    @Slot(str, bool)
    def setInProject(self, project_id: str, on: bool) -> None:
        off = [p for p in self._off_projects() if p != project_id] + ([] if on else [project_id])
        self.app.settings.setValue("editor/complete_off", off)
        self.dismiss()
        self.changed.emit()

    # ---------------------------------------------------------------- typing

    @Slot(str, int, str)
    def typed(self, key: str, position: int, char: str) -> None:
        """A key put `char` in before `position` ("" - something else changed the text): grey text that begins with
        it stays, the rest of it; anything else puts it away. Grey text is asked again once typing pauses."""
        if char and self._text.startswith(char) and key == self._key and position == self._position + len(char):
            self._request += 1  # the one on its way would not fit any more
            self._text, self._position = self._text[len(char) :], position
            self.changed.emit()
        else:
            self.dismiss()
        self._key = key
        if self.app.client is not None and (b := self.editor.buffer(key)) is not None and self.on(b.project_id):
            self._timer.start()

    @Slot(str, int)
    def moved(self, key: str, position: int) -> None:
        """The cursor went elsewhere (a click, the arrows): the grey text goes, and the request on its way."""
        if key == self._key and position != self._position:
            self.dismiss()

    @Slot()
    def dismiss(self) -> None:
        self._timer.stop()
        self._request += 1
        if self._text:
            self._text = ""
            self.changed.emit()

    def _ask(self) -> None:
        b = self.editor.buffer(self._key)
        if b is None or b.document is None or self.editor.completer.open or not self.on(b.project_id):
            return
        position = b.cursor
        block = b.document.findBlock(position)
        if not LINE_END.fullmatch(block.text()[position - block.position() :]):
            return  # in the middle of a line: no grey text over what is there
        text = b.text()
        before, after = text[:position][-BEFORE:], text[position:][:AFTER]
        self._request += 1
        number, load = self._request, self._load
        self._load = False
        self._position, self._text = position, ""

        def work() -> dict[str, Any]:
            last: dict[str, Any] = {}
            for line in self.app.client.complete(b.project_id, b.path, before, after, load=load,
                                                 stop=lambda: number != self._request):  # fmt: skip
                if "text" in line:
                    self.piece.emit(number, line["text"])
                last = line
            return last

        def done(last: dict[str, Any]) -> None:
            if "paused" in last:
                self._state, self._paused = last.get("message", ""), last["paused"]
            elif "model" in last or "done" in last:
                self._state = self._paused = ""
            self.changed.emit()

        self.app.bg.run(work, done, lambda _e: None)

    def _got(self, number: int, text: str) -> None:
        if number == self._request:
            self._text += text
            self.changed.emit()

    @Slot()
    def load(self) -> None:
        """«Загрузить модель»: the person's word - a local model not loaded may be loaded now (the chat's may go)."""
        self._load = True
        self._state, self._paused = "Дописывание: модель загружается…", ""
        self.changed.emit()
        self._ask()

    # ---------------------------------------------------------------- taking it

    @Slot(bool, result=int)
    def accept(self, word: bool) -> int:
        """Tab - all of it, Ctrl+→ - a word: in at the cursor, one undo step; where the cursor goes. A word taken
        leaves the rest grey."""
        b = self.editor.buffer(self._key)
        if not self._text or b is None or b.document is None:
            return self._position
        found = WORD.match(self._text) if word else None
        taken = found.group(0) if found else self._text
        cursor = QTextCursor(b.document)
        cursor.setPosition(self._position)
        cursor.insertText(taken)
        self._request += 1
        self._text, self._position = self._text[len(taken) :], cursor.position()
        self.changed.emit()
        return cursor.position()
