"""The proving ground and the models' exam in the window (stage 12.2, D45): a panel with the tasks («Попробовать»
with the chat's model opens the run's chat, its goal at work), the tries, and the exam - the models and tasks chosen,
the judge or not, its progress and its table «tasks × models»; a cell opens that run's chat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

RUNS = {"waiting": "ждёт", "running": "идёт", "passed": "сдано", "failed": "не сдано", "error": "не началось",
        "skipped": "пропущено", "stopped": "остановлено"}  # fmt: skip
EXAMS = {"running": "идёт", "done": "закончен", "stopped": "остановлен"}


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} мин".replace(".", ",") if seconds >= 60 else f"{seconds:.0f} с"


def money(usd: float) -> str:
    return f"${usd:.2f}".replace(".", ",") if usd else "бесплатно"


def cell(run: dict[str, Any] | None) -> dict[str, Any]:
    """One run in the table: a mark, a line of what it took, why it did not pass."""
    if run is None:
        return {"mark": "", "text": "", "why": "", "run": "", "conversation": ""}
    status = run["status"]
    mark = {"passed": "✓", "failed": "✗", "error": "!", "running": "…", "waiting": "·"}.get(status, "–")
    text = RUNS.get(status, status)
    if status in ("passed", "failed"):
        tokens = f"{run['tokens'] / 1000:.0f} тыс." if run["tokens"] >= 1000 else str(run["tokens"])
        text = f"{run['turns']} ход. · {tokens} ток. · {minutes(run['seconds'])}"
    return {"mark": mark, "text": text, "why": "" if status == "passed" else run.get("detail", ""), "status": status,
            "run": run["id"], "conversation": run.get("conversation_id") or ""}  # fmt: skip


def table(exam: dict[str, Any]) -> dict[str, Any]:
    """The exam as the window draws it: the models' heads with their totals, a row per task with a cell per model."""
    names = {r["model_id"]: r["model_name"] for r in exam["runs"]}
    heads = [{"id": s["model_id"], "name": names.get(s["model_id"], s["model_id"]),
              "score": f"сдано {s['passed']} из {s['tasks']}",
              "spent": f"{money(s['cost_usd'])} · {minutes(s['seconds'])}"} for s in exam["summary"]]  # fmt: skip
    rows = []
    for task_id in exam["tasks"]:
        mine = {r["model_id"]: r for r in exam["runs"] if r["task_id"] == task_id}
        title = next((r["title"] for r in mine.values()), task_id)
        rows.append({"task": task_id, "title": title, "cells": [cell(mine.get(m)) for m in exam["models"]]})
    fails = [{"text": f"{r['title']} · {r['model_name']}", "why": r.get("detail", ""),
              "conversation": r.get("conversation_id") or ""}
             for r in exam["runs"] if r["status"] in ("failed", "error")]  # fmt: skip
    going = next((r for r in exam["runs"] if r["status"] == "running"), None)
    done = sum(r["status"] not in ("waiting", "running") for r in exam["runs"])
    state = EXAMS.get(exam["status"], exam["status"])
    if going:
        state += f": {done} из {len(exam['runs'])}, сейчас «{going['title']}» — {going['model_name']}"
    return {"id": exam["id"], "status": exam["status"], "state": state, "heads": heads, "rows": rows, "fails": fails,
            "when": local_time(exam["created_at"]), "judge": bool(exam.get("judge"))}  # fmt: skip


