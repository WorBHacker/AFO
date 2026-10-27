"""The agent's memory in the window (stage 9.3, D40, Q24): the facts the hub keeps for good - each with where it came
from and when, marked personal or allowed for a cloud model, corrected or deleted here - and the skills: plans of
finished tasks the user chose to keep, given to similar goals as a hint. Opened from the Agent mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller


def fact_rows(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in facts:
        source = "вы" if f.get("source") == "user" else "агент"
        when = local_time(f.get("created_at", ""))
        out.append(
            {
                "id": f["id"],
                "text": f["text"],
                "about": f.get("about") or "",
                "personal": f["personal"],
                "cloudOk": f["cloud_ok"],
                "meta": " · ".join(p for p in (f.get("about"), source, when) if p),
            }
        )
    return out


def skill_rows(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": s["id"], "name": s["name"], "goal": s["goal"],
             "steps": "\n".join(f"{n}. {st['title']}" for n, st in enumerate(s["steps"], 1)),
             "meta": f"шагов {len(s['steps'])} · подсказан {s.get('uses', 0)} раз · "
                     f"{local_time(s.get('created_at', ''))}"}
            for s in skills]  # fmt: skip


class MemoryView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._facts: list[dict[str, Any]] = []
        self._skills: list[dict[str, Any]] = []
        self._query = ""

    @Property(list, notify=changed)
    def facts(self) -> list:
        return fact_rows(self._facts)

    @Property(list, notify=changed)
    def skills(self) -> list:
        return skill_rows(self._skills)

    @Property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @Slot()
    def load(self) -> None:
        query = self._query

        def show(found: tuple[list, list]) -> None:
            if query == self._query:
                self._facts, self._skills = found
                self.changed.emit()

        self.app._call(lambda: (self.app.client.memory_facts(query), self.app.client.skills(query)), show,
                       lambda _e: None)  # fmt: skip

    @Slot(str)
    def search(self, query: str) -> None:
        self._query = query.strip()
        self.load()

    @Slot(str, str, bool)
    def addFact(self, text: str, about: str, personal: bool) -> None:
        if not text.strip():
            return
        self.app.call_or_fail(lambda: self.app.client.add_fact(text.strip(), about.strip(), personal),
                              lambda _f: self.load(), lambda e: self.app.show_error(f"Не запомнено: {e}"))  # fmt: skip

    @Slot(int, str)
    def saveFact(self, fact_id: int, text: str) -> None:
        if text.strip():
            self._change(fact_id, text=text.strip())

    @Slot(int, str, bool)
    def setFlag(self, fact_id: int, name: str, value: bool) -> None:
        if name in ("personal", "cloud_ok"):
            self._change(fact_id, **{name: value})

    def _change(self, fact_id: int, **changes: Any) -> None:
        self.app.call_or_fail(lambda: self.app.client.change_fact(fact_id, **changes), lambda _f: self.load(),
                              lambda e: self.app.show_error(f"Не изменено: {e}"))  # fmt: skip

    @Slot(int)
    def deleteFact(self, fact_id: int) -> None:
        self.app.call_or_fail(lambda: self.app.client.delete_fact(fact_id), lambda _r: self.load(),
                              lambda e: self.app.show_error(f"Не удалено: {e}"))  # fmt: skip

    @Slot(int)
    def deleteSkill(self, skill_id: int) -> None:
        self.app.call_or_fail(lambda: self.app.client.delete_skill(skill_id), lambda _r: self.load(),
                              lambda e: self.app.show_error(f"Не удалено: {e}"))  # fmt: skip

    @Slot(str, str)
    def keepSkill(self, run_id: str, name: str) -> None:
        """A finished task's plan as a skill (the user chose to, from its report)."""

        def done(found: dict[str, Any]) -> None:
            self.app.flash(f"Навык «{found['name']}» сохранён: похожие задачи получат его подсказкой")
            self.load()

        self.app.call_or_fail(lambda: self.app.client.add_skill(run_id, name), done,
                              lambda e: self.app.show_error(f"Навык не сохранён: {e}"))  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "memory.changed":
            self.load()
