"""The programs the autonomous agent may control (D43): the list with each program's decision, and a question about
every program the hub finds after its first look.

The question comes from the list itself: a program is asked about while it is fresh and undecided, so one found
while the window was closed is asked about when it opens. After the first look there is one offer instead of a
question per program: "found N programs, choose what the agent may use".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from .models import RowModel
from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

FILTERS = [
    ("all", "Все"),
    ("allowed", "Разрешены"),
    ("denied", "Запрещены"),
    ("undecided", "Не решено"),
    ("new", "Новые"),
]
ROLES = ["key", "id", "name", "detail", "path", "decision", "fresh", "removed", "manual"]
KIND_NOTES = {"store": "Microsoft Store", "manual": "добавлена вручную"}


def matches(program: dict[str, Any], view: str) -> bool:
    decision = program.get("decision")
    return {
        "all": True,
        "allowed": decision == "allow",
        "denied": decision == "deny",
        "undecided": decision is None,
        "new": bool(program.get("fresh")) and decision is None,
    }.get(view, True)


def visible(program: dict[str, Any]) -> bool:
    """A program the last look did not find is shown only while its decision is worth seeing."""
    return not program.get("removed") or program.get("decision") is not None


def detail_of(program: dict[str, Any]) -> str:
    parts = [program.get("publisher", ""), program.get("version", ""), KIND_NOTES.get(program.get("kind", ""), "")]
    if program.get("removed"):
        parts.append("не найдена на компьютере")
    return " · ".join(p for p in parts if p)


def program_rows(programs: list[dict[str, Any]], view: str, query: str) -> list[dict[str, Any]]:
    wanted = query.casefold().strip()
    out = []
    for p in programs:
        if not visible(p) or not matches(p, view):
            continue
        if wanted and wanted not in f"{p['name']} {p.get('publisher', '')} {p.get('exe', '')}".casefold():
            continue
        out.append({
            "key": p["id"],
            "id": p["id"],
            "name": p["name"],
            "detail": detail_of(p),
            "path": p.get("exe") or p.get("folder") or p.get("aumid") or "",
            "decision": p.get("decision") or "",
            "fresh": bool(p.get("fresh")) and p.get("decision") is None,
            "removed": bool(p.get("removed")),
            "manual": p.get("kind") == "manual",
        })  # fmt: skip
    return out


def counts(programs: list[dict[str, Any]]) -> dict[str, int]:
    shown = [p for p in programs if visible(p)]
    return {key: sum(1 for p in shown if matches(p, key)) for key, _ in FILTERS}


def questions(programs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The programs to ask about: new since the first look, still undecided, still there - oldest first."""
    fresh = [p for p in programs if p.get("fresh") and p.get("decision") is None and not p.get("removed")]
    fresh.sort(key=lambda p: p.get("first_seen", ""))
    return [
        {"id": p["id"], "name": p["name"], "detail": detail_of(p), "path": p.get("exe") or p.get("folder") or ""}
        for p in fresh
    ]