class ProvingView(QObject):
    changed = Signal()
    openRequested = Signal()
    examShown = Signal()  # the panel scrolls to the table
    noticed = Signal(str, str)  # an exam ended: for the notifier

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._tasks: list[dict[str, Any]] = []
        self._tries: list[dict[str, Any]] = []
        self._exams: list[dict[str, Any]] = []
        self._shown = ""  # the exam the table shows ('' - the newest)
        self._models: list[str] = []  # chosen for the exam
        self._skipped: set[str] = set()  # tasks left out of the exam
        self._judge = False
        self._busy = False
        self._reveal = False  # the panel was opened: an exam going shows first

    # ---------- what the panel shows ----------

    @Property(list, notify=changed)
    def tasks(self) -> list:
        out = []
        for t in self._tasks:
            limits = t["limits"]
            out.append({**t, "chosen": t["id"] not in self._skipped,
                        "limit": f"до {limits.get('max_turns', '?')} ходов, {limits.get('max_minutes', '?')} мин",
                        "checks": f"проверок: {t['criteria']} видимых, {t['hidden']} скрытых"})  # fmt: skip
        return out

    @Property(list, notify=changed)
    def tries(self) -> list:
        return [{"id": r["id"], "title": r["title"], "model": r["model_name"], "status": r["status"],
                 "state": RUNS.get(r["status"], r["status"]) + (f": {r['detail']}" if r["status"] == "failed" else ""),
                 "conversation": r.get("conversation_id") or ""} for r in self._tries]  # fmt: skip

    @Property(list, notify=changed)
    def models(self) -> list:
        """The models an exam may take: offered, not Claude Code (it carries no goal yet)."""
        out = []
        for model_id, model in self.app.models.items():
            if model.get("provider") == "claude-code" or not model.get("enabled", True):
                continue
            local = model.get("locality") == "local"
            out.append({"id": model_id, "name": model.get("name") or model_id, "chosen": model_id in self._models,
                        "note": "локальная: по очереди на видеокарте" if local else "облачная: платная",
                        "icon": "cpu" if local else "cloud"})  # fmt: skip
        return out

    @Property(bool, notify=changed)
    def judge(self) -> bool:
        return self._judge

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def examGoing(self) -> bool:
        return any(e["status"] == "running" for e in self._exams)

    @Property(list, notify=changed)
    def exams(self) -> list:
        return [{"id": e["id"], "name": f"{local_time(e['created_at'])} · "
                 + ", ".join(sorted({r['model_name'] for r in e['runs']})), "selected": e["id"] == self._exam_id()}
                for e in self._exams]  # fmt: skip

    @Property("QVariantMap", notify=changed)
    def exam(self) -> dict:
        found = next((e for e in self._exams if e["id"] == self._exam_id()), None)
        return table(found) if found else {}

    @Property(str, notify=changed)
    def workerName(self) -> str:
        model = self.app.models.get(self.app._model_id) or {}
        return model.get("name") or "модель не выбрана"

    def _exam_id(self) -> str:
        if self._shown and any(e["id"] == self._shown for e in self._exams):
            return self._shown
        return self._exams[0]["id"] if self._exams else ""

    # ---------- what it does ----------

    @Slot()
    def open(self) -> None:
        self._reveal = True
        self.load()
        self.openRequested.emit()

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return
        client = self.app.client

        def read() -> tuple[list, list, list]:
            return client.proving_tasks(), client.proving_runs(), client.proving_exams()

        def show(found: tuple[list, list, list]) -> None:
            self._tasks, self._tries, self._exams = found
            self.changed.emit()
            if self._reveal and self.examGoing:
                self.examShown.emit()
            self._reveal = False

        self.app._call(read, show, lambda e: self.app.show_error(f"Полигон не открылся: {e}"))

    def _act(self, call, done=None, said: str = "") -> None:
        self._busy = True
        self.changed.emit()

        def ok(result: Any) -> None:
            self._busy = False
            if said:
                self.app.flash(said)
            if done is not None:
                done(result)
            self.load()

        def failed(error: Exception) -> None:
            self._busy = False
            self.app.show_error(f"Не вышло: {error}")
            self.changed.emit()

        self.app.call_or_fail(call, ok, failed)

    @Slot(str)
    def tryTask(self, task_id: str) -> None:
        """«Попробовать»: the task on a fresh copy with the chat's model; its chat opens, the goal at work."""
        model_id = self.app._model_id
        if model_id not in self.app.models:
            self.app.show_error("Выберите модель внизу чата: она и будет решать задание")
            return

        def opened(run: dict[str, Any]) -> None:
            if run.get("conversation_id"):
                self.app.setMode("chats")
                self.app.openChat(run["conversation_id"])
            if run.get("status") == "error":
                self.app.show_error(run.get("detail") or "Задание не началось")

        self._act(lambda: self.app.client.proving_try(task_id, model_id), opened,
                  "Задание на свежей копии: модель работает, хаб проверит результат")  # fmt: skip

    @Slot(str)
    def openRun(self, conversation_id: str) -> None:
        """«Ход работы»: the run's chat (hidden from the chats' list)."""
        if conversation_id:
            self.app.setMode("chats")
            self.app.openChat(conversation_id)

    @Slot(str)
    def deleteRun(self, run_id: str) -> None:
        self._act(lambda: self.app.client.delete_proving_run(run_id), said="Проба удалена вместе с копией")

    @Slot(str)
    def toggleModel(self, model_id: str) -> None:
        self._models = [m for m in self._models if m != model_id] if model_id in self._models else \
            [*self._models, model_id]  # fmt: skip
        self.changed.emit()

    @Slot(str)
    def toggleTask(self, task_id: str) -> None:
        self._skipped ^= {task_id}
        self.changed.emit()

    @Slot(bool)
    def setJudge(self, judge: bool) -> None:
        self._judge = judge
        self.changed.emit()

    @Slot()
    def startExam(self) -> None:
        models = [m for m in self._models if m in self.app.models]
        tasks = [t["id"] for t in self._tasks if t["id"] not in self._skipped]
        if not models:
            self.app.show_error("Отметьте модели для экзамена")
            return
        if not tasks:
            self.app.show_error("Отметьте хотя бы одно задание")
            return

        def shown(exam: dict[str, Any]) -> None:
            self._shown = exam["id"]
            self._exams = [exam, *(e for e in self._exams if e["id"] != exam["id"])]
            self.changed.emit()
            self.examShown.emit()

        self._act(lambda: self.app.client.start_exam(models, tasks, self._judge), shown,
                  f"Экзамен начался: заданий {len(tasks)}, моделей {len(models)} — по очереди")  # fmt: skip

    @Slot()
    def stopExam(self) -> None:
        exam_id = self._exam_id()
        if exam_id:
            self._act(lambda: self.app.client.stop_exam(exam_id), said="Экзамен останавливается")

    @Slot(str)
    def showExam(self, exam_id: str) -> None:
        self._shown = exam_id
        self.changed.emit()
        self.examShown.emit()

    @Slot()
    def deleteExam(self) -> None:
        exam_id = self._exam_id()
        if not exam_id:
            return

        def decide(yes: bool) -> None:
            if yes:
                self._shown = ""
                self._act(lambda: self.app.client.delete_exam(exam_id), said="Экзамен удалён вместе с копиями")

        self.app._ask("Удалить экзамен?", "Его таблица, чаты прогонов и копии учебных проектов удалятся.", "Удалить",
                      decide)  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "proving.run":
            run = payload.get("run") or {}
            for exam in self._exams:  # the table moves at once; the totals come with the next read
                exam["runs"] = [run if r["id"] == run.get("id") else r for r in exam["runs"]]
            self._tries = [run if r["id"] == run.get("id") else r for r in self._tries]
            self.changed.emit()
            if run.get("status") not in ("running", "waiting"):
                self.load()
            return
        exam = payload.get("exam") or {}
        if kind == "proving.exam" and exam.get("status") == "done" and exam.get("runs"):
            said = "; ".join(f"{h['name']}: {h['score']}" for h in table(exam)["heads"])
            self.noticed.emit("Экзамен закончен", said)
            self.app.flash(f"Экзамен закончен — {said}")
        self.load()
