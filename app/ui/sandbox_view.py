"""«Песочница» in the window (D17, stage 9.4b): a chat in this mode writes into a git worktree of each project; a bar
over the composer says how many files the model changed, and the sheet shows them with their diff - «Применить» puts
them onto the project, «Отбросить» throws them away.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .approvals import preview_lines

if TYPE_CHECKING:
    from .controller import Controller

STATUS = {"added": "новый", "deleted": "удалён", "changed": "изменён"}


def sandbox_rows(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": p["project_id"], "name": p["name"], "error": p.get("error", ""),
             "files": [f"{f['path']} — {STATUS.get(f['status'], f['status'])}" for f in p.get("files", [])],
             "lines": preview_lines({"kind": "diff", "text": p.get("diff", "")})}
            for p in projects if p.get("files") or p.get("error")]  # fmt: skip


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
            return "Песочница: модель пишет в отдельную копию проекта — здесь появятся её правки"
        word = "файл" if n % 10 == 1 and n % 100 != 11 else "файла" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 \
            else "файлов"  # fmt: skip
        return f"Песочница: изменено {n} {word} — посмотрите и примените или отбросьте"

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
            self.app.show_error(f"Не вышло: {error}")
            self.changed.emit()

        self.app.call_or_fail(lambda: call(cid, project_id), ok, failed)

    @Slot(str)
    def apply(self, project_id: str) -> None:
        def said(result: dict[str, Any]) -> str:
            if result.get("conflicts"):
                return f"Применено, но с конфликтами: {', '.join(result['conflicts'])} — поправьте их в проекте"
            return f"Применено к проекту: {len(result.get('applied', []))} файлов"

        self._act(project_id, self.app.client.apply_sandbox, said)

    @Slot(str)
    def discard(self, project_id: str) -> None:
        name = next((p["name"] for p in self._projects if p["project_id"] == project_id), "")

        def decide(yes: bool) -> None:
            if yes:
                self._act(project_id, self.app.client.discard_sandbox, lambda _r: "Правки песочницы отброшены")

        self.app._ask(f"Отбросить правки в «{name}»?", "Копия проекта и её ветка удалятся; сам проект не изменится.",
                      "Отбросить", decide)  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if payload.get("conversation_id") in (None, self.app.current):
            self.load()
