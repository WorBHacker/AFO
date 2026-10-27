"""The feed (stage 2, D29): what happened, from the hub's event log, newest first and live.

The panel loads the log when it opens and pages back on request; stored events that arrive while the window runs
are added at the top. The open project's last few events are kept apart for its overview ("Недавно").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import feed_rows

if TYPE_CHECKING:
    from .controller import Controller

PAGE = 150
RECENT = 6
KEPT = 1000  # live events kept at most; older ones come back with "show more"


class Feed(QObject):
    itemsChanged = Signal()
    recentChanged = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._events: list[dict[str, Any]] = []  # newest first
        self._more = False
        self._loaded = False
        self._group = "all"
        self._recent: list[dict[str, Any]] = []
        self._project = ""

    # ================= properties for QML =================

    def _names(self) -> tuple[dict[str, str], dict[str, str]]:
        projects = {p["id"]: p["name"] for p in self.app.projects.items}
        chats = {c["id"]: c.get("title") or "" for c in self.app.conversations}
        return projects, chats

    @Property(list, notify=itemsChanged)
    def items(self) -> list:
        projects, chats = self._names()
        return feed_rows.feed_rows(self._events, projects=projects, chats=chats, group=self._group)

    @Property(bool, notify=itemsChanged)
    def more(self) -> bool:
        return self._more

    @Property(str, notify=itemsChanged)
    def group(self) -> str:
        return self._group

    @Property(list, constant=True)
    def groups(self) -> list:
        return [{"id": key, "name": name} for key, name in feed_rows.GROUPS]

    @Property(list, notify=recentChanged)
    def recent(self) -> list:
        projects, chats = self._names()
        return feed_rows.feed_rows(self._recent, projects=projects, chats=chats, sections=False)

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        def show(answer: dict[str, Any]) -> None:
            self._events, self._more, self._loaded = answer["events"], answer["more"], True
            self.itemsChanged.emit()

        self.app._call(lambda: self.app.client.feed(limit=PAGE), show)

    @Slot()
    def loadMore(self) -> None:
        if not self._events:
            return
        before = self._events[-1]["id"]

        def show(answer: dict[str, Any]) -> None:
            known = {e["id"] for e in self._events}
            self._events += [e for e in answer["events"] if e["id"] not in known]
            self._more = answer["more"]
            self.itemsChanged.emit()

        self.app._call(lambda: self.app.client.feed(limit=PAGE, before=before), show)

    @Slot(str)
    def setGroup(self, group: str) -> None:
        if group != self._group and group in dict(feed_rows.GROUPS):
            self._group = group
            self.itemsChanged.emit()

    def set_project(self, project_id: str) -> None:
        self._project = project_id
        self._recent = []
        self.recentChanged.emit()
        if not project_id or self.app.client is None:
            return

        def show(answer: dict[str, Any]) -> None:
            if project_id == self._project:
                projects, chats = self._names()
                shown = [e for e in answer["events"] if feed_rows.describe(e, projects=projects, chats=chats)]
                self._recent = shown[:RECENT]
                self.recentChanged.emit()

        self.app._call(lambda: self.app.client.feed(limit=40, project_id=project_id), show, lambda _e: None)

    # ================= live =================

    def on_event(self, event: dict[str, Any]) -> None:
        """Every event from the hub passes here; the stored ones worth showing join the feed."""
        if event.get("id") is None:
            return
        projects, chats = self._names()
        if feed_rows.describe(event, projects=projects, chats=chats) is None:
            return
        if self._loaded and all(e["id"] != event["id"] for e in self._events[:20]):
            self._events.insert(0, event)
            if len(self._events) > KEPT:
                del self._events[KEPT:]
                self._more = True
            self.itemsChanged.emit()
        if self._project and event.get("project_id") == self._project:
            self._recent = [event, *[e for e in self._recent if e["id"] != event["id"]]][:RECENT]
            self.recentChanged.emit()
