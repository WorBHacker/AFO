"""The projects side of the window (stage 2): the project list, a lazily loaded file tree, a file or its diff, git.

Everything comes from the hub (D4, D17): the window never reads the project's folder itself. Folders load when
opened; git state is polled while the projects view is shown (file watching comes in a later step).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from ..hub_client import HubError
from . import project_rows
from .models import TREE_ROLES, RowModel

if TYPE_CHECKING:
    from .controller import Controller

GIT_REFRESH_MS = 10_000


class Projects(QObject):
    listChanged = Signal()
    projectChanged = Signal()
    gitChanged = Signal()
    fileChanged = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.items: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.tree = RowModel(self, roles=TREE_ROLES)
        self._git: dict[str, Any] = {}
        self._marks: dict[str, str] = {}
        self._file: dict[str, Any] = {}
        self._file_data: dict[str, Any] | None = None  # the hub's answer, kept to colour again on a theme switch
        self._diff_text = ""
        self._active = False
        self._git_timer = app._timer(GIT_REFRESH_MS, self.refresh_git)
        app.theme.changed.connect(self._restyle)

    # ================= properties for QML =================

    @Property(list, notify=listChanged)
    def projectList(self) -> list:
        cid = self.current["id"] if self.current else ""
        return [{**p, "selected": p["id"] == cid} for p in self.items]

    @Property(str, notify=projectChanged)
    def currentId(self) -> str:
        return self.current["id"] if self.current else ""

    @Property(str, notify=projectChanged)
    def currentName(self) -> str:
        return self.current["name"] if self.current else ""

    @Property(str, notify=projectChanged)
    def currentPath(self) -> str:
        return self.current["path"] if self.current else ""

    @Property(bool, notify=projectChanged)
    def currentMissing(self) -> bool:
        return bool(self.current) and not self.current.get("exists", True)

    @Property(QObject, constant=True)
    def treeModel(self) -> RowModel:
        return self.tree

    @Property("QVariantMap", notify=gitChanged)
    def git(self) -> dict:
        return self._git

    @Property("QVariantMap", notify=fileChanged)
    def file(self) -> dict:
        return self._file

    # ================= the list =================

    def load(self) -> None:
        """The project list from the hub; reopens the project open last time."""

        def show(found: list[dict[str, Any]]) -> None:
            self.items = found
            self.listChanged.emit()
            ids = {p["id"]: p for p in found}
            if self.current and self.current["id"] in ids:
                self.current = ids[self.current["id"]]
                self.projectChanged.emit()
            elif self.current:
                self._clear()
            if self.current is None:
                last = str(self.app.settings.value("projects/current") or "")
                if last in ids:
                    self.openProject(last)

        self.app._call(lambda: self.app.client.projects(), show)

    @Slot(str)
    def addProject(self, folder: str) -> None:
        """`folder` is a path or a file:// URL (what a folder dialog gives)."""
        path = QUrl(folder).toLocalFile() if folder.startswith("file:") else folder
        if not path:
            return

        def done(project: dict[str, Any]) -> None:
            self.app.flash(f"Проект «{project['name']}» добавлен")
            self.items = sorted([*self.items, project], key=lambda p: p["name"].casefold())
            self.listChanged.emit()
            self.openProject(project["id"])

        self.app._call(
            lambda: self.app.client.add_project(path),
            done,
            lambda error: self.app.show_error(f"Проект не добавлен: {error}"),
        )

    @Slot(str)
    def askRemoveProject(self, project_id: str) -> None:
        pid = project_id or self.currentId
        project = next((p for p in self.items if p["id"] == pid), None)
        if project is None:
            return

        def decide(yes: bool) -> None:
            if yes:
                self.app._call(lambda: self.app.client.remove_project(pid), lambda _r: self._forget(pid))

        self.app._ask(
            f"Убрать проект «{project['name']}» из списка?", "Папка и файлы останутся на месте.", "Убрать", decide
        )

    def _forget(self, project_id: str) -> None:
        self.items = [p for p in self.items if p["id"] != project_id]
        self.listChanged.emit()
        if self.current and self.current["id"] == project_id:
            self._clear()

    def _clear(self) -> None:
        self.current = None
        self.app.settings.setValue("projects/current", "")
        self.tree.reset([])
        self._git, self._marks = {}, {}
        self.closeFile()
        self.projectChanged.emit()
        self.listChanged.emit()
        self.gitChanged.emit()
        self._git_timer.stop()

    @Slot(str)
    def openProject(self, project_id: str) -> None:
        project = next((p for p in self.items if p["id"] == project_id), None)
        if project is None:
            return
        self.current = project
        self.app.settings.setValue("projects/current", project_id)
        self.tree.reset([])
        self._git, self._marks = {}, {}
        self.closeFile()
        self.projectChanged.emit()
        self.listChanged.emit()
        self.gitChanged.emit()
        self.refresh()
        self._poll()

    # ================= the tree =================

    @Slot()
    def refresh(self) -> None:
        """The tree again (the open folders stay open), git, and the open file."""
        if not self.current:
            return
        pid = self.current["id"]
        opened = [row["path"] for row in self.tree.rows if row["dir"] and row["open"]]

        def fetch() -> dict[str, list[dict[str, Any]]]:
            folders = {"": self.app.client.tree(pid, "")}
            for path in opened:
                try:
                    folders[path] = self.app.client.tree(pid, path)
                except HubError:  # the folder went away meanwhile
                    continue
            return folders

        def show(folders: dict[str, list[dict[str, Any]]]) -> None:
            if self.currentId == pid:
                self.tree.reset(self._assemble(folders, "", 0))

        self.app._call(fetch, show, self._tree_failed)
        self.refresh_git()
        if self._file:
            self.openFile(self._file["path"], self._file.get("diffMode", False))

    def _assemble(self, folders: dict[str, list[dict[str, Any]]], path: str, depth: int) -> list[dict[str, Any]]:
        out = []
        for row in project_rows.tree_rows(folders.get(path, []), depth, self._marks):
            row["open"] = row["dir"] and row["path"] in folders
            out.append(row)
            if row["open"]:
                out.extend(self._assemble(folders, row["path"], depth + 1))
        return out

    def _tree_failed(self, error: Exception) -> None:
        self.tree.reset([])
        if self.currentMissing:
            self.app.show_error(f"Папки проекта нет на месте: {self.currentPath}")
        else:
            self.app.show_error(f"Дерево проекта не загрузилось: {error}")

    @Slot(int)
    def toggleDir(self, index: int) -> None:
        if not 0 <= index < len(self.tree.rows) or not self.tree.rows[index]["dir"] or not self.current:
            return
        row = self.tree.rows[index]
        if row["open"]:
            end = index + 1
            while end < len(self.tree.rows) and self.tree.rows[end]["depth"] > row["depth"]:
                end += 1
            self.tree.remove(index + 1, end - index - 1)
            self.tree.update(index, open=False)
            return
        pid, path = self.current["id"], row["path"]

        def show(entries: list[dict[str, Any]]) -> None:
            at = self.tree.find(path)  # rows may have moved while the folder loaded
            if self.currentId != pid or at < 0 or self.tree.rows[at]["open"]:
                return
            self.tree.update(at, open=True)
            self.tree.insert(at + 1, project_rows.tree_rows(entries, row["depth"] + 1, self._marks))

        self.app._call(lambda: self.app.client.tree(pid, path), show)

    # ================= git =================

    def set_active(self, active: bool) -> None:
        """The projects view is shown (or not): git is polled only while it is."""
        self._active = active
        self._poll()
        if active and self.current:
            self.refresh_git()

    def _poll(self) -> None:
        if self._active and self.current:
            self._git_timer.start()
        else:
            self._git_timer.stop()

    def refresh_git(self) -> None:
        if not self.current or self.app.client is None:
            return
        pid = self.current["id"]

        def show(git: dict[str, Any]) -> None:
            if self.currentId != pid:
                return
            self._marks = project_rows.marks(git.get("changes", []))
            self._git = {
                **git,
                "summary": project_rows.git_summary(git),
                "changes": project_rows.change_rows(git.get("changes", [])),
            }
            self.gitChanged.emit()
            for index, row in enumerate(self.tree.rows):
                status = self._marks.get(row["path"], "")
                if row["status"] != status:
                    self.tree.update(index, status=status)
            if self._file:
                has_diff = self._file["path"] in self._marks and self._marks[self._file["path"]] != "•"
                if has_diff != self._file.get("hasDiff"):
                    self._file = {**self._file, "hasDiff": has_diff}
                    self.fileChanged.emit()

        self.app._call(lambda: self.app.client.git(pid), show, lambda _e: None)  # polled: stay quiet

    # ================= a file =================

    @Slot(str, bool)
    def openFile(self, path: str, diff: bool = False) -> None:
        if not self.current:
            return
        pid = self.current["id"]
        exists = self._marks.get(path) != "D"  # a deleted file has only its diff

        def fetch() -> tuple[dict[str, Any] | None, str]:
            data = self.app.client.file(pid, path) if exists else None
            text = self.app.client.diff(pid, path)["diff"] if diff else ""
            return data, text

        def show(found: tuple[dict[str, Any] | None, str]) -> None:
            if self.currentId != pid:
                return
            data, text = found
            self._file_data = data or {"path": path, "size": 0, "binary": False, "text": "", "missing": True}
            self._diff_text = text
            self._render_file(diff)

        self.app._call(fetch, show, lambda error: self.app.show_error(f"Файл не открылся: {error}"))

    def _render_file(self, diff: bool) -> None:
        data = self._file_data
        if data is None:
            return
        view = project_rows.file_view(data)
        mark = self._marks.get(data["path"], "")
        view["missing"] = bool(data.get("missing"))
        view["hasDiff"] = bool(mark) and mark != "•"
        view["mark"] = mark
        view["diffMode"] = diff
        if diff:
            shown = project_rows.diff_view(self._diff_text)
            view["diff"] = shown["code"]
            view["diffRich"] = shown["rich"]
            view["diffEmpty"] = not self._diff_text
        self._file = view
        self.fileChanged.emit()

    @Slot(bool)
    def showDiff(self, diff: bool) -> None:
        if self._file:
            self.openFile(self._file["path"], diff)

    @Slot()
    def closeFile(self) -> None:
        self._file, self._file_data, self._diff_text = {}, None, ""
        self.fileChanged.emit()

    def _restyle(self) -> None:
        if self._file_data is not None:
            self._render_file(bool(self._file.get("diffMode")))

    # ================= small commands =================

    @Slot(str)
    def openFolder(self, path: str) -> None:
        """Shows the project folder (or the folder of `path` in it) in Explorer."""
        if not self.current:
            return
        target = Path(self.current["path"]) / path if path else Path(self.current["path"])
        if not target.is_dir():
            target = target.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot(str)
    def copyPath(self, path: str) -> None:
        if self.current:
            full = Path(self.current["path"]) / path if path else Path(self.current["path"])
            QGuiApplication.clipboard().setText(str(full))
            self.app.flash("Путь скопирован", ms=2000)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "project.removed":
            self._forget(payload.get("id", ""))
        elif kind in ("project.added", "project.changed"):
            self.load()
