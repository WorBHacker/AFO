"""The folder of a Claude Code chat with no project (31.7; the user's choice 2026-11-08: «показывать его папку в окне»).

Claude Code in such a chat works in a folder of the chat's own, in the hub's data (hub/claude_code.py); what it made
there the window never showed. A bar over the composer says how many files are there and the newest by name, with
«Открыть папку» and «Копировать путь». It is read when another chat opens and when an answer ends.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from common.i18n import plural, tr

if TYPE_CHECKING:
    from .controller import Controller

NAMED = 3  # the newest files named in the bar


def summary(listing: dict[str, Any]) -> str:
    """«Папка чата · 3 файла: index.html, style.css, README.md» - the newest first."""
    count = listing.get("count") or 0
    names = [Path(f["path"]).name for f in (listing.get("files") or [])[:NAMED]]
    more = "…" if count > len(names) else ""
    return tr("Папка чата · {n} {files}: {names}", n=count, files=plural(count, "файл", "файла", "файлов"),
              names=", ".join(names) + more)  # fmt: skip


class ChatFolder(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._conversation = ""
        self._listing: dict[str, Any] = {}

    @Property(bool, notify=changed)
    def shown(self) -> bool:
        return self._conversation == (self.app.current or "") and bool(self._listing.get("count"))

    @Property(str, notify=changed)
    def summary(self) -> str:
        return summary(self._listing) if self._listing.get("count") else ""

    @Property(str, notify=changed)
    def path(self) -> str:
        return self._listing.get("path") or ""

    @Slot()
    def load(self) -> None:
        cid = self.app.current or ""
        if cid != self._conversation:
            self._conversation, self._listing = cid, {}
            self.changed.emit()
        if self.app.client is None or not cid:
            return

        def show(listing: dict[str, Any]) -> None:
            if cid == (self.app.current or ""):
                self._listing = listing
                self.changed.emit()

        self.app._call(lambda: self.app.client.chat_folder(cid), show, lambda _e: None)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        """An answer of the open chat ended: Claude Code may have made files."""
        if kind in ("chat.done", "chat.error") and payload.get("conversation_id") == (self.app.current or ""):
            self.load()

    @Slot()
    def openFolder(self) -> None:
        if self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    @Slot()
    def copyPath(self) -> None:
        if self.path:
            self.app._clipboard().setText(self.path)
            self.app.flash(tr("Путь скопирован"), ms=2000)
