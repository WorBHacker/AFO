"""«Песочница» in the window (D17, stage 9.4b): a chat in this mode writes into a git worktree of each project; a bar
over the composer says how many files the model changed, and the sheet shows them with their diff - «Применить» puts
them onto the project, «Отбросить» throws them away.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, plural, tr

from .approvals import PREVIEW_LINES, preview_lines

if TYPE_CHECKING:
    from .controller import Controller

STATUS = {"added": N_("новый"), "deleted": N_("удалён"), "changed": N_("изменён")}


def sandbox_rows(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": p["project_id"], "name": p["name"], "error": p.get("error", ""),
             "files": [f"{f['path']} — {tr(STATUS.get(f['status'], f['status']))}" for f in p.get("files", [])],
             "lines": preview_lines({"kind": "diff", "text": p.get("diff", "")}), "more": more_text(p)}
            for p in projects if p.get("files") or p.get("error")]  # fmt: skip


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
        self._busy = False

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return (self.app.contextSettings or {}).get("mode") == "sandbox"

    @Property(int, notify=changed)
    def count(self) -> int:
        return sum(len(p.get("files", [])) for p in self._projects)

    @Property(list, notify=changed)
    def projects(self) -> list:
        return sandbox_rows(self._projects)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def summary(self) -> str:
        n = self.count
        if not n:
            return tr("Песочница: модель пишет в отдельную копию проекта — здесь появятся её правки")
        return tr("Песочница: изменено {n} {files} — посмотрите и примените или отбросьте", n=n,
                  files=plural(n, "файл", "файла", "файлов"))  # fmt: skip

    @Slot()
    def load(self) -> None:
        cid = self.app.current
        if self.app.client is None or not cid or not self.active:
            self._conversation, self._projects = cid or "", []
            self.changed.emit()
            return

        def show(data: dict[str, Any]) -> None:
            if cid == self.app.current:
                self._conversation, self._projects = cid, data.get("projects", [])
                self.changed.emit()

        self.app._call(lambda: self.app.client.sandbox(cid), show, lambda _e: None)

    def _act(self, project_id: str, call, said) -> None:
        cid = self._conversation
        self._busy = True
        self.changed.emit()

        def ok(result: dict[str, Any]) -> None:
            self._busy = False
            self.app.flash(said(result))
            self.load()

        def failed(error: Exception) -> None:
            self._busy = False
            self.app.show_error(tr("Не вышло: {error}", error=error))
            self.changed.emit()

        self.app.call_or_fail(lambda: call(cid, project_id), ok, failed)

    @Slot(str)
    def apply(self, project_id: str) -> None:
        def said(result: dict[str, Any]) -> str:
            if result.get("conflicts"):
                return tr("Применено, но с конфликтами: {files} — поправьте их в проекте",
                          files=", ".join(result["conflicts"]))  # fmt: skip
            return tr("Применено к проекту: {count} файлов", count=len(result.get("applied", [])))

        self._act(project_id, self.app.client.apply_sandbox, said)

    @Slot(str)
    def discard(self, project_id: str) -> None:
        name = next((p["name"] for p in self._projects if p["project_id"] == project_id), "")

        def decide(yes: bool) -> None:
            if yes:
                self._act(project_id, self.app.client.discard_sandbox, lambda _r: tr("Правки песочницы отброшены"))

        self.app._ask(tr("Отбросить правки в «{name}»?", name=name),
                      tr("Копия проекта и её ветка удалятся; сам проект не изменится."), tr("Отбросить"),
                      decide)  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if payload.get("conversation_id") in (None, self.app.current):
            self.load()
