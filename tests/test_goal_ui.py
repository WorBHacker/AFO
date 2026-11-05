"""A chat's goal in the window (stage 11.3, D44): the bar over the composer and what it says, the contract card -
drafted by a model, checked, set -, «/цель …», the composer's words going to a working goal, the hub's notes to the
model as quiet lines, the notices of a goal met or waiting."""

import copy

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.ui import goal_view, rows
from common.paths import data_paths
from tests.test_ui import FakeClient, opened, qapp, wait_until  # noqa: F401 - the qapp fixture

GOAL = {"id": "g1", "conversation_id": "c1", "objective": "Тесты проходят", "status": "active", "detail": "",
        "turns_used": 3, "max_turns": 25, "tokens_used": 12_400, "max_tokens": 500_000, "seconds_used": 250.0,
        "max_minutes": 60, "max_seconds": 3600, "soft_ratio": 0.88, "cost_usd": 0.0, "judge_model_id": None,
        "criteria": [{"n": 1, "kind": "command", "command": "pytest -q", "project_name": "LocalChat",
                      "expected_exit_code": 0, "verified": False},
                     {"n": 2, "kind": "semantic", "rule": "README описывает запуск", "verified": False}]}  # fmt: skip
STAGES = [{"n": 1, "title": "Починить тесты", "status": "done", "summary": "поправлен test_app.py", "turn": 2},
          {"n": 2, "title": "Обновить README", "status": "pending", "summary": "", "turn": None},
          {"n": 3, "title": "Проверить запуск", "status": "pending", "summary": "", "turn": None}]  # fmt: skip
CHECK = {"id": 5, "turn": 3, "route": "continue", "verdict": "INCOMPLETE", "detail": "",
         "missing": ["criterion 1: `pytest -q` ended with exit code 1 (expected 0)"],
         "results": [{"kind": "judge", "status": "INCOMPLETE", "feedback": "Допиши тест", "missing": []}]}  # fmt: skip
DRAFT = {"draft": {"objective": "Тесты в src проходят", "constraints": ["не трогать migrations"],
                   "limits": {"max_turns": 12}, "criteria": [
                       {"kind": "command", "command": "pytest -q", "project": "LocalChat", "expected_exit_code": 0},
                       {"kind": "semantic", "rule": "README описывает запуск"}]},
         "notes": ["команда «git push» убрана: проверка только смотрит"], "model_id": "llama-swap/coder"}  # fmt: skip


class GoalClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.goal_data = {"goal": None, "checks": [], "steering": [], "checking": False}
        self.goal_calls = []

    def goal(self, cid):
        return copy.deepcopy(self.goal_data) if cid == "c1" or self.goal_data["goal"] else {"goal": None}

    def set_goal(self, cid, contract):
        self.goal_calls.append(("set", cid, contract))
        goal = {**GOAL, "conversation_id": cid, "objective": contract["objective"], "turns_used": 0}
        self.goal_data = {"goal": goal, "checks": [], "steering": []}
        return goal

    def draft_goal(self, cid, text, model_id):
        self.goal_calls.append(("draft", cid, text, model_id))
        return copy.deepcopy(DRAFT)

    def _status(self, status):
        self.goal_data["goal"]["status"] = status
        return self.goal_data["goal"]

    def pause_goal(self, cid):
        self.goal_calls.append(("pause", cid))
        return self._status("paused")

    def set_milestones(self, cid, titles):
        self.goal_calls.append(("stages", cid, titles))
        done = [m for m in self.goal_data["goal"].get("milestones") or [] if m["status"] == "done"]
        self.goal_data["goal"]["milestones"] = done + [
            {"n": len(done) + n, "title": t, "status": "pending", "summary": "", "turn": None}
            for n, t in enumerate(titles, 1)]  # fmt: skip
        return self.goal_data["goal"]

    def resume_goal(self, cid, limits=None):
        self.goal_calls.append(("resume", cid, limits))
        return self._status("active")

    def clear_goal(self, cid):
        self.goal_calls.append(("clear", cid))
        return self._status("cleared")

    def steer_goal(self, cid, text):
        self.goal_calls.append(("steer", cid, text))
        return {"id": 1, "text": text}


