"""«История файла» (stage 26.2): every version of a project's file the hub kept (hub/history.py) - whoever changed it:
the user, a model, Claude Code, a command, another program, git. A version shows with its diff: what changed in it
(from the version before) or what taking it back would change (from the file now); «Вернуть» takes it back through
the hub, the way a save goes (14.1) - checked against the file as the sheet saw it, kept in «Изменения» as the
user's, to undo as any change.

Opened from the viewer's header, or from «Удалённые файлы» in the project's overview for a file that is gone. The
hub's `history.changed` and `file.*` events for the file read it again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

from ..hub_client import HubError
from . import project_rows

if TYPE_CHECKING:
    from .controller import Controller

WHO = {"save": N_("вы, в редакторе"), "restore": N_("вы вернули версию"), "write_file": N_("модель"),
       "edit_file": N_("модель")}  # fmt: skip
MODES = ("previous", "current")


def who(version: dict[str, Any]) -> str:
    """Who made the version, as far as the hub knows: its own writes by their names, the rest - «на диске»."""
    if version.get("deleted"):
        return tr("файл удалён")
    tool = version.get("tool") or ""
    if tool in ("write_file", "edit_file") and version.get("chat"):
        return tr("модель, чат «{chat}»", chat=version["chat"])
    return tr(WHO[tool]) if tool in WHO else tr("изменён на диске")


def version_rows(found: list[dict[str, Any]], current: str, selected: int) -> list[dict[str, Any]]:
    """The versions, newest first: when, who, size; the newest one the file is now is marked so («сейчас»)."""
    now = next((v["id"] for v in found if not v.get("deleted") and v.get("hash") == current), None)
    return [{"id": v["id"], "when": project_rows.local_time(v.get("created_at", "")), "who": who(v),
             "size": "" if v.get("deleted") else project_rows.size_text(int(v.get("size") or 0)),
             "deleted": bool(v.get("deleted")), "current": v["id"] == now, "selected": v["id"] == selected}
            for v in found]  # fmt: skip


class HistoryView(QObject):
    changed = Signal()
    closeRequested = Signal()  # taken back: the sheet closes, the file shows

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._pid = ""
        self._path = ""
        self._versions: list[dict[str, Any]] = []
        self._current = ""  # sha256 of the file now, as the sheet saw it; "" - no file
        self._selected = 0
        self._mode = "previous"
        self._view: dict[str, Any] = {}
        self._state = ""  # loading / ready / failed
        self._busy = False
        self._gen = 0

    @Property(str, notify=changed)
    def path(self) -> str:
        return self._path

    @Property(list, notify=changed)
    def rows(self) -> list:
        return version_rows(self._versions, self._current, self._selected)

    @Property(int, notify=changed)
    def selected(self) -> int:
        return self._selected

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property("QVariantMap", notify=changed)
    def view(self) -> dict:
        """The selected version's diff for the CodeView: {text or chunks, lexer, key, empty}."""
        return self._view

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def empty(self) -> str:
        """What the sheet says with no version to show: why there is none."""
        if self._state != "ready" or self._versions:
            return ""
        return tr("Хаб ещё не видел, как этот файл меняется: история начинается с первого изменения после того, как "
                  "проект добавлен. Не хранятся секретные файлы, файлы больше 2 МБ и двоичные.")  # fmt: skip

    @Property(str, notify=changed)
    def note(self) -> str:
        """The selected version in words, over its diff."""
        found = self._chosen()
        if found is None:
            return ""
        row = next(r for r in self.rows if r["id"] == found["id"])
        said = [row["when"], row["who"], row["size"], tr("сейчас на диске") if row["current"] else ""]
        return " · ".join(s for s in said if s)

    @Property(bool, notify=changed)
    def canRestore(self) -> bool:
        found = self._chosen()
        return bool(found) and not found.get("deleted") and found.get("hash") != self._current and not self._busy

    def _chosen(self) -> dict[str, Any] | None:
        return next((v for v in self._versions if v["id"] == self._selected), None)

    # ---------------------------------------------------------------- reading

    @Slot(str, str)
    def show(self, project_id: str, path: str) -> None:
        """The file's history, its newest version chosen."""
        if (project_id, path) != (self._pid, self._path):
            self._versions, self._selected, self._view, self._current = [], 0, {}, ""
        self._pid, self._path, self._mode = project_id, path, "previous"
        self._state = "loading"
        self.changed.emit()
        self._load(keep=False)

    def _load(self, *, keep: bool) -> None:
        """`keep`: the chosen version stays chosen, if it is still there."""
        if self.app.client is None or not self._pid:
            return
        pid, path = self._pid, self._path
        self._gen += 1
        gen = self._gen

        def fetch() -> tuple[list[dict[str, Any]], str]:
            try:
                current = self.app.client.file(pid, path).get("sha256", "")
            except HubError:
                current = ""  # gone from the disk
            return self.app.client.history(pid, path), current

        def show(found: tuple[list[dict[str, Any]], str]) -> None:
            if gen != self._gen:
                return
            self._versions, self._current = found
            ids = [v["id"] for v in self._versions]
            if not (keep and self._selected in ids):
                self._selected = ids[0] if ids else 0
            self._state = "ready"
            self.changed.emit()
            self._load_view()

        def failed(error: Exception) -> None:
            if gen == self._gen:
                self._state = "failed"
                self.changed.emit()
                self.app.show_error(tr("История не открылась: {error}", error=error))

        self.app._call(fetch, show, failed)

    def _load_view(self) -> None:
        version, mode = self._selected, self._mode
        if not version:
            self._view = {}
            self.changed.emit()
            return
        gen = self._gen

        def fetch() -> dict[str, Any]:
            found = self.app.client.version(version, mode)
            text = found.get("diff", "")
            return {**project_rows.diff_view(text), "key": f"history:{version}:{mode}", "digits": 0, "empty": not text}

        def show(view: dict[str, Any]) -> None:
            if gen == self._gen and (version, mode) == (self._selected, self._mode):
                self._view = view
                self.changed.emit()

        def failed(error: Exception) -> None:
            if gen == self._gen:
                self._view = {}
                self.changed.emit()
                self.app.show_error(tr("Версия не открылась: {error}", error=error))

        self.app._call(fetch, show, failed)

    @Slot(int)
    def pick(self, version_id: int) -> None:
        if version_id != self._selected and any(v["id"] == version_id for v in self._versions):
            self._selected = version_id
            self.changed.emit()
            self._load_view()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        """«Что изменилось» (from the version before) or «С файлом сейчас» (what taking it back changes)."""
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self.changed.emit()
            self._load_view()

    @Slot()
    def close(self) -> None:
        self._gen += 1
        self._pid, self._path, self._versions, self._view, self._state = "", "", [], {}, ""
        self.changed.emit()

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        """The hub kept a new version of the file, or wrote it: read again."""
        if not self._pid or payload.get("project_id") != self._pid:
            return
        paths = payload.get("paths") if kind == "history.changed" else [
            payload.get("path") or (payload.get("change") or {}).get("path")]  # fmt: skip
        if self._path in (paths or []):
            self._load(keep=True)

    # ---------------------------------------------------------------- taking a version back

    @Slot()
    def restore(self) -> None:
        """«Вернуть»: asked first; the hub writes the version when the file is still what the sheet saw."""
        found = self._chosen()
        if found is None or not self.canRestore:
            return
        pid, path, base = self._pid, self._path, self._current
        when = project_rows.local_time(found.get("created_at", ""))
        name = path.rsplit("/", 1)[-1]

        def done(_result: dict[str, Any]) -> None:
            self._busy = False
            self.app.flash(tr("{name}: возвращена версия от {when}", name=name, when=when))
            self.closeRequested.emit()
            self.close()
            if self.app.projects.currentId == pid:
                self.app.projects.openFile(path, False)

        def failed(error: Exception) -> None:
            self._busy = False
            self.changed.emit()
            if isinstance(error, HubError) and error.data.get("reason"):
                self.app.show_error(tr("Не вернулось: файл изменился, пока была открыта история. Посмотрите ещё раз."))
                self._load(keep=True)
            else:
                self.app.show_error(tr("Не вернулось: {error}", error=error))

        def decide(yes: bool) -> None:
            if yes:
                self._busy = True
                self.changed.emit()
                self.app.call_or_fail(lambda: self.app.client.restore_version(found["id"], base), done, failed)

        now = tr("Нынешний вид останется в истории, а возврат — в «Изменениях»: его можно откатить.")
        if not base:
            now = tr("Файл появится снова.")
        self.app._ask(tr("Вернуть {name} к версии от {when}?", name=name, when=when),
                      tr("Файл станет таким, каким был в этой версии.") + " " + now, tr("Вернуть"), decide)  # fmt: skip