class Programs(QObject):
    changed = Signal()  # the list, its counts, the look's state
    pendingChanged = Signal()  # the questions about new programs, the offer after the first look
    requested = Signal(str, str)  # a notice outside the window: a new program to decide about (D21)

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.rows = RowModel(self, roles=ROLES)
        self._programs: list[dict[str, Any]] = []
        self._view = "all"
        self._query = ""
        self._scanning = False
        self._scanned_at = ""
        self._error = ""
        self._offer = 0
        self._pending: list[dict[str, Any]] = []

    # ================= properties for QML =================

    @Property(QObject, constant=True)
    def rowModel(self) -> RowModel:
        return self.rows

    @Property(list, notify=changed)
    def filters(self) -> list:
        found = counts(self._programs)
        return [{"id": key, "name": f"{name} {found[key]}" if found[key] else name} for key, name in FILTERS]

    @Property(str, notify=changed)
    def view(self) -> str:
        return self._view

    @Property(bool, notify=changed)
    def scanning(self) -> bool:
        return self._scanning

    @Property(str, notify=changed)
    def status(self) -> str:
        if self._scanning:
            return "Ищу программы…"
        if self._error:
            return f"Поиск программ не удался: {self._error}"
        if not self._scanned_at and not self._programs:
            return "Хаб ещё не искал программы"
        allowed = sum(1 for p in self._programs if p.get("decision") == "allow" and visible(p))
        when = f"Последний поиск — {local_time(self._scanned_at)[-5:]}. " if self._scanned_at else ""
        return f"{when}Агенту разрешено программ: {allowed}. Не решённая — то же, что запрещённая."

    @Property(bool, notify=changed)
    def empty(self) -> bool:
        return not self.rows.rows

    @Property(list, notify=pendingChanged)
    def pending(self) -> list:
        return self._pending

    @Property(int, notify=pendingChanged)
    def offer(self) -> int:
        """After the first look: how many programs it found, until the list was offered (0: no offer)."""
        return self._offer

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        self.app._call(lambda: self.app.client.programs(), self._show, lambda _e: None)

    def _show(self, answer: dict[str, Any]) -> None:
        self._programs = answer.get("programs") or []
        self._scanning = bool(answer.get("scanning"))
        self._scanned_at = answer.get("scanned_at") or self._scanned_at
        self._error = answer.get("error") or ""
        offer = int((answer.get("offer") or {}).get("count", 0))
        offered, self._offer = self._offer != offer, offer
        self._refresh()
        self._ask(changed=offered)

    def _refresh(self) -> None:
        self.rows.sync(program_rows(self._programs, self._view, self._query))
        self.changed.emit()

    def _ask(self, *, changed: bool = False) -> None:
        pending = questions(self._programs)
        if changed or pending != self._pending:
            self._pending = pending
            self.pendingChanged.emit()

    @Slot()
    def scan(self) -> None:
        self._scanning = True
        self.changed.emit()

        def failed(error: Exception) -> None:
            self._scanning = False
            self._error = str(error)
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.scan_programs(), None, failed)

    @Slot(str)
    def setView(self, view: str) -> None:
        if view in dict(FILTERS) and view != self._view:
            self._view = view
            self._refresh()

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self._refresh()

    # ================= decisions =================

    @Slot(str, str)
    def decide(self, program_id: str, decision: str) -> None:
        """allow / deny / "" (not decided). The list shows it at once; if the hub refuses, it is read again."""
        value = decision or None
        self._programs = [
            {**p, "decision": value, "fresh": False} if p["id"] == program_id else p for p in self._programs
        ]
        self._refresh()
        self._ask()

        def failed(error: Exception) -> None:
            self.app.show_error(f"Решение не сохранено: {error}")
            self.load()

        self.app._call(lambda: self.app.client.decide_program(program_id, value), None, failed)

    @Slot(str)
    def later(self, program_id: str) -> None:
        """Not now: no longer asked; it stays undecided (no)."""
        self._programs = [{**p, "fresh": False} if p["id"] == program_id else p for p in self._programs]
        self._refresh()
        self._ask()
        self.app._call(lambda: self.app.client.put_off_program(program_id), None, lambda _e: self.load())

    @Slot(str)
    def addProgram(self, url: str) -> None:
        """An .exe from a file dialog (a file:// URL) or a path."""
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if not path:
            return

        def done(program: dict[str, Any]) -> None:
            self.app.flash(f"Программа «{program['name']}» в списке — решите, можно ли агенту")
            self.load()

        self.app._call(
            lambda: self.app.client.add_program(path),
            done,
            lambda error: self.app.show_error(f"Программа не добавлена: {error}"),
        )

    @Slot(str)
    def removeProgram(self, program_id: str) -> None:
        self.app._call(lambda: self.app.client.remove_program(program_id), lambda _r: self.load())

    @Slot()
    def dismissOffer(self) -> None:
        self._offer = 0
        self.pendingChanged.emit()
        self.app._call(lambda: self.app.client.dismiss_programs_offer(), None, lambda _e: None)

    @Slot()
    def openList(self) -> None:
        """The offer's "choose": the list, with the undecided first in view."""
        if self._offer:
            self.dismissOffer()
        self.setView("undecided" if counts(self._programs)["undecided"] else "all")
        self.app.uiRequested.emit("programs")

    # ================= the hub's events =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "programs.scanning":
            self._scanning = True
            self.changed.emit()
        elif kind == "programs.found":
            found = payload.get("programs") or []
            if found:
                names = ", ".join(p.get("name", "") for p in found[:3]) + ("…" if len(found) > 3 else "")
                title = "Новая программа" if len(found) == 1 else f"Новые программы: {len(found)}"
                self.requested.emit(title, f"{names} — разрешить автономному агенту?")
            self.load()
        elif kind in ("programs.scanned", "program.decided"):
            self.load()
