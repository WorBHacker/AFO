"""The projects side of the window (stage 2): the project list, a lazily loaded file tree, a file or its diff, git.

Everything comes from the hub (D4, D17): the window never reads the project's folder itself. Folders load when
opened. The hub watches the folders (hub/watch.py) and says what changed: `project.files` updates the open folders
and the open file, `project.git` the marks and the overview, `processes.changed` what runs in each project. The
tree is updated row by row, so the list keeps its place. Answers that come back late (an older file, an older
tree) are dropped by a generation count.
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

SEARCH_DELAY_MS = 250  # typing pauses this long before the search goes out


class Projects(QObject):
    listChanged = Signal()
    projectChanged = Signal()
    gitChanged = Signal()
    fileChanged = Signal()
    processesChanged = Signal()
    searchChanged = Signal()

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
        self._processes: dict[str, list[dict[str, Any]]] = {}  # project id -> its processes, from the hub
        self._tree_gen = 0
        self._search: dict[str, Any] | None = None  # the hub's last answer
        self._search_args: tuple[str, bool, bool] = ("", False, False)
        self._search_gen = 0
        self._searching = False
        self._search_timer = app._timer(SEARCH_DELAY_MS, self._run_search, single=True)
        self._goto: tuple[str, int] | None = None  # a file to show at a line, from a search
        self._goto_seq = 0
        self._file_gen = 0
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

    @Property(str, notify=projectChanged)
    def currentDescription(self) -> str:
        return (self.current or {}).get("description", "")

    @Property(bool, notify=projectChanged)
    def currentCloud(self) -> bool:
        return bool((self.current or {}).get("cloud_ok"))

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

    @Property(list, notify=searchChanged)
    def searchRows(self) -> list:
        return project_rows.search_rows(self._search) if self._search else []

    @Property(str, notify=searchChanged)
    def searchSummary(self) -> str:
        return "поиск…" if self._searching else project_rows.search_summary(self._search)

    @Property(bool, notify=searchChanged)
    def searchActive(self) -> bool:
        return bool(self._search_args[0].strip())

    @Property(list, notify=processesChanged)
    def processes(self) -> list:
        return project_rows.process_rows(self._processes.get(self.currentId, []))

    @Property(str, notify=processesChanged)
    def processesSummary(self) -> str:
        return project_rows.processes_summary(self._processes.get(self.currentId, []))

    # ================= the list =================

    def load(self, *, catch_up: bool = True) -> None:
        """The project list from the hub; reopens the project open last time. `catch_up`: also read the open
        project again (after a break in the event stream its changes were missed)."""

        def show(found: list[dict[str, Any]]) -> None:
            self.items = found
            self.listChanged.emit()
            ids = {p["id"]: p for p in found}
            if self.current and self.current["id"] in ids:
                self.current = ids[self.current["id"]]
                self.projectChanged.emit()
                if catch_up:
                    self.refresh()
            elif self.current:
                self._clear()
            if self.current is None:
                last = str(self.app.settings.value("projects/current") or "")
                if last in ids:
                    self.openProject(last)

        self.app._call(lambda: self.app.client.projects(), show)
        self.app._call(lambda: self.app.client.processes(), self._set_processes, lambda _e: None)

    @Slot(str)
    def addProject(self, folder: str) -> None:
        """`folder` is a path or a file:// URL (what a folder dialog gives)."""
        path = QUrl(folder).toLocalFile() if folder.startswith("file:") else folder
        if not path:
            return

        def done(project: dict[str, Any]) -> None:
            self.app.flash(f"Проект «{project['name']}» добавлен")
            if all(p["id"] != project["id"] for p in self.items):  # the hub's event may have listed it already
                self.items = sorted([*self.items, project], key=lambda p: p["name"].casefold())
                self.listChanged.emit()
            self.openProject(project["id"])

        self.app._call(
            lambda: self.app.client.add_project(path),
            done,
            lambda error: self.app.show_error(f"Проект не добавлен: {error}"),
        )

    @Slot(str, str, bool)
    def saveSettings(self, name: str, description: str, cloud_ok: bool) -> None:
        """The open project's name, its one-line description (the ecosystem map shows it to models) and whether
        cloud models may see it (D10)."""
        if not self.current:
            return
        pid = self.current["id"]
        changes = {"name": name.strip() or self.current["name"], "description": " ".join(description.split()),
                   "cloud_ok": cloud_ok}  # fmt: skip

        def done(project: dict[str, Any]) -> None:
            self.items = sorted(
                [project if p["id"] == pid else p for p in self.items], key=lambda p: p["name"].casefold()
            )
            if self.currentId == pid:
                self.current = project
                self.projectChanged.emit()
            self.listChanged.emit()
            self.app.flash("Настройки проекта сохранены")

        self.app._call(lambda: self.app.client.update_project(pid, **changes), done)

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
        self._reset_view()

    def _reset_view(self) -> None:
        self._tree_gen += 1
        self._search_gen += 1
        self._search, self._searching, self._search_args = None, False, ("", False, False)
        self.searchChanged.emit()
        self.tree.reset([])
        self._git, self._marks = {}, {}
        self.closeFile()
        self.projectChanged.emit()
        self.listChanged.emit()
        self.gitChanged.emit()
        self.processesChanged.emit()

    @Slot(str)
    def openProject(self, project_id: str) -> None:
        project = next((p for p in self.items if p["id"] == project_id), None)
        if project is None:
            return
        self.current = project
        self.app.settings.setValue("projects/current", project_id)
        self._reset_view()
        self.refresh()

    # ================= the tree =================

    @Slot()
    def refresh(self) -> None:
        """The tree again (the open folders stay open), git, and the open file."""
        if not self.current:
            return
        self.reload_tree()
        self.refresh_git()
        if self._file:
            self.openFile(self._file["path"], bool(self._file.get("diffMode")))

    def reload_tree(self) -> None:
        pid = self.current["id"]
        opened = [row["path"] for row in self.tree.rows if row["dir"] and row["open"]]
        self._tree_gen += 1
        generation = self._tree_gen

        def fetch() -> dict[str, list[dict[str, Any]]]:
            folders = {"": self.app.client.tree(pid, "")}
            for path in opened:
                try:
                    folders[path] = self.app.client.tree(pid, path)
                except HubError:  # the folder went away meanwhile
                    continue
            return folders

        def show(folders: dict[str, list[dict[str, Any]]]) -> None:
            if generation == self._tree_gen:
                self.tree.sync(self._assemble(folders, "", 0))

        self.app._call(fetch, show, self._tree_failed)

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
        pid, path, generation = self.current["id"], row["path"], self._tree_gen

        def show(entries: list[dict[str, Any]]) -> None:
            at = self.tree.find(path)  # rows may have moved while the folder loaded
            if generation != self._tree_gen or at < 0 or self.tree.rows[at]["open"]:
                return
            self.tree.update(at, open=True)
            self.tree.insert(at + 1, project_rows.tree_rows(entries, row["depth"] + 1, self._marks))

        self.app._call(lambda: self.app.client.tree(pid, path), show)

    # ================= git =================

    def refresh_git(self) -> None:
        if not self.current or self.app.client is None:
            return
        pid = self.current["id"]

        def show(git: dict[str, Any]) -> None:
            if self.currentId == pid:
                self._apply_git(git)

        self.app._call(lambda: self.app.client.git(pid), show, lambda _e: None)  # the next change tries again

    def _apply_git(self, git: dict[str, Any]) -> None:
        before = self._marks
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
        path = self._file.get("path")
        if path and before.get(path) != self._marks.get(path):
            if self._file.get("diffMode"):  # e.g. staged: the diff against the index is another one now
                self.openFile(path, True)
            else:
                mark = self._marks.get(path, "")
                self._file = {**self._file, "mark": mark, "hasDiff": bool(mark) and mark != "•"}
                self.fileChanged.emit()

    # ================= a file =================

    @Slot(str, bool)
    def openFile(self, path: str, diff: bool = False) -> None:
        if self._goto is not None and self._goto[0] != path:
            self._goto = None  # a file opened from the tree starts at its top
        self._load_file(path, diff)

    def _load_file(self, path: str, diff: bool, *, reloading: bool = False) -> None:
        """`reloading`: the open file changed on disk; if it is gone now, it shows as deleted, with no error."""
        if not self.current:
            return
        pid = self.current["id"]
        exists = self._marks.get(path) != "D"  # a deleted file has only its diff
        self._file_gen += 1
        generation = self._file_gen

        def fetch() -> tuple[dict[str, Any] | None, str]:
            data = self.app.client.file(pid, path) if exists else None
            text = self.app.client.diff(pid, path)["diff"] if diff else ""
            return data, text

        def show(found: tuple[dict[str, Any] | None, str]) -> None:
            if generation != self._file_gen or self.currentId != pid:
                return
            data, text = found
            self._file_data = data or {"path": path, "size": 0, "binary": False, "text": "", "missing": True}
            self._diff_text = text
            self._render_file(diff)

        def failed(error: Exception) -> None:
            if generation != self._file_gen:
                return
            if reloading:
                self._file_data = {"path": path, "size": 0, "binary": False, "text": "", "missing": True}
                self._diff_text = ""
                self._render_file(False)
            else:
                self.app.show_error(f"Файл не открылся: {error}")

        self.app._call(fetch, show, failed)

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
        view.update(self._goto_fields(data) if not diff else {"gotoLine": 0, "gotoOffset": -1, "gotoSeq": 0})
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
        self._file_gen += 1
        self._file, self._file_data, self._diff_text = {}, None, ""
        self.fileChanged.emit()

    def _restyle(self) -> None:
        if self._file_data is not None:
            self._render_file(bool(self._file.get("diffMode")))
        if self._search:
            self.searchChanged.emit()  # the marks carry colours

    # ================= what the hub says =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "project.removed":
            self._forget(payload.get("id", ""))
        elif kind in ("project.added", "project.changed"):
            self.load(catch_up=False)
        elif kind == "processes.changed":
            self._set_processes(payload.get("projects") or {})
        elif payload.get("project_id") != self.currentId or not self.current:
            return
        elif kind == "project.files":
            self._files_changed(payload)
        elif kind == "project.git":
            self._apply_git(payload)

    def _files_changed(self, payload: dict[str, Any]) -> None:
        more = bool(payload.get("more"))
        opened = {""} | {row["path"] for row in self.tree.rows if row["dir"] and row["open"]}
        if more or opened & set(payload.get("folders", [])):
            self.reload_tree()
        path = self._file.get("path")
        if path and (more or path in payload.get("changed", [])):
            self._load_file(path, bool(self._file.get("diffMode")), reloading=True)

    # ================= search =================

    @Slot(str, bool, bool)
    def search(self, text: str, regex: bool, case_sensitive: bool) -> None:
        """Searches the open project once typing pauses; an empty text goes back to the tree."""
        self._search_args = (text, regex, case_sensitive)
        self._search_gen += 1
        if not text.strip():
            self._search_timer.stop()
            self._search, self._searching = None, False
        else:
            self._searching = True
            self._search_timer.start()
        self.searchChanged.emit()

    def _run_search(self) -> None:
        text, regex, case_sensitive = self._search_args
        if not self.current or not text.strip():
            return
        pid, generation = self.current["id"], self._search_gen
        case = "sensitive" if case_sensitive else "smart"

        def show(result: dict[str, Any]) -> None:
            if generation == self._search_gen:
                self._search, self._searching = result, False
                self.searchChanged.emit()

        def failed(error: Exception) -> None:
            if generation == self._search_gen:
                self._search, self._searching = {"files": [], "matches": 0, "error": str(error)}, False
                self.searchChanged.emit()
                self.app.show_error(f"Поиск не удался: {error}")

        self.app._call(lambda: self.app.client.search(pid, text, regex=regex, case=case), show, failed)

    @Slot()
    def clearSearch(self) -> None:
        self.search("", False, False)

    @Slot(str, int)
    def openFileAt(self, path: str, line: int) -> None:
        """Opens the file (or keeps it open) and shows the line."""
        self._goto_seq += 1
        self._goto = (path, line)
        if self._file.get("path") == path and not self._file.get("diffMode") and self._file_data is not None:
            self._file = {**self._file, **self._goto_fields(self._file_data)}
            self.fileChanged.emit()
        else:
            self._load_file(path, False)

    def _goto_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        if self._goto is None or self._goto[0] != data["path"]:
            return {"gotoLine": 0, "gotoOffset": -1, "gotoSeq": 0}
        line = self._goto[1]
        text = data.get("text", "").replace("\r\n", "\n")
        return {"gotoLine": line, "gotoOffset": project_rows.line_offset(text, line), "gotoSeq": self._goto_seq}

    # ================= processes =================

    def _set_processes(self, found: dict[str, list[dict[str, Any]]]) -> None:
        before = self._processes.get(self.currentId)
        self._processes = found
        if found.get(self.currentId) != before:
            self.processesChanged.emit()

    @Slot(int)
    def askStopProcess(self, pid: int) -> None:
        process = next((p for p in self._processes.get(self.currentId, []) if p["pid"] == pid), None)
        if process is None or process.get("hub"):
            return

        def decide(yes: bool) -> None:
            if not yes:
                return

            def done(result: dict[str, Any]) -> None:
                if result.get("stopped"):
                    self.app.flash(f"Процесс {process['name']} завершён")
                else:
                    self.app.show_error(f"Процесс {process['name']} не завершился")

            self.app._call(lambda: self.app.client.stop_process(pid), done)

        self.app._ask(
            f"Завершить {process['name']} (PID {pid})?",
            "Вместе с его дочерними процессами. Что в нём не сохранено, пропадёт.",
            "Завершить",
            decide,
        )

    @Slot(int)
    def openPort(self, port: int) -> None:
        QDesktopServices.openUrl(QUrl(f"http://localhost:{port}/"))

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
