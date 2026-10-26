"""The composer's mentions (stage 3, D27): the @-menu while typing, and projects named in the draft.

Typing `@` offers the projects, `@project/` that project's files (the hub finds them by a few letters). A project
the draft names without @ is offered for attaching, unless it is attached already or was waved away in this chat.
What a sent message mentions with @ is attached and pinned by the controller (Controller._send).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import mentions

if TYPE_CHECKING:
    from .controller import Controller

FILES_DELAY_MS = 150


class Mentions(QObject):
    menuChanged = Signal()
    suggestionsChanged = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._start = -1  # where the @ being typed is
        self._items: list[dict[str, Any]] = []
        self._files_for: tuple[dict[str, Any], str] | None = None  # (project, part) waiting for the hub
        self._files_gen = 0
        self._files_timer = app._timer(FILES_DELAY_MS, self._ask_files, single=True)
        self._draft = ""
        self._suggested: list[dict[str, Any]] = []
        self._waved: set[str] = set()  # projects the user did not want suggested in this chat
        app.currentChanged.connect(self._new_chat)
        app.chatProjectsChanged.connect(lambda: self._suggest(self._draft))

    # ================= properties for QML =================

    @Property(list, notify=menuChanged)
    def items(self) -> list:
        return self._items

    @Property(int, notify=menuChanged)
    def start(self) -> int:
        return self._start

    @Property(list, notify=suggestionsChanged)
    def suggestions(self) -> list:
        return self._suggested

    # ================= typing =================

    @Slot(str, int)
    def typed(self, text: str, cursor: int) -> None:
        """The draft or the cursor changed: the @-menu follows, and so do the suggestions."""
        self._suggest(text)
        word = mentions.at_word(text, cursor)
        if word is None:
            self.close()
            return
        self._start, query = word
        files = mentions.file_query(query, self.app.projects.items)
        if files is None:
            self._files_timer.stop()
            self._files_for = None
            self._items = mentions.project_choices(query, self.app.projects.items)
        else:
            self._files_for = files
            self._files_timer.start()
        self.menuChanged.emit()

    def _ask_files(self) -> None:
        if self._files_for is None or self.app.client is None:
            return
        project, part = self._files_for
        self._files_gen += 1
        generation = self._files_gen

        def show(found: list[str]) -> None:
            if generation == self._files_gen and self._files_for is not None:
                self._items = mentions.file_choices(project, found)
                self.menuChanged.emit()

        pid = project["id"]
        self.app._call(lambda: self.app.client.find_files(pid, part), show, lambda _e: None)

    @Slot()
    def close(self) -> None:
        self._files_timer.stop()
        self._files_for = None
        self._files_gen += 1
        if self._items or self._start != -1:
            self._items, self._start = [], -1
            self.menuChanged.emit()

    # ================= projects named in passing =================

    def _suggest(self, text: str) -> None:
        self._draft = text
        attached = set(self.app._chat_projects)
        explicit, _files = mentions.mentioned(text, self.app.projects.items)
        found = mentions.named(text, self.app.projects.items, attached | self._waved | set(explicit))
        if found != self._suggested:
            self._suggested = found
            self.suggestionsChanged.emit()

    @Slot()
    def attachSuggested(self) -> None:
        ids = [*self.app._chat_projects, *(p["id"] for p in self._suggested)]
        self.app._set_chat_projects(list(dict.fromkeys(ids)), push=self.app.current is not None)
        names = ", ".join(p["name"] for p in self._suggested)
        self.app.flash(f"Подключено к чату: {names}")

    @Slot()
    def waveAway(self) -> None:
        self._waved |= {p["id"] for p in self._suggested}
        self._suggest(self._draft)

    def _new_chat(self) -> None:
        self._waved = set()
        self._suggest(self._draft)
