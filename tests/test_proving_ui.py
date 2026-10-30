"""The proving ground in the window (stage 12.2, D45): the panel's tasks, «Попробовать» with the chat's model (its run's
chat opens), the exam - models and tasks chosen, the judge, started, stopped, deleted - and its table «tasks ×
models», which moves at once with the hub's events."""

import copy

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.ui import proving_view
from app.ui.project_rows import local_time
from common.paths import data_paths
from hub.proving import summary
from tests.test_ui import FakeClient, qapp, wait_until  # noqa: F401 - the qapp fixture

OPUS, CODER = "anthropic/claude-opus-5-5", "llama-swap/coder"
NAMES = {OPUS: "Claude Opus 5.5", CODER: "Coder"}
TASKS = [{"id": "fizzbuzz", "title": "FizzBuzz с тестами", "level": "лёгкое", "about": "Функция и её тесты.",
          "objective": "…", "limits": {"max_turns": 12, "max_minutes": 20}, "criteria": 1, "hidden": 2},
         {"id": "shop_bug", "title": "Скидка в корзине", "level": "трудное", "about": "Найти ошибку в скидке.",
          "objective": "…", "limits": {"max_turns": 25, "max_minutes": 40}, "criteria": 1, "hidden": 1}]  # fmt: skip
TITLES = {t["id"]: t["title"] for t in TASKS}


def run(n, task, model, status, exam="e1", **more):
    return {"id": f"r{n}", "exam_id": exam, "n": n, "task_id": task, "title": TITLES[task], "model_id": model,
            "model_name": NAMES[model], "status": status, "detail": "", "goal_status": "", "checks": [], "turns": 0,
            "tokens": 0, "cost_usd": 0.0, "seconds": 0.0, "conversation_id": None, **more}  # fmt: skip


def exam_of(exam_id, runs, models, tasks, status="running", judge=0):
    return {"id": exam_id, "models": models, "tasks": tasks, "judge": judge, "status": status, "created_by": "app",
            "created_at": "2026-10-30T12:00:41.123Z", "ended_at": None, "runs": runs,
            "summary": summary(runs, models)}  # fmt: skip


EXAM = exam_of("e1", [
    run(1, "fizzbuzz", OPUS, "passed", turns=4, tokens=23_400, cost_usd=0.41, seconds=150, conversation_id="c1"),
    run(2, "fizzbuzz", CODER, "failed", turns=12, tokens=800, seconds=30, conversation_id="c2",
        detail="скрытая проверка не прошла: fizzbuzz(15) должно быть «FizzBuzz»"),
    run(3, "shop_bug", OPUS, "running", conversation_id="c3"),
    run(4, "shop_bug", CODER, "waiting")], [OPUS, CODER], ["fizzbuzz", "shop_bug"])  # fmt: skip


def test_the_exam_as_a_table():
    t = proving_view.table(EXAM)
    assert t["state"] == "идёт: 2 из 4, сейчас «Скидка в корзине» — Claude Opus 5.5"
    assert t["when"] == local_time("2026-10-30T12:00:41.123Z") and t["status"] == "running" and not t["judge"]
    assert t["fails"] == [{"text": "FizzBuzz с тестами · Coder", "conversation": "c2",
                           "why": "скрытая проверка не прошла: fizzbuzz(15) должно быть «FizzBuzz»"}]  # fmt: skip
    assert t["heads"] == [
        {"id": OPUS, "name": "Claude Opus 5.5", "score": "сдано 1 из 2", "spent": "$0,41 · 2,5 мин"},
        {"id": CODER, "name": "Coder", "score": "сдано 0 из 2", "spent": "бесплатно · 30 с"}]  # fmt: skip
    assert [r["title"] for r in t["rows"]] == ["FizzBuzz с тестами", "Скидка в корзине"]
    passed, failed = t["rows"][0]["cells"]
    assert passed == {"mark": "✓", "text": "4 ход. · 23 тыс. ток. · 2,5 мин", "why": "", "status": "passed",
                      "run": "r1", "conversation": "c1"}  # fmt: skip
    assert failed["mark"] == "✗" and failed["text"] == "12 ход. · 800 ток. · 30 с"
    assert failed["why"].startswith("скрытая проверка не прошла")
    going, waiting = t["rows"][1]["cells"]
    assert (going["mark"], going["text"]) == ("…", "идёт") and (waiting["mark"], waiting["conversation"]) == ("·", "")
    assert proving_view.cell(None)["mark"] == ""  # a model the exam did not give this task
    done = proving_view.table({**EXAM, "status": "stopped", "runs": EXAM["runs"][:2]})
    assert done["state"] == "остановлен"


class ProvingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.proving_calls = []
        self.try_list = []
        self.exam_list = [{**copy.deepcopy(EXAM), "status": "done"}]

    def proving_tasks(self):
        return copy.deepcopy(TASKS)

    def proving_runs(self):
        return copy.deepcopy(self.try_list)

    def proving_exams(self):
        return copy.deepcopy(self.exam_list)

    def proving_try(self, task_id, model_id):
        self.proving_calls.append(("try", task_id, model_id))
        tried = run(9, task_id, model_id, "running", exam=None, conversation_id="c1")
        self.try_list.insert(0, tried)
        return tried

    def delete_proving_run(self, run_id):
        self.proving_calls.append(("delete run", run_id))
        self.try_list = [r for r in self.try_list if r["id"] != run_id]

    def start_exam(self, model_ids, task_ids, judge):
        self.proving_calls.append(("exam", model_ids, task_ids, judge))
        runs = [run(10 + i, t, m, "waiting", exam="e2") for i, (t, m) in
                enumerate((t, m) for t in task_ids for m in model_ids)]  # fmt: skip
        exam = exam_of("e2", runs, model_ids, task_ids, judge=int(judge))
        self.exam_list.insert(0, exam)
        return exam

    def stop_exam(self, exam_id):
        self.proving_calls.append(("stop", exam_id))
        for exam in self.exam_list:
            if exam["id"] == exam_id:
                exam["status"] = "stopped"
        return {"id": exam_id}

    def delete_exam(self, exam_id):
        self.proving_calls.append(("delete exam", exam_id))
        self.exam_list = [e for e in self.exam_list if e["id"] != exam_id]


