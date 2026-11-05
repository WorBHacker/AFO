"""«Песочница» in the window (D17, stage 9.4b): a chat in this mode writes into a git worktree of each project; a bar
over the composer says how many files the model changed, and the sheet shows them with their diff - «Применить» puts
them onto the project, «Отбросить» throws them away.

A coordinator's run (16.2, D50): its tasks wrote into sandboxes of their own; the same bar and sheet show what each
left, and «Применить всё» puts them onto the projects in the order the tasks ran, «Отбросить всё» throws them away.

Each file has a tick (17.1, D51), on by default: a file unticked stays in the sandbox, and the button says
«Применить выбранные (N из M)». After applying, what is left comes back ticked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, plural, tr

from .approvals import PREVIEW_LINES, preview_lines

if TYPE_CHECKING:
    from .controller import Controller

STATUS = {"added": N_("новый"), "deleted": N_("удалён"), "changed": N_("изменён")}


def file_key(task: str, project_id: str, path: str) -> str:
    """A file's tick: the task ("" for the chat's own sandbox), the project, the path."""
    return "\x1f".join((task, project_id, path))


def apply_text(ticked: int, total: int, everything: str) -> str:
    if ticked == total:
        return everything
    return tr("Применить выбранные ({ticked} из {total})", ticked=ticked, total=total)


def sandbox_rows(projects: list[dict[str, Any]], task: str = "", skipped: frozenset | set = frozenset()) -> list[dict]:
    rows = []
    for p in projects:
        if not (p.get("files") or p.get("error")):
            continue
        files = [{"path": f["path"], "label": f"{f['path']} — {tr(STATUS.get(f['status'], f['status']))}",
                  "key": (key := file_key(task, p["project_id"], f["path"])), "ticked": key not in skipped}
                 for f in p.get("files", [])]  # fmt: skip
        ticked = sum(f["ticked"] for f in files)
        rows.append({"id": p["project_id"], "name": p["name"], "error": p.get("error", ""), "files": files,
                     "ticked": ticked, "applyText": apply_text(ticked, len(files), tr("Применить")),
                     "lines": preview_lines({"kind": "diff", "text": p.get("diff", "")}),
                     "more": more_text(p)})  # fmt: skip
    return rows


def task_rows(tasks: list[dict[str, Any]], skipped: frozenset | set = frozenset()) -> list[dict[str, Any]]:
    """A coordinator's tasks' sandboxes, one row per task and project: «[a] «Писатель» · proj»."""
    return [row | {"name": f"[{t['task_id']}] «{t['agent']}» · {row['name']}"}
            for t in tasks for row in sandbox_rows(t.get("projects", []), t["task_id"], skipped)]  # fmt: skip


def more_text(project: dict[str, Any]) -> str:
    """What of the diff is not shown (D46): the window shows its first lines; the hub sends at most 400 000 characters
    - «Применить» takes every file all the same."""
    diff = str(project.get("diff") or "")
    total = len(diff.splitlines())
    cut = int(project.get("diff_chars") or 0) > len(diff)
    if total <= PREVIEW_LINES and not cut:
        return ""
    shown = min(total, PREVIEW_LINES)
    if cut:
        return tr("Показаны первые {shown} строк из {total} с лишним — «Применить» переносит все файлы целиком.",
                  shown=shown, total=total)  # fmt: skip
    return tr("Показаны первые {shown} строк из {total} — «Применить» переносит все файлы целиком.", shown=shown,
              total=total)  # fmt: skip


class SandboxView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._conversation = ""
        self._projects: list[dict[str, Any]] = []
        self._tasks: list[dict[str, Any]] = []  # a coordinator's tasks' sandboxes (16.2)
        self._skipped: set[str] = set()  # the files unticked (17.1): file_key
        self._busy = False

    def _own(self) -> bool:
        return (self.app.contextSettings or {}).get("mode") == "sandbox"

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return self._own() or bool(self._tasks)

    @Property(int, notify=changed)
    def count(self) -> int:
        return sum(len(p.get("files", [])) for p in self._projects)

    @Property(list, notify=changed)
    def projects(self) -> list:
        return sandbox_rows(self._projects, "", self._skipped)

    @Property(int, notify=changed)
    def taskCount(self) -> int:
        return sum(len(p.get("files", [])) for t in self._tasks for p in t.get("projects", []))

    @Property(list, notify=changed)
    def tasks(self) -> list:
        return task_rows(self._tasks, self._skipped)

    @Property(int, notify=changed)
    def taskTicked(self) -> int:
        return sum(row["ticked"] for row in self.tasks)

    @Property(str, notify=changed)
    def taskApplyText(self) -> str:
        return apply_text(self.taskTicked, self.taskCount, tr("Применить всё"))

    @Slot(str)
    def tick(self, key: str) -> None:
        """A file's tick on or off (17.1)."""
        self._skipped ^= {key}
        self.changed.emit()

    def _keys(self) -> set[str]:
        return {f["key"] for row in [*self.projects, *self.tasks] for f in row["files"]}

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def summary(self) -> str:
        if self._tasks:
            n, k = self.taskCount, len(self._tasks)
            return tr("Подзадачи: изменено {n} {files} в {k} {copies} — посмотрите и примените или отбросьте", n=n,
                      files=plural(n, "файл", "файла", "файлов"), k=k,
                      copies=plural(k, "песочнице", "песочницах", "песочницах"))  # fmt: skip
        n = self.count
        if not n:
            return tr("Песочница: модель пишет в отдельную копию проекта — здесь появятся её правки")
        return tr("Песочница: изменено {n} {files} — посмотрите и примените или отбросьте", n=n,
                  files=plural(n, "файл", "файла", "файлов"))  # fmt: skip

    @Slot()
    def load(self) -> None:
        """The chat's own sandbox (in «Песочница») and, for a coordinator's run, its tasks' (16.2)."""
        cid = self.app.current
        if self.app.client is None or not cid:
            self._conversation, self._projects, self._tasks = cid or "", [], []
            self.changed.emit()
            return

        def show(data: dict[str, Any]) -> None:
            if cid == self.app.current:
                if cid != self._conversation:
                    self._skipped.clear()
                self._conversation = cid
                self._projects = data.get("projects", []) if self._own() else []
                self._tasks = data.get("tasks", [])
                self._skipped &= self._keys()  # a file gone from the sandbox takes its tick with it
                self.changed.emit()

        self.app._call(lambda: self.app.client.sandbox(cid), show, lambda _e: None)

    def _act(self, call, said, done=None) -> None:
        cid = self._conversation
        self._busy = True
        self.changed.emit()

        def ok(result: dict[str, Any]) -> None:
            self._busy = False
            if done is not None:
                done()
            self.app.flash(said(result))
            self.load()

        def failed(error: Exception) -> None:
            self._busy = False
            self.app.show_error(tr("Не вышло: {error}", error=error))
            self.changed.emit()

        self.app.call_or_fail(lambda: call(cid), ok, failed)

    @Slot(str)
    def apply(self, project_id: str) -> None:
        row = next((r for r in self.projects if r["id"] == project_id), None)
        if row is None or not row["ticked"]:
            return
        files = None if row["ticked"] == len(row["files"]) else [f["path"] for f in row["files"] if f["ticked"]]

        def said(result: dict[str, Any]) -> str:
            if result.get("conflicts"):
                text = tr("Применено, но с конфликтами: {files} — поправьте их в проекте",
                          files=", ".join(result["conflicts"]))  # fmt: skip
            else:
                text = tr("Применено к проекту: {count} файлов", count=len(result.get("applied", [])))
            if result.get("kept"):
                text += " " + tr("Остальные файлы ({count}) ждут в песочнице.", count=len(result["kept"]))
            return text

        def untick() -> None:  # what is left comes back ticked
            self._skipped -= {f["key"] for f in row["files"]}

        self._act(lambda cid: self.app.client.apply_sandbox(cid, project_id, files), said, untick)

    @Slot(str)
    def discard(self, project_id: str) -> None:
        name = next((p["name"] for p in self._projects if p["project_id"] == project_id), "")

        def decide(yes: bool) -> None:
            if yes:
                self._act(lambda cid: self.app.client.discard_sandbox(cid, project_id),
                          lambda _r: tr("Правки песочницы отброшены"))  # fmt: skip

        self.app._ask(tr("Отбросить правки в «{name}»?", name=name),
                      tr("Копия проекта и её ветка удалятся; сам проект не изменится."), tr("Отбросить"),
                      decide)  # fmt: skip

    @Slot()
    def applyAll(self) -> None:
        """Every task's sandboxes onto the projects, in the order the tasks ran (16.2) - or the files ticked (17.1)."""
        rows = self.tasks
        if not self.taskTicked:
            return
        files: dict[str, dict[str, list[str]]] | None = None
        if self.taskTicked < self.taskCount:
            files = {}
            for t in self._tasks:
                for row in task_rows([t], self._skipped):
                    if ticked := [f["path"] for f in row["files"] if f["ticked"]]:
                        files.setdefault(t["task_id"], {})[row["id"]] = ticked

        def said(result: dict[str, Any]) -> str:
            applied = result.get("applied", [])
            conflicts = [f"{x['project']}: {name}" for x in applied for name in x.get("conflicts", [])]
            text = (tr("Применено, но с конфликтами: {files} — поправьте их в проекте", files=", ".join(conflicts))
                    if conflicts else tr("Применено к проекту: {count} файлов",
                                         count=sum(len(x["files"]) for x in applied)))  # fmt: skip
            if result.get("kept"):
                text += " " + tr("Не применены (подзадача не закончилась): {tasks}", tasks=", ".join(result["kept"]))
            return text

        def untick() -> None:
            self._skipped -= {f["key"] for row in rows for f in row["files"]}

        self._act(lambda cid: self.app.client.apply_tasks(cid, files), said, untick)

    @Slot()
    def discardAll(self) -> None:
        def decide(yes: bool) -> None:
            if yes:
                self._act(self.app.client.discard_tasks, lambda _r: tr("Правки подзадач отброшены"))

        self.app._ask(tr("Отбросить правки всех подзадач?"),
                      tr("Копии проектов и их ветки удалятся; сами проекты не изменятся."), tr("Отбросить всё"),
                      decide)  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if payload.get("conversation_id") in (None, self.app.current):
            self.load()