def test_a_goals_stages_in_the_window():
    """11.5: the stages with a tick and the one at work; the card's stages - a line each; the pending ones edited."""
    rows_ = goal_view.stage_rows({**GOAL, "milestones": STAGES})
    assert [(r["text"], r["done"], r["current"]) for r in rows_] == [
        ("1. Починить тесты", True, False),
        ("2. Обновить README", False, True),
        ("3. Проверить запуск", False, False),
    ]
    assert rows_[0]["summary"] == "поправлен test_app.py" and goal_view.stage_rows(GOAL) == []
    assert goal_view.pending_stages({**GOAL, "milestones": STAGES}) == "Обновить README\nПроверить запуск"
    editor = {**goal_view.blank_editor(), "objective": "x", "milestones": "Первое\n\n Второе ",
              "criteria": [{"kind": "semantic", "rule": "всё хорошо"}]}  # fmt: skip
    contract, error = goal_view.contract_of(editor, "agent/m", "medium")
    assert not error and contract["milestones"] == ["Первое", "Второе"]
    drafted = goal_view.editor_of_draft(goal_view.blank_editor(), {"draft": {"objective": "y", "milestones": [
        "Раз", "Два"]}})  # fmt: skip
    assert drafted["milestones"] == "Раз\nДва"


def test_what_the_goal_says_in_words():
    assert goal_view.progress_text(GOAL) == "ход 3 из 25 · 12 тыс. из 500 тыс. токенов · 4 из 60 мин"
    assert goal_view.thousands(1_500_000) == "1,5 млн" and goal_view.thousands(512) == "512"
    assert goal_view.check_text(CHECK) == "проверка после хода 3: не выполнено — 1 пункт"
    assert goal_view.check_text(None) == "проверка — после первого хода"
    looked = {**CHECK, "route": "investigate"}  # 11.4: the next turn looks for the cause
    assert goal_view.check_text(looked) == ("проверка после хода 3: не выполнено — 1 пункт · следующий ход — поиск "
                                            "причины, без записи")  # fmt: skip
    assert goal_view.check_text({**CHECK, "route": "investigated", "verdict": None, "missing": []}) == (
        "проверка после хода 3: причину искали — дальше правка")  # fmt: skip
    assert goal_view.check_text({**CHECK, "route": "rolled_back", "verdict": None, "missing": []}) == (
        "проверка после хода 3: файлы возвращены к точке сохранения")  # fmt: skip
    assert goal_view.criteria_rows(GOAL) == [
        {"text": "`pytest -q` в «LocalChat» → код 0", "met": False, "words": False},
        {"text": "README описывает запуск", "met": False, "words": True}]  # fmt: skip
    assert goal_view.judge_word(CHECK) == "Допиши тест"
    editor = goal_view.editor_of_draft({**goal_view.blank_editor(), "wish": "сделай тесты"}, DRAFT)
    assert editor["objective"] == "Тесты в src проходят" and editor["turns"] == "12" and editor["minutes"] == "60"
    assert editor["criteria"][0] == {"kind": "command", "command": "pytest -q", "project": "LocalChat", "code": "0",
                                     "rule": ""}  # fmt: skip
    contract, wrong = goal_view.contract_of(editor, "m/1", "high")
    assert wrong == "" and contract == {
        "objective": "Тесты в src проходят", "model_id": "m/1", "effort": "high",
        "constraints": ["не трогать migrations"],
        "limits": {"max_turns": 12, "max_tokens": 500_000, "max_minutes": 60},
        "criteria": [{"kind": "command", "command": "pytest -q", "project": "LocalChat", "expected_exit_code": 0},
                     {"kind": "semantic", "rule": "README описывает запуск"}]}  # fmt: skip
    assert goal_view.contract_of({**editor, "objective": "", "wish": ""}, "m", "low")[1] == "Напишите, чего добиться"
    assert "хотя бы один критерий" in goal_view.contract_of({**editor, "criteria": []}, "m", "low")[1]
    bad = {**editor, "criteria": [{**editor["criteria"][0], "code": "ноль"}]}
    assert goal_view.contract_of(bad, "m", "low")[1] == "Критерий 1: код выхода — целое число"
    assert goal_view.contract_of({**editor, "judge": "j/1"}, "m", "low")[0]["judge_model_id"] == "j/1"
    assert goal_view.contract_of({**editor, "strong": "s/1"}, "m", "low")[0]["strong_model_id"] == "s/1"  # 11.7
    assert "strong_model_id" not in goal_view.contract_of(editor, "m", "low")[0]
    models = {"m/1": {"name": "Рабочая"}, "s/1": {"name": "Сильная", "locality": "cloud"},
              "claude-code/default": {"name": "Claude Code", "provider": "claude-code"},
              "off/1": {"name": "Выключена", "enabled": False}}  # fmt: skip
    items = goal_view.strong_items(models, "m/1", "s/1")
    assert [(i["id"], i["name"], i["selected"]) for i in items] == [("", "Нет — все ходы ведёт «Рабочая»", False),
                                                                    ("s/1", "Сильная", True)]  # fmt: skip
    assert items[1]["icon"] == "cloud"
    assert goal_view.strong_name(models, "m/1", "") == "нет"
    assert goal_view.strong_name(models, "m/1", "s/1") == "Сильная"
    assert goal_view.cost_text({"prices": {}}, "500") == "Цена: бесплатно — модель локальная"
    assert goal_view.cost_text({"prices": {"input": 4.0, "output": 20.0}}, "500").startswith("Цена: до ≈ $3,20")


