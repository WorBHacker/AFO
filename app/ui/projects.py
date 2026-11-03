"""The projects side of the window (stage 2): the project list, a lazily loaded file tree, a file or its diff, git.

Everything comes from the hub (D4, D17): the window never reads the project's folder itself. Folders load when
opened. The hub watches the folders (hub/watch.py) and says what changed: `project.files` updates the open folders
and the open file, `project.git` the marks and the overview, `processes.changed` what runs in each project. The
tree is updated row by row, so the list keeps its place. Answers that come back late (an older file, an older
tree) are dropped by a generation count.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .. import palette, vscode
from ..hub_client import HubError
from . import editor, links_text, project_rows, rows
from .models import TREE_ROLES, RowModel

if TYPE_CHECKING:
    from .controller import Controller

SEARCH_DELAY_MS = 250  # typing pauses this long before the search goes out


class Projects(QObject):
    listChanged = Signal()
    projectChanged = Signal()
    gitChanged = Signal()
    fileChanged = Signal()
    fileShownChanged = Signal()
    processesChanged = Signal()
    cardChanged = Signal()
    exportChanged = Signal()
    notesChanged = Signal()
    changesChanged = Signal()
    linksChanged = Signal()
    searchChanged = Signal()
    outlineChanged = Signal()

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
        self._shown: dict[str, Any] = {}  # what the viewer shows: see fileShown
        self._diff_text = ""
        self._processes: dict[str, list[dict[str, Any]]] = {}  # project id -> its processes, from the hub
        self._tree_gen = 0
        self._search: dict[str, Any] | None = None  # the hub's last answer
        self._search_args: tuple[str, bool, bool] = ("", False, False)
        self._search_gen = 0
        self._searching = False
        self._symbol_mode = False  # search the symbol index (definitions) instead of the text
        self._outline: dict[str, Any] = {"path": "", "rows": [], "state": ""}  # state: loading / ready / failed
        self._search_timer = app._timer(SEARCH_DELAY_MS, self._run_search, single=True)
        self._goto: tuple[str, int] | None = None  # a file to show at a line, from a search
        self._goto_seq = 0
        self._card: dict[str, Any] = {}  # the open project's card, as the hub describes it
        self._export: list[dict[str, Any]] = []  # what writing the card into each file would do (D23)
        self._card_error = ""
        self._notes: list[dict[str, Any]] = []
        self._changes: list[dict[str, Any]] = []  # what models wrote in the open project (D17)
        self._rules: list[dict[str, Any]] = []
        self._links: list[dict[str, Any]] = []  # every link, as the hub lists them
        self._scanning = False
        self._memos: dict[str, tuple[tuple, Any]] = {}  # computed properties, built once per change (_memo)
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

    @Property("QVariantMap", notify=fileShownChanged)
    def fileShown(self) -> dict:
        """The open file (or its diff) for the viewer, in pieces: {chunks, rich, columns, digits, key}. Apart from
        `file`, which changes more often (its git mark, a line to show) and so does not carry the text each time."""
        return self._shown

    @Property(str, notify=fileChanged)
    def openPath(self) -> str:
        """For the tree's rows: `file` carries the whole text and its HTML, too much to hand to every row."""
        return self._file.get("path", "")

    @Property(list, notify=searchChanged)
    def searchRows(self) -> list:
        return project_rows.search_rows(self._search) if self._search else []

    @Property(str, notify=searchChanged)
    def searchSummary(self) -> str:
        return "поиск…" if self._searching else project_rows.search_summary(self._search)

    @Property(bool, notify=searchChanged)
    def searchActive(self) -> bool:
        return bool(self._search_args[0].strip())

    @Property(bool, notify=searchChanged)
    def symbolSearch(self) -> bool:
        return self._symbol_mode

    @Property("QVariantMap", notify=outlineChanged)
    def outline(self) -> dict:
        """The open file's definitions: {rows, state}; empty for another file than the one loaded."""
        if self._outline["path"] != self._file.get("path"):
            return {"rows": [], "state": ""}
        return {"rows": self._outline["rows"], "state": self._outline["state"]}

    @Property("QVariantMap", notify=cardChanged)
    def card(self) -> dict:
        """The card for the overview: state (none / drafting / draft / confirmed), texts, version, outdated paths.
        QML reads it once per binding (the overview and the card sheet: about twenty), so it is built once."""
        inputs = (self._card, self._card_error, self.app.model_list, self.app.modelId, palette.current().name)
        return self._memo("card", inputs, self._card_view)

    @Property("QVariantMap", notify=linksChanged)
    def links(self) -> dict:
        inputs = (self._links, self.currentId, self._scanning)
        return self._memo("links", inputs, lambda: {**links_text.project_links(*inputs[:2]), "scanning": inputs[2]})

    def _memo(self, name: str, inputs: tuple, build: Callable[[], Any]) -> Any:
        """The value built last time, while its inputs are the same objects (or equal ones)."""
        cached = self._memos.get(name)
        if cached is not None and all(a is b or a == b for a, b in zip(cached[0], inputs, strict=True)):
            return cached[1]
        value = build()
        self._memos[name] = (inputs, value)
        return value

    def _card_view(self) -> dict[str, Any]:
        c = self._card
        confirmed, draft = c.get("confirmed"), c.get("draft")
        state = "drafting" if c.get("drafting") else "draft" if draft else "confirmed" if confirmed else "none"
        models = {m["id"]: m["name"] for m in self.app.model_list}
        return {
            "state": state,
            "confirmed": bool(confirmed),
            "text": confirmed["text"] if confirmed else "",
            "segments": rows.segments(confirmed["text"]) if confirmed else [],
            "version": confirmed["version"] if confirmed else 0,
            "versions": [{**v, "when": project_rows.local_time(v["created_at"])} for v in c.get("versions", [])],
            "draft": draft["text"] if draft else "",
            "draftModel": models.get(draft["model_id"], draft["model_id"] or "") if draft else "",
            "missing": c.get("missing", []),
            "changes": project_rows.card_changes(c.get("changes")),  # D24: worth an update?
            "changedSections": project_rows.changed_sections(confirmed["text"], draft["text"])
            if confirmed and draft
            else [],
            "error": self._card_error,
            "writer": models.get(self._card_writer(), ""),
        }

    @Property(list, notify=notesChanged)
    def notes(self) -> list:
        return [{"id": n["id"], "text": n["text"]} for n in self._notes]

    @Property(list, notify=changesChanged)
    def fileChanges(self) -> list:
        """What models wrote in the open project, newest first (D17): each can be undone."""
        return project_rows.write_rows(self._changes)

    @Property(list, notify=changesChanged)
    def rules(self) -> list:
        """The project's answers "always": what models may do here without asking."""
        return [{"id": r["id"], "text": project_rows.rule_text(r["grant_key"])} for r in self._rules]

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
        self._card, self._card_error = {}, ""
        self.cardChanged.emit()
        self._notes = []
        self.notesChanged.emit()
        self._changes, self._rules = [], []
        self.changesChanged.emit()
        self._tree_gen += 1
        self._search_gen += 1
        self._search, self._searching, self._search_args = None, False, ("", False, False)
        self.searchChanged.emit()
        self.tree.reset([])
        self._git, self._marks = {}, {}
        self.closeView()
        self.projectChanged.emit()
        self.listChanged.emit()
        self.gitChanged.emit()
        self.processesChanged.emit()

    @Slot(str)
    def openProject(self, project_id: str) -> None:
        project = next((p for p in self.items if p["id"] == project_id), None)
        if project is None:
            return
        if project_id != self.currentId and not self.app.editor.leave(lambda: self.openProject(project_id)):
            return  # the tabs of the project shown before: unsaved edits asked about first (D47)
        self.current = project
        self.app.settings.setValue("projects/current", project_id)
        self._reset_view()
        self.refresh()
        self.app.editor.restore(project_id)  # tabs a window that went without closing them left as drafts (14.4)

    # ================= the tree =================

    @Slot()
    def refresh(self) -> None:
        """The tree again (the open folders stay open), git, and the open file."""
        if not self.current:
            return
        self.reload_tree()
        self.refresh_git()
        self.load_card()
        self.load_notes()
        self.load_links()
        self.load_changes()
        if self._file:  # read again where it is shown: a session in front stays in front
            self._load_file(self._file["path"], bool(self._file.get("diffMode")))

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
            "log": [{**c, "when": project_rows.ago(c.get("when", ""))} for c in git.get("log", [])],
        }
        self.gitChanged.emit()
        for index, row in enumerate(self.tree.rows):
            status = self._marks.get(row["path"], "")
            if row["status"] != status:
                self.tree.update(index, status=status)
        path = self._file.get("path")
        if path and before.get(path) != self._marks.get(path):
            if self._file.get("diffMode"):  # e.g. staged: the diff against the index is another one now
                self._load_file(path, True)
            else:
                mark = self._marks.get(path, "")
                self._file = {**self._file, "mark": mark, "hasDiff": bool(mark) and mark != "•"}
                self.fileChanged.emit()

    # ================= a file =================

    @Slot()
    def copyFile(self) -> None:
        """The whole open file to the clipboard (a selection with the mouse stays within one piece of the viewer)."""
        if self._file_data is not None and not self._file_data.get("binary"):
            self.app.copy(self._file_data.get("text", ""))

    @Slot(str, bool)
    def openFile(self, path: str, diff: bool = False) -> None:
        self.app.editor.show(self.currentId, path)  # its tab, when it has one (14.4): the others keep their edits
        self.app.show_project_view()  # over a session of the project, the file shows (2026-10-29)
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

        def fetch() -> tuple[dict[str, Any], str, tuple[str, dict[str, Any], dict[str, Any] | None]]:
            data = self.app.client.file(pid, path) if exists else None
            data = data or {"path": path, "size": 0, "binary": False, "text": "", "missing": True}
            text = self.app.client.diff(pid, path)["diff"] if diff else ""
            # coloured here, off the window's thread (~1 s per MB); again there only if the theme changes meanwhile
            theme = palette.current().name
            return data, text, (theme, project_rows.file_view(data), project_rows.diff_view(text) if diff else None)

        def show(found: tuple[dict[str, Any], str, tuple[str, dict[str, Any], dict[str, Any] | None]]) -> None:
            if generation != self._file_gen or self.currentId != pid:
                return
            self._file_data, self._diff_text, (theme, view, shown) = found
            self._render_file(diff, (view, shown) if theme == palette.current().name else None)

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

    def _render_file(self, diff: bool, ready: tuple[dict[str, Any], dict[str, Any] | None] | None = None) -> None:
        """`ready`: the file view and the diff view, coloured already in the current theme."""
        data = self._file_data
        if data is None:
            return
        view, shown = ready or (project_rows.file_view(data), project_rows.diff_view(self._diff_text) if diff else None)
        mark = self._marks.get(data["path"], "")
        view["missing"] = bool(data.get("missing"))
        view["editBlock"] = editor.why_not(data)  # "" - the editor may take it (D47)
        view["hasDiff"] = bool(mark) and mark != "•"
        view["mark"] = mark
        view["diffMode"] = diff
        view.update(self._goto_fields(data) if not diff else {"gotoLine": 0, "gotoOffset": -1, "gotoSeq": 0})
        chunks = view.pop("chunks", [])
        body = {"chunks": chunks, "rich": view.get("rich", False), "columns": view.get("columns", 0),
                "digits": view.get("digits", 1)}  # fmt: skip
        if diff and shown is not None:
            body = {"chunks": shown["chunks"], "rich": shown["rich"], "columns": shown["columns"], "digits": 0}
            view["diffEmpty"] = not self._diff_text
        self._file = view
        self._shown = {**body, "key": data["path"] + (":diff" if diff else "")}
        self.fileChanged.emit()
        self.fileShownChanged.emit()

    @Slot(int)
    def openInCode(self, line: int) -> None:
        """«Открыть в VS Code»: the shown file at `line` (the editor's cursor, or the top)."""
        path = self._file.get("path", "")
        if not path or not self.current:
            return
        if not vscode.open_at(Path(self.current["path"]) / path, line):
            self.app.show_error("VS Code не найден: нет ни `code` в PATH, ни VS Code в обычных папках установки")

    @Slot(bool)
    def showDiff(self, diff: bool) -> None:
        if self._file:
            self.openFile(self._file["path"], diff)

    @Slot()
    def closeFile(self) -> None:
        """«Закрыть файл»: its tab too (with unsaved edits, asked first); the tab beside it shows then, if any."""
        path = self._file.get("path", "")
        if path and self.app.editor.has(self.currentId, path):
            self.closeTab(f"{self.currentId}:{path}")
        else:
            self.closeView()

    @Slot(str)
    def closeTab(self, key: str) -> None:
        """A tab's ×. When it is the file shown, the tab beside it shows then (or the project's overview)."""
        path = key.split(":", 1)[1] if ":" in key else ""

        def closed(beside: str) -> None:
            if self._file.get("path") != path:
                return
            if beside:
                self.openFile(beside, False)
            else:
                self.closeView()

        self.app.editor.close(key, closed)

    @Slot()
    def closeView(self) -> None:
        """Back to the project's overview; the tabs stay."""
        self._file_gen += 1
        self._file, self._file_data, self._diff_text, self._shown = {}, None, "", {}
        self.fileChanged.emit()
        self.fileShownChanged.emit()

    def _restyle(self) -> None:
        if self._file_data is not None:
            self._render_file(bool(self._file.get("diffMode")))
        if self._search:
            self.searchChanged.emit()  # the marks carry colours
        if self._card:
            self.cardChanged.emit()  # so do the card's code blocks

    # ================= what the hub says =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "project.removed":
            self._forget(payload.get("id", ""))
        elif kind in ("project.added", "project.changed", "project.moved", "project.missing", "project.found"):
            self.load(catch_up=False)
        elif kind == "processes.changed":
            self._set_processes(payload.get("projects") or {})
        elif kind in ("links.changed", "link.confirmed"):
            self.load_links()
        elif kind == "note.changed":
            if payload.get("project_id") == self.currentId:
                self.load_notes()
        elif kind.startswith(("file.", "rules.")):
            if payload.get("project_id") == self.currentId:
                self.load_changes()
        elif kind.startswith("card."):
            if payload.get("project_id") == self.currentId:
                if kind == "card.failed":
                    self._card_error = payload.get("detail", "")
                    self.app.show_error(f"Черновик карточки не составлен: {self._card_error}")
                self.load_card()
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
        self.app.editor.files_changed(self.currentId, payload.get("changed", []), more)
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

    @Slot(bool)
    def setSymbolSearch(self, on: bool) -> None:
        """Definitions only (the symbol index) or the text of the files; the current query runs again."""
        self._symbol_mode = on
        self.search(*self._search_args)

    def _run_search(self) -> None:
        text, regex, case_sensitive = self._search_args
        if not self.current or not text.strip():
            return
        pid, generation = self.current["id"], self._search_gen
        case = "sensitive" if case_sensitive else "smart"
        symbols = self._symbol_mode

        def show(result: dict[str, Any]) -> None:
            if generation == self._search_gen:
                self._search, self._searching = result, False
                self.searchChanged.emit()

        def failed(error: Exception) -> None:
            if generation == self._search_gen:
                self._search, self._searching = {"files": [], "matches": 0, "error": str(error)}, False
                self.searchChanged.emit()
                self.app.show_error(f"Поиск не удался: {error}")

        if symbols:
            self.app.call_or_fail(lambda: self.app.client.symbols(pid, q=text), show, failed)
        else:
            self.app.call_or_fail(lambda: self.app.client.search(pid, text, regex=regex, case=case), show, failed)

    @Slot()
    def loadOutline(self) -> None:
        """The open file's definitions, from the symbol index (asked anew each time: the file may have changed)."""
        path = self._file.get("path")
        if not self.current or not path:
            return
        pid = self.current["id"]
        self._outline = {"path": path, "rows": self._outline["rows"] if self._outline["path"] == path else [],
                         "state": "loading"}  # fmt: skip
        self.outlineChanged.emit()

        def show(result: dict[str, Any]) -> None:
            if self._outline["path"] == path:
                self._outline = {"path": path, "rows": project_rows.outline_rows(result["symbols"]), "state": "ready"}
                self.outlineChanged.emit()

        def failed(_error: Exception) -> None:
            if self._outline["path"] == path:
                self._outline = {"path": path, "rows": [], "state": "failed"}
                self.outlineChanged.emit()

        self.app._call(lambda: self.app.client.symbols(pid, path=path), show, failed)

    @Slot()
    def clearSearch(self) -> None:
        self.search("", False, False)

    @Slot(str, int)
    def openFileAt(self, path: str, line: int) -> None:
        """Opens the file (or keeps it open) and shows the line."""
        self.app.editor.show(self.currentId, path)
        self.app.show_project_view()
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

    # ================= the card (D24) =================

    @Property(list, notify=exportChanged)
    def exportTargets(self) -> list:
        """The files the card can go to, each with what writing it would do - for the menu."""
        return [{"id": t["target"], "name": t["path"], "note": project_rows.EXPORT_MODES.get(t["mode"], t["mode"]),
                 "icon": "file", "enabled": t["mode"] != "unchanged"} for t in self._export]  # fmt: skip

    @Slot()
    def loadExport(self) -> None:
        if not self.current:
            return
        pid = self.current["id"]

        def show(answer: dict[str, Any]) -> None:
            if self.current and self.current["id"] == pid:
                self._export = answer["targets"]
                self.exportChanged.emit()

        self._export = []
        self.exportChanged.emit()
        self.app._call(lambda: self.app.client.card_export(pid), show)

    @Slot(str)
    def exportCard(self, target: str) -> None:
        """Writes the card into the project after the user agrees (D23: nothing appears there unasked)."""
        found = next((t for t in self._export if t["target"] == target), None)
        if not self.current or found is None or found["mode"] == "unchanged":
            return
        pid, path = self.current["id"], found["path"]
        text = project_rows.export_question(found)

        def decide(yes: bool) -> None:
            if not yes:
                return

            def done(_answer: dict[str, Any]) -> None:
                self.app.flash(f"Карточка записана в {path}")
                self.loadExport()

            self.app._call(lambda: self.app.client.export_card(pid, [target]), done)

        self.app._ask(f"Записать карточку в {path}?", text, "Записать", decide)

    def load_card(self) -> None:
        if not self.current or self.app.client is None:
            return
        pid = self.current["id"]

        def show(card: dict[str, Any]) -> None:
            if pid == self.currentId:
                self._card = card
                self.cardChanged.emit()

        self.app._call(lambda: self.app.client.card(pid), show, lambda _e: None)

    def _card_writer(self) -> str:
        """Who drafts: the model of the role «cards» (D39), else the chosen one if local, else the first local one
        (D24: local by default)."""
        if self.app.role_model("cards"):
            return self.app.role_model("cards")
        models = self.app.model_list
        chosen = self.app.models.get(self.app.modelId)
        if chosen and chosen.get("locality") == "local":
            return chosen["id"]
        local = next((m for m in models if m.get("locality") == "local"), None)
        return local["id"] if local else self.app.modelId

    @Slot()
    def updateCard(self) -> None:
        """A draft that brings the confirmed card up to date: only the sections the changes touch (D24)."""
        self.draftCard(update=True)

    @Slot()
    def draftCard(self, update: bool = False) -> None:
        if not self.current:
            return
        pid, model_id = self.current["id"], self._card_writer()
        self._card_error = ""
        self._card = {**self._card, "drafting": True}
        self.cardChanged.emit()

        def failed(error: Exception) -> None:
            self._card = {**self._card, "drafting": False}
            self._card_error = str(error)
            self.cardChanged.emit()
            self.app.show_error(f"Черновик карточки не начат: {error}")

        self.app.call_or_fail(lambda: self.app.client.draft_card(pid, model_id, update=update), None, failed)

    @Slot(str)
    def confirmCard(self, text: str) -> None:
        if not self.current:
            return
        pid = self.current["id"]

        def done(card: dict[str, Any]) -> None:
            if pid == self.currentId:
                self._card = card
                self.cardChanged.emit()
            self.app.flash(f"Карточка подтверждена, версия {card['confirmed']['version']}")

        self.app._call(lambda: self.app.client.confirm_card(pid, text), done)

    @Slot(int)
    def restoreCard(self, version: int) -> None:
        if self.current:
            pid = self.current["id"]
            self.app._call(lambda: self.app.client.restore_card(pid, version), lambda _c: self.load_card())

    @Slot()
    def dropCardDraft(self) -> None:
        if self.current:
            pid = self.current["id"]
            self.app._call(lambda: self.app.client.drop_card_draft(pid), lambda _r: self.load_card())

    # ================= links between projects (D25) =================

    def load_links(self) -> None:
        if self.app.client is None:
            return

        def show(found: list[dict[str, Any]]) -> None:
            self._links = found
            self.linksChanged.emit()

        self.app._call(lambda: self.app.client.links(), show, lambda _e: None)

    @Slot()
    def scanLinks(self) -> None:
        self._scanning = True
        self.linksChanged.emit()

        def done(result: dict[str, Any]) -> None:
            self._scanning = False
            self._links = result["links"]
            self.linksChanged.emit()
            added = result["added"]
            self.app.flash(f"Найдено новых связей: {added}" if added else "Новых связей не найдено")

        def failed(error: Exception) -> None:
            self._scanning = False
            self.linksChanged.emit()
            self.app.show_error(f"Поиск связей не удался: {error}")

        self.app.call_or_fail(lambda: self.app.client.scan_links(), done, failed)

    @Slot(int, str)
    def confirmLink(self, link_id: int, description: str) -> None:
        self.app._call(lambda: self.app.client.set_link(link_id, status="confirmed", description=description),
                       lambda _r: self.load_links())  # fmt: skip

    @Slot(int)
    def rejectLink(self, link_id: int) -> None:
        self.app._call(lambda: self.app.client.set_link(link_id, status="rejected"), lambda _r: self.load_links())

    @Slot(int)
    def deleteLink(self, link_id: int) -> None:
        self.app._call(lambda: self.app.client.delete_link(link_id), lambda _r: self.load_links())

    # ================= what models wrote (stage 5, D17) =================

    def load_changes(self) -> None:
        if not self.current or self.app.client is None:
            return
        pid = self.current["id"]

        def show(found: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
            if pid == self.currentId:
                self._changes, self._rules = found
                self.changesChanged.emit()

        self.app._call(lambda: (self.app.client.changes(pid), self.app.client.rules(pid)), show, lambda _e: None)

    @Slot(int)
    def revertChange(self, change_id: int) -> None:
        """Undo a model's write: asked first; if the file changed since, asked again whether to lose that."""
        found = next((c for c in self._changes if c["id"] == change_id), None)
        if found is None or found.get("reverted_at"):
            return
        path = found["path"]

        def undo(force: bool) -> None:
            def failed(error: Exception) -> None:
                if not force and "changed since" in str(error):
                    self.app._ask(
                        f"{path} изменился после этой правки",
                        "Откат вернёт файл к виду до правки модели, и всё, что менялось в нём потом, пропадёт. "
                        "Откатить всё равно?",
                        "Откатить",
                        lambda yes: undo(True) if yes else None,
                    )
                else:
                    self.app.show_error(f"Не откатилось: {error}")

            def done(_change: dict[str, Any]) -> None:
                self.app.flash(f"Правка {path} откачена")
                self.load_changes()

            self.app._call(lambda: self.app.client.revert_change(change_id, force=force), done, failed)

        made = "созданный моделью файл будет удалён" if found["before_hash"] is None else "файл вернётся к виду до неё"
        self.app._ask(f"Откатить правку {path}?", f"Правка модели отменится: {made}.", "Откатить",
                      lambda yes: undo(False) if yes else None)  # fmt: skip

    @Slot(int)
    def removeRule(self, rule_id: int) -> None:
        self.app._call(lambda: self.app.client.delete_rule(rule_id), lambda _r: self.load_changes())

    # ================= notes (D22 L4) =================

    def load_notes(self) -> None:
        if not self.current or self.app.client is None:
            return
        pid = self.current["id"]

        def show(found: list[dict[str, Any]]) -> None:
            if pid == self.currentId:
                self._notes = found
                self.notesChanged.emit()

        self.app._call(lambda: self.app.client.notes(pid), show, lambda _e: None)

    @Slot(str)
    def addNote(self, text: str) -> None:
        if self.current and text.strip():
            pid = self.current["id"]
            self.app._call(lambda: self.app.client.add_note(pid, text), lambda _n: self.load_notes())

    @Slot(int, str)
    def updateNote(self, note_id: int, text: str) -> None:
        if text.strip():
            self.app._call(lambda: self.app.client.update_note(note_id, text), lambda _n: self.load_notes())

    @Slot(int)
    def deleteNote(self, note_id: int) -> None:
        self.app._call(lambda: self.app.client.delete_note(note_id), lambda _r: self.load_notes())

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
            self.app._clipboard().setText(str(full))
            self.app.flash("Путь скопирован", ms=2000)