def test_the_proving_ground_in_the_window(qapp):  # noqa: F811
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = ProvingClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.proving_view
        sheet = quick.window.findChild(QObject, "provingSheet")
        assert not sheet.property("opened")
        shown, noticed = [], []
        view.examShown.connect(lambda: shown.append(view.exam.get("id")))  # the panel scrolls to the table
        view.noticed.connect(lambda title, text: noticed.append((title, text)))

        app.runCommand("action:proving")  # the palette; the title bar's flask does the same
        wait_until(lambda: not app.bg.busy and sheet.property("opened"))
        assert [t["id"] for t in view.tasks] == ["fizzbuzz", "shop_bug"]
        assert view.tasks[0]["limit"] == "до 12 ходов, 20 мин"
        assert view.tasks[1]["checks"] == "проверок: 1 видимых, 1 скрытых"
        assert view.workerName == NAMES[app.modelId] and view.exam["id"] == "e1" and not view.examGoing
        assert [m["id"] for m in view.models] == [OPUS, CODER] and not any(m["chosen"] for m in view.models)

        # «Попробовать»: the chat's model on a fresh copy; the run's chat opens
        worker = app.modelId
        view.tryTask("fizzbuzz")
        wait_until(lambda: not app.bg.busy and view.tries)
        assert client.proving_calls[-1] == ("try", "fizzbuzz", worker)
        wait_until(lambda: app.currentId == "c1" and app.mode == "chats")
        assert app.flashText.startswith("Задание на свежей копии")
        assert view.tries[0]["state"] == "идёт" and view.tries[0]["model"] == NAMES[worker]
        view.deleteRun("r9")
        wait_until(lambda: not app.bg.busy and not view.tries)

        # the exam: nothing chosen - said, not sent
        view.startExam()
        assert app.flashText == "Отметьте модели для экзамена" and client.proving_calls[-1][0] == "delete run"
        view.toggleModel(CODER)
        view.toggleModel(OPUS)
        view.toggleModel(CODER)
        view.toggleModel(CODER)  # the order chosen is the table's
        view.toggleTask("shop_bug")
        view.setJudge(True)
        assert [m["chosen"] for m in view.models] == [True, True] and not view.tasks[1]["chosen"] and view.judge
        view.startExam()
        wait_until(lambda: not app.bg.busy and view.exam["id"] == "e2")
        assert client.proving_calls[-1] == ("exam", [OPUS, CODER], ["fizzbuzz"], True)
        assert app.flashText == "Экзамен начался: заданий 1, моделей 2 — по очереди"
        assert view.examGoing and view.exam["judge"]
        assert [h["name"] for h in view.exam["heads"]] == ["Claude Opus 5.5", "Coder"]
        assert len(view.exams) == 2 and view.exams[0]["selected"] and shown == ["e2"]
        view.open()  # an exam going: the panel opens at its table
        wait_until(lambda: not app.bg.busy and shown == ["e2", "e2"])

        # the hub's events: a run going moves its cell at once; a run done reads the totals again
        going = run(10, "fizzbuzz", OPUS, "running", exam="e2", conversation_id="c1")
        client.exam_list[0]["runs"][0] = going
        app.on_event({"type": "proving.run", "payload": {"run": going}})
        assert view.exam["rows"][0]["cells"][0]["mark"] == "…"
        assert view.exam["state"].startswith("идёт: 0 из 2, сейчас «FizzBuzz с тестами»")
        done = {**going, "status": "passed", "turns": 3, "tokens": 9000, "seconds": 70}
        client.exam_list[0]["runs"][0] = done
        client.exam_list[0]["summary"] = summary(client.exam_list[0]["runs"], [OPUS, CODER])
        app.on_event({"type": "proving.run", "payload": {"run": done}})
        wait_until(lambda: not app.bg.busy and view.exam["heads"][0]["score"] == "сдано 1 из 1")
        assert view.exam["rows"][0]["cells"][0]["text"] == "3 ход. · 9 тыс. ток. · 1,2 мин"

        view.stopExam()
        wait_until(lambda: not app.bg.busy and not view.examGoing)
        assert client.proving_calls[-1] == ("stop", "e2")
        app.on_event({"type": "proving.exam", "payload": {"exam": client.exam_list[0]}})
        wait_until(lambda: not app.bg.busy and view.exam["status"] == "stopped" and not view.examGoing)
        assert noticed == []  # stopped by the person: no notice
        app.on_event({"type": "proving.exam", "payload": {"exam": {**EXAM, "status": "done"}}})
        assert noticed == [("Экзамен закончен", "Claude Opus 5.5: сдано 1 из 2; Coder: сдано 0 из 2")]
        assert app.flashText == "Экзамен закончен — Claude Opus 5.5: сдано 1 из 2; Coder: сдано 0 из 2"
        wait_until(lambda: not app.bg.busy)

        view.showExam("e1")  # a past exam
        assert view.exam["id"] == "e1" and view.exams[1]["selected"] and shown[-1] == "e1"
        view.deleteExam()
        assert app.question["title"] == "Удалить экзамен?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and view.exam.get("id") == "e2")
        assert client.proving_calls[-1] == ("delete exam", "e1") and len(view.exams) == 1

        view.openRun("c1")  # «Ход работы» of a cell: the run's chat
        assert app.currentId == "c1"
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()