def test_the_hubs_notes_are_quiet_lines():
    note = {"id": 7, "role": "user", "content": [{"type": "text", "hub": True, "text":
            '[AFO hub] <goal_loop type="continuation">\nCHECK OF YOUR LAST TURN: not met.\n- criterion 1: x\n'
            "- criterion 2: y\nTHE JUDGE SAYS: z\nTHE USER ADDED WHILE YOU WORKED - it comes first:\n"
            "только src\n"}]}  # fmt: skip
    nudge = {"id": 8, "role": "user", "content": [{"type": "text", "hub": True, "text": "[AFO hub] Go on."}]}
    mine = {"id": 9, "role": "user", "content": [{"type": "text", "text": "привет"}]}
    start = '[AFO hub] <goal_loop type="start">\nGOAL: x\n'
    planned = {"id": 10, "role": "user", "content": [{"type": "text", "hub": True, "text": start,
                                                      "worker": {"model_id": "s/1", "why": "plan"}}]}  # fmt: skip
    plain = {"id": 11, "role": "user", "content": [{**planned["content"][0], "worker": {"model_id": "m/1", "why": ""}}]}
    models = {"s/1": {"name": "Сильная"}, "m/1": {"name": "Рабочая"}}
    found = rows.rows_for([note, nudge, mine, planned, plain], models)
    assert [r["role"] for r in found] == ["hub", "hub", "user", "hub", "hub"]
    assert found[3]["note"] == "ход ведёт «Сильная» — план" and found[4]["note"] == "ход ведёт «Рабочая»"  # 11.7
    assert found[0]["title"] == "Хаб → модели: продолжение цели" and found[0]["goal"] is True
    assert found[0]["note"] == "не выполнено: 2 · слово проверяющей модели · ваше уточнение"
    assert found[1]["title"] == "Хаб → модели: напоминание" and rows.restyled(found[0]) == found[0]


