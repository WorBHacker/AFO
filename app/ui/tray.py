"""The tray's menu (43.3, the user's word 2026-11-13): AFO lives in the notification area - the window's cross hides
it there - and its menu does what the window would without opening it first: a new chat, a recent project, the
loaded local model unloaded (the card free for a game), the quiet hours on or off, the settings, «Выход» (everything
stops). Filled anew each time it opens: the projects, the model, the quiet hours are as they are now.

Its items are QActions (QtGui: no widgets); the QMenu that shows them is made only where QtWidgets runs - the tray
needs it, the window's tests run on a QGuiApplication and use the actions themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtGui import QAction

from common.i18n import tr

RECENT = 8  # projects in «Проекты»


def recent_projects(projects: list[dict[str, Any]], conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The projects whose chats were busy last first (by their newest chat), then the others as the list has them."""
    last: dict[str, str] = {}
    for c in conversations:
        home = c.get("home_project") or ""
        if home and (c.get("updated_at") or "") > last.get(home, ""):
            last[home] = c.get("updated_at") or ""
    busy = sorted((p for p in projects if p["id"] in last), key=lambda p: last[p["id"]], reverse=True)
    return [*busy, *(p for p in projects if p["id"] not in last)][:RECENT]


def _widgets() -> bool:
    from PySide6.QtWidgets import QApplication

    return isinstance(QCoreApplication.instance(), QApplication)


class TrayMenu(QObject):
    """The menu's items, in order (`items`; None - a separator; "projects" - the recent projects' submenu); `show`
    brings the window forward, `quit_all` is «Выход»."""

    def __init__(self, controller, notifier, show: Callable[[], None], quit_all: Callable[[], None]):
        super().__init__(controller)
        self.app = controller
        self.notifier = notifier
        self._show = show
        self.open = self._action(tr("Открыть AFO"), "trayOpen", show)
        self.new_chat = self._action(tr("Новый чат"), "trayNewChat", self._new_chat)
        self.model = self._action("", "trayModel", self._unload)
        self.quiet = self._action(tr("Тихие часы"), "trayQuiet", self._toggle_quiet)
        self.quiet.setCheckable(True)
        self.stop = self._action(tr("Остановить агента (управляет компьютером)"), "trayStop", notifier.stop_agent_now)
        self.settings = self._action(tr("Настройки"), "traySettings", self._settings)
        self.quit = self._action(tr("Выход"), "trayQuit", quit_all)
        self.projects: list[QAction] = []  # the recent ones, made anew at each refresh
        self.items: list[QAction | str | None] = [self.open, self.new_chat, "projects", None, self.model, self.quiet,
                                                  self.stop, None, self.settings, self.quit]  # fmt: skip
        self.menu = self.submenu = None
        if _widgets():
            self._make_menu()
        self.refresh()

    def _action(self, text: str, name: str, slot: Callable[[], None]) -> QAction:
        action = QAction(text, self)
        action.setObjectName(name)
        action.triggered.connect(lambda _checked=False: slot())
        return action

    def _make_menu(self) -> None:
        from PySide6.QtWidgets import QMenu

        self.menu = QMenu()
        self.menu.setObjectName("trayMenu")
        for item in self.items:
            if item is None:
                self.menu.addSeparator()
            elif item == "projects":
                self.submenu = self.menu.addMenu(tr("Проекты"))
            else:
                self.menu.addAction(item)
        self.menu.aboutToShow.connect(self.refresh)
        self.notifier.use_menu(self.menu)

    def shown(self) -> list[str]:
        """The visible items' names, in order (the projects' submenu: "trayProjects", when it has any)."""
        out = []
        for item in self.items:
            if item == "projects":
                if self.projects:
                    out.append("trayProjects")
            elif item is not None and item.isVisible():
                out.append(item.objectName())
        return out

    def refresh(self) -> None:
        """As things are now: the recent projects, the loaded model, the quiet hours, the agent at the desktop."""
        for action in self.projects:
            action.deleteLater()
        self.projects = []
        for project in recent_projects(self.app.projects.projectList, self.app.conversations):
            action = self._action(project.get("name") or project["id"], "trayProject",
                                  lambda pid=project["id"]: self._open_project(pid))  # fmt: skip
            action.setData(project["id"])
            self.projects.append(action)
        if self.submenu is not None:
            self.submenu.clear()
            self.submenu.addActions(self.projects)
            self.submenu.setEnabled(bool(self.projects))
        loaded = list((self.app.status or {}).get("loaded") or [])
        self.model.setText(tr("Выгрузить модель: {name}", name=", ".join(loaded)) if loaded else
                           tr("Модель не загружена"))  # fmt: skip
        self.model.setEnabled(bool(loaded))
        quiet = self.app.schedules_view.quiet
        self.quiet.setChecked(bool(quiet.get("enabled")))
        self.quiet.setText(tr("Тихие часы ({start}–{end})", start=quiet.get("from", ""), end=quiet.get("to", "")))
        self.stop.setVisible(self.notifier.stop_shown)

    def _new_chat(self) -> None:
        self.app.setMode("chats")
        self.app.newChat()
        self._show()

    def _open_project(self, project_id: str) -> None:
        self.app.setMode("projects")
        self.app.projects.openProject(project_id)
        self._show()

    def _unload(self) -> None:
        self.app.unloadLocal()  # the sidebar asks first; here the person chose it in the menu itself

    def _toggle_quiet(self) -> None:
        quiet = self.app.schedules_view.quiet
        self.app.schedules_view.setQuiet(not quiet.get("enabled"), quiet.get("from", ""), quiet.get("to", ""))

    def _settings(self) -> None:
        self._show()
        self.app.uiRequested.emit("settings")