def test_the_goal_in_the_window(qapp):  # noqa: F811
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = GoalClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        view = app.goal_view
        bar = quick.window.findChild(QObject, "goalBar")
        chip = quick.window.findChild(QObject, "goalChip")
        assert not view.shown and not bar.property("visible") and chip.property("text") == "Цель"
        noticed = []
        view.noticed.connect(lambda title, text: noticed.append((title, text)))

        client.goal_data = {"goal": copy.deepcopy(GOAL), "checks": [CHECK], "steering": [], "checking": False}
        app.on_event({"type": "goal.check", "payload": {"conversation_id": "c1", "goal_id": "g1"}})
        wait_until(lambda: not app.bg.busy and view.shown)
        assert bar.property("visible") and chip.property("text") == "Цель: идёт" and view.steering
        assert view.lastCheck.startswith("проверка после хода 3") and view.judgeWord == "Допиши тест"
        assert view.missing == ["criterion 1: `pytest -q` ended with exit code 1 (expected 0)"]
        app.on_event({"type": "goal.judging", "payload": {"conversation_id": "c1"}})
        assert view.statusText == "проверяет модель"

        sent = len(client.sent)
        app.send("Только в папке src")  # the goal works: a word for its next turn, not a message
        wait_until(lambda: not app.bg.busy)
        assert client.goal_calls[-1] == ("steer", "c1", "Только в папке src") and len(client.sent) == sent
        assert app.flashText.startswith("Уточнение передано")

        view.pause()
        wait_until(lambda: not app.bg.busy and view.status == "paused")
        assert not view.steering and quick.window.findChild(QObject, "goalResume").property("visible")
        client.goal_data["goal"]["status"] = "budget"
        view.load()
        wait_until(lambda: not app.bg.busy and view.status == "budget")
        view.resume()  # at its limit: higher limits go with it
        wait_until(lambda: not app.bg.busy and view.status == "active")
        assert client.goal_calls[-1] == ("resume", "c1", {"max_turns": 25, "max_tokens": 500_000, "max_minutes": 60})
        client.goal_data["goal"].update(status="budget", turns_used=25, tokens_used=400_000, seconds_used=600)
        view.load()
        wait_until(lambda: not app.bg.busy and view.status == "budget")
        view.resume()
        wait_until(lambda: not app.bg.busy)
        assert client.goal_calls[-1] == ("resume", "c1", {"max_turns": 37, "max_tokens": 601_000, "max_minutes": 60})

        other = {"conversation_id": "c9", "status": "achieved", "detail": "цель достигнута",
                 "goal": {"objective": "Другая"}}  # fmt: skip
        app.on_event({"type": "goal.status", "payload": {**other, "conversation_id": "c8", "proving": True}})
        app.on_event({"type": "goal.status", "payload": other})  # a run of the proving ground above: no notice
        assert noticed == [("Цель достигнута", "Другая\nцель достигнута")]
        assert app.flashText == "Цель достигнута: «Другая»"

        # the stages (11.5): in the bar, the pending ones edited there
        client.goal_data["goal"] = {**client.goal_data["goal"], "status": "active", "milestones": copy.deepcopy(STAGES)}
        view.load()
        wait_until(lambda: not app.bg.busy and len(view.stages) == 3)
        assert view.pendingStages == "Обновить README\nПроверить запуск"
        view.saveStages("Обновить README\n\nСобрать установщик\n")
        wait_until(lambda: not app.bg.busy and [s["text"] for s in view.stages][-1] == "3. Собрать установщик")
        assert client.goal_calls[-1] == ("stages", "c1", ["Обновить README", "Собрать установщик"])
        assert app.flashText.startswith("Этапы изменены")

        view.clear()
        assert app.question["title"] == "Снять цель?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and not view.shown)
        assert client.goal_calls[-1] == ("clear", "c1")

        # «/цель …»: the card opens with the words; a model drafts; the contract is set
        sheet = quick.window.findChild(QObject, "goalSheet")
        app.send("/цель сделай, чтобы тесты проходили")
        wait_until(lambda: sheet.property("opened"))
        assert view.editor["wish"] == "сделай, чтобы тесты проходили" and not view.editor["drafted"]
        view.draft()
        wait_until(lambda: not app.bg.busy and view.editor["drafted"])
        assert client.goal_calls[-1] == ("draft", "c1", "сделай, чтобы тесты проходили", app.modelId)
        assert view.editor["notes"] == ["команда «git push» убрана: проверка только смотрит"]
        view.setCriterion(0, "code", "ноль")
        view.start()
        assert view.error == "Критерий 1: код выхода — целое число" and sheet.property("opened")  # said, not sent
        view.setCriterion(0, "code", "0")
        view.addCriterion("semantic")
        view.setCriterion(2, "rule", "сборка без предупреждений")
        view.setField("tokens", "200")
        view.setField("milestones", "Тесты\nREADME")
        strong = next(i for i in view.strongItems if i["id"])  # 11.7: another model for the plan and the hard places
        assert quick.window.findChild(QObject, "goalStrong").property("visible") and view.strongName == "нет"
        view.setField("strong", strong["id"])
        assert view.strongName == strong["name"] and strong["id"] != app.modelId
        view.start()
        wait_until(lambda: not app.bg.busy and not sheet.property("opened"))
        _kind, cid, contract = client.goal_calls[-1]
        assert cid == "c1" and contract["model_id"] == app.modelId and contract["limits"]["max_tokens"] == 200_000
        assert contract["milestones"] == ["Тесты", "README"] and contract["strong_model_id"] == strong["id"]
        rules = [c.get("rule") for c in contract["criteria"]][1:]
        assert rules == ["README описывает запуск", "сборка без предупреждений"]
        wait_until(lambda: view.shown and view.status == "active")
        assert app.flashText.startswith("Цель поставлена")

        # a new chat: the goal's card makes it first
        app.newChat()
        view.openEditor("новая цель")
        view.draft()
        wait_until(lambda: not app.bg.busy and view.editor["drafted"])
        assert client.goal_calls[-1][:2] == ("draft", "c2") and app.currentId == "c2"
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()
