"""A chat's goal (stage 11.1, D44, Q26): the chat works turn after turn until the hub's check passes - the criteria's
commands run in real hub terminals; the last turn at the soft limit and the goal parked after it; pause, steering,
resume (a parked goal with higher limits) and clear; a stopped turn pauses, a failed one blocks; criteria in words
wait for the judge; what a goal refuses; an active goal going on after a hub restart."""

import asyncio
import json
import threading

from hub import goals
from hub.auth import create_client
from hub.gateway import ProviderError
from hub.gateway.base import StreamEvent
from tests.test_autonomous import Scripted, use
from tests.test_changes import wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture

DONE = "if (Test-Path done.txt) { exit 0 } else { exit 1 }"
WRITE = [("write_file", {"project": "proj", "path": "done.txt", "content": "ok\n"})]
BOOM = "boom"  # a step of the script: the model fails


class Gated(Scripted):
    """Scripted, but each answer waits until its gate is open (or the user stops it)."""

    def __init__(self, name, script):
        super().__init__(name, script)
        self.gate = threading.Event()
        self.gate.set()
        self.waiting = threading.Event()
        self.last = []  # the messages of the call that waits now (or came last)

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.last = messages
        self.waiting.set()
        while not self.gate.is_set() and not cancel.is_set():
            await asyncio.sleep(0.02)
        self.waiting.clear()
        if cancel.is_set():
            self.prompts.append(messages)
            yield StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 2}, stop_reason="end_turn")
            return
        if self.script and self.script[0] == BOOM:
            self.script.pop(0)
            self.prompts.append(messages)
            raise ProviderError("broken: 503 the server is overloaded")
        async for event in super().stream_chat(model_ref, messages, effort, cancel, tools):
            yield event


def chat_with_project(hub, repo, provider, mode="trusted"):  # noqa: F811 - the fixture's name
    use(hub, provider)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    assert hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]}).status_code == 200
    assert hub.put(f"/v1/conversations/{conv}/context", json={"mode": mode}).status_code == 200
    return pid, conv


def set_goal(hub, conv, **fields):  # noqa: F811
    body = {"objective": "Создать done.txt", "criteria": [{"command": DONE}], "model_id": "agent/m", **fields}
    response = hub.put(f"/v1/conversations/{conv}/goal", json=body)
    assert response.status_code == 201, response.text
    return response.json()["goal"]


def goal(hub, conv):  # noqa: F811
    return hub.get(f"/v1/conversations/{conv}/goal").json()


def idle(hub, conv):  # noqa: F811
    return conv not in hub.get("/v1/conversations").json()["busy"]


def settled(hub, conv, *statuses):  # noqa: F811
    """The goal came to one of `statuses`, with no check and no turn going on."""
    return wait(lambda: (g := goal(hub, conv))["goal"]["status"] in statuses and not g["checking"]
                and idle(hub, conv) and g, timeout=90)  # fmt: skip


def hub_note(messages):
    """The goal's last word to the worker in these messages."""
    return next(m["content"] for m in reversed(messages)
                if m["role"] == "user" and m["content"].startswith("[LocalChat hub] <goal_loop"))  # fmt: skip


def test_a_goal_works_turn_after_turn_until_its_check_passes(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", ["Смотрю, с чего начать.", WRITE, "Записал done.txt."]))
    pid, conv = chat_with_project(hub, repo, worker)
    started = set_goal(hub, conv, constraints=["Не трогать README.md"])
    assert started["status"] == "active" and started["max_turns"] == 25 and started["max_minutes"] == 60
    assert started["criteria"][0]["project_id"] == pid and started["criteria"][0]["expected_exit_code"] == 0
    found = settled(hub, conv, "achieved")
    reached = found["goal"]
    assert reached["turns_used"] == 2 and reached["ended_at"] and reached["tokens_used"] == 21  # 3 calls x 7
    assert (repo / "done.txt").read_text(encoding="utf-8") == "ok\n"
    first, second = found["checks"]
    assert (first["route"], first["verdict"], first["turn"]) == ("continue", "INCOMPLETE", 1)
    assert first["results"][0]["exit_code"] == 1 and "ended with exit code 1 (expected 0)" in first["missing"][0]
    assert (second["route"], second["verdict"], second["results"][0]["met"]) == ("achieved", "ACHIEVED", True)
    assert reached["criteria"][0]["verified"] is True and reached["detail"].startswith("цель достигнута")
    start = hub_note(worker.prompts[0])
    assert start.startswith('[LocalChat hub] <goal_loop type="start">') and "GOAL: Создать done.txt" in start
    assert f"1. `{DONE}` in «proj» ends with exit code 0" in start and "- Не трогать README.md" in start
    going = hub_note(worker.prompts[1])
    assert 'type="continuation"' in going and "CHECK OF YOUR LAST TURN: not met." in going
    assert "ended with exit code 1 (expected 0)" in going and "Turn 2 of 25" in going
    stored = hub.get(f"/v1/conversations/{conv}").json()["messages"]
    assert sum(1 for m in stored if m["role"] == "user" and m["content"][0].get("hub")) == 2  # the notes are kept
    checks = [t for t in hub.get("/v1/terminals", params={"project_id": pid}).json()["terminals"]
              if t["created_by"] == "goal"]  # fmt: skip
    assert len(checks) == 2 and checks[0]["title"].startswith("Цель: if (Test-Path")


def test_the_last_turn_comes_at_the_soft_limit_and_then_the_goal_is_parked(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", ["Пробую.", "Сдаю дела: done.txt не создан.", "Снова пробую.", WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, limits={"max_turns": 2, "soft_ratio": 0.5})
    parked = settled(hub, conv, "budget")
    assert [c["route"] for c in parked["checks"]] == ["last_turn", "budget"]
    assert parked["goal"]["detail"] == "предел: ходов 2 из 2" and parked["goal"]["last_turn"] == 1
    last = hub_note(worker.prompts[1])
    assert 'type="budget_limit"' in last and "LAST turn" in last and "handover report" in last
    refused = hub.post(f"/v1/conversations/{conv}/goal/resume", json={})
    assert refused.status_code == 409 and "raise them" in refused.json()["detail"]
    resumed = hub.post(f"/v1/conversations/{conv}/goal/resume", json={"limits": {"max_turns": 6}})
    assert resumed.status_code == 202 and resumed.json()["goal"]["max_turns"] == 6
    done = settled(hub, conv, "achieved")
    assert [c["route"] for c in done["checks"]] == ["last_turn", "budget", "last_turn", "achieved"]
    assert done["goal"]["turns_used"] == 4 and "The user resumed the goal (app)." in hub_note(worker.prompts[2])


def test_pause_steer_resume_and_clear(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", ["Работаю.", "Учёл.", "Ещё."]))
    worker.gate.clear()
    _pid, conv = chat_with_project(hub, repo, worker)
    url = f"/v1/conversations/{conv}/goal"
    set_goal(hub, conv)
    wait(worker.waiting.is_set)
    paused = hub.post(f"{url}/pause").json()["goal"]
    assert paused["status"] == "paused" and paused["detail"] == "на паузе (app)"
    assert hub.post(f"{url}/pause").status_code == 409
    assert hub.post(f"{url}/steer", json={"text": "Только в папке src"}).json()["text"] == "Только в папке src"
    assert [s["text"] for s in goal(hub, conv)["steering"]] == ["Только в папке src"]
    worker.gate.set()
    held = settled(hub, conv, "paused")
    assert held["goal"]["turns_used"] == 1 and held["checks"] == []  # the turn counted; no check, no next turn
    assert len(worker.prompts) == 1
    worker.gate.clear()
    assert hub.post(f"{url}/resume").json()["goal"]["status"] == "active"
    wait(lambda: worker.waiting.is_set() and "resumed" in hub_note(worker.last))
    note = hub_note(worker.last)
    assert "The user resumed the goal (app)." in note and "THE USER ADDED WHILE YOU WORKED" in note
    assert "Только в папке src" in note and goal(hub, conv)["steering"] == []  # read: it is not given twice
    cleared = hub.delete(url).json()["goal"]
    assert cleared["status"] == "cleared" and cleared["ended_at"]
    worker.gate.set()
    wait(lambda: goal(hub, conv)["goal"]["turns_used"] == 2 and idle(hub, conv))
    after = goal(hub, conv)
    assert after["goal"]["status"] == "cleared" and len(worker.prompts) == 2  # no next turn
    assert hub.post(f"{url}/resume").status_code == 404  # a cleared goal is not open: a new one may be set


def test_a_stopped_turn_pauses_the_goal_and_a_failed_one_blocks_it(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", [BOOM]))
    worker.gate.clear()
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv)
    wait(worker.waiting.is_set)
    hub.post(f"/v1/conversations/{conv}/cancel")
    paused = settled(hub, conv, "paused")
    assert paused["checks"][-1]["route"] == "paused" and paused["goal"]["detail"] == "остановлено: ход прерван"
    worker.gate.set()
    hub.post(f"/v1/conversations/{conv}/goal/resume")
    blocked = settled(hub, conv, "blocked")
    assert blocked["goal"]["detail"].startswith("ход не удался") and "503" in blocked["goal"]["detail"]
    assert blocked["checks"][-1]["route"] == "blocked" and blocked["goal"]["turns_used"] == 2


def test_criteria_in_words_wait_for_the_judge(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", ["Сделал."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, criteria=[{"command": "exit 0"}, {"rule": "В README сказано, как запускать"}])
    blocked = settled(hub, conv, "blocked")
    check = blocked["checks"][-1]
    assert check["route"] == "blocked" and check["verdict"] is None
    assert "проверить некому" in blocked["goal"]["detail"] and "«Проверка цели»" in blocked["goal"]["detail"]
    assert [r["met"] for r in criteria(check)] == [True, None]
    assert check["missing"] == ["criterion 2 (in words, the judge checks it): В README сказано, как запускать"]


def criteria(check):
    return [r for r in check["results"] if r["kind"] in ("command", "semantic")]


def verdict(status, missing=(), feedback=""):
    return json.dumps({"status": status, "missing_items": list(missing), "feedback_for_worker": feedback},
                      ensure_ascii=False)  # fmt: skip


def test_the_judge_closes_a_goal_only_when_it_agrees(hub, repo):  # noqa: F811
    referee = use(hub, Scripted("judge", [verdict("INCOMPLETE", ["в README нет строки о done.txt"], "Допиши README"),
                                          verdict("ACHIEVED")]))  # fmt: skip
    readme = [("edit_file", {"project": "proj", "path": "README.md", "old_text": "# Проект",
                             "new_text": "# Проект\n\ndone.txt — признак готовности"})]  # fmt: skip
    worker = use(hub, Gated("agent", [WRITE, "Записал done.txt.", readme, "Дописал README."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    (repo / ".env").write_text("API_KEY=sk-live-secret-0123456789abcdef\n", encoding="utf-8")
    started = set_goal(hub, conv, judge_model_id="judge/m",
                       criteria=[{"command": DONE}, {"rule": "В README сказано, что значит done.txt"}])  # fmt: skip
    assert started["judge_model_id"] == "judge/m"
    done = settled(hub, conv, "achieved")
    first, second = done["checks"]
    assert (first["route"], first["verdict"]) == ("continue", "INCOMPLETE")
    assert first["missing"] == ["criterion 2 (in words, the judge checks it): В README сказано, что значит done.txt",
                                "в README нет строки о done.txt"]  # fmt: skip
    assert next(r for r in first["results"] if r["kind"] == "judge")["feedback"] == "Допиши README"
    changed = next(r for r in second["results"] if r["kind"] == "changes")["projects"][0]
    assert changed["how"] == "git" and {f["path"] for f in changed["files"]} == {"done.txt", "README.md"}
    assert (second["verdict"], done["goal"]["detail"]) == ("ACHIEVED", "цель достигнута: проверки прошли, "
                                                           "проверяющая модель согласна")  # fmt: skip
    assert all(c["verified"] for c in done["goal"]["criteria"])
    going = hub_note(worker.prompts[2])
    assert "THE JUDGE SAYS: Допиши README" in going and "- в README нет строки о done.txt" in going
    asked = referee.prompts[0][1]["content"]
    assert "OBJECTIVE: Создать done.txt" in asked and "the hub ran it: met" in asked and "+ok" in asked
    assert '<worker_claims unverified="true">\nЗаписал done.txt.' in asked and "sk-live-secret" not in asked
    assert "added done.txt" in asked and ".env" not in asked
    assert done["goal"]["tokens_used"] == 4 * 7 + 2 * 7  # the worker's four calls and the judge's two


def test_lost_tests_are_regressed_without_the_judge_and_twice_stop_the_goal(hub, repo):  # noqa: F811
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_one():\n    assert False\n", encoding="utf-8", newline="\n")
    referee = use(hub, Scripted("judge", [verdict("ACHIEVED")]))
    gone = [("write_file", {"project": "proj", "path": "tests/test_app.py", "content": "# пусто\n"})]
    worker = use(hub, Gated("agent", [gone, "Убрал падающий тест.", "Ничего не менял."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, judge_model_id="judge/m", criteria=[{"command": "exit 0"}])
    blocked = settled(hub, conv, "blocked")
    assert [c["verdict"] for c in blocked["checks"]] == ["REGRESSED", "REGRESSED"] and referee.prompts == []
    assert "tests/test_app.py: test(s) removed - test_one" in blocked["checks"][0]["missing"]
    assert blocked["goal"]["detail"].startswith("второй раз подряд потеряно то, что работало")
    note = hub_note(worker.prompts[2])
    assert "CHECK OF YOUR LAST TURN: REGRESSED" in note and "Put them back" in note


def test_a_file_that_does_not_parse_goes_back_before_the_judge(hub, repo):  # noqa: F811
    referee = use(hub, Scripted("judge", [verdict("ACHIEVED")]))
    broken = [("write_file", {"project": "proj", "path": "src/app.py", "content": "def f(:\n    pass\n"})]
    fixed = [("write_file", {"project": "proj", "path": "src/app.py", "content": "def f():\n    pass\n"})]
    worker = use(hub, Gated("agent", [broken, "Написал f.", fixed, "Поправил."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, judge_model_id="judge/m", criteria=[{"command": "exit 0"}])
    done = settled(hub, conv, "achieved")
    first = done["checks"][0]
    assert first["verdict"] == "INCOMPLETE" and first["missing"] == ["proj/src/app.py, line 1: does not parse "
                                                                     "(invalid syntax)"]  # fmt: skip
    assert len(referee.prompts) == 1  # asked only once the file parsed


def test_a_model_drafts_the_contract_from_the_wish(hub, repo):  # noqa: F811
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8")
    answer = json.dumps(
        {
            "objective": "Тесты проходят",
            "criteria": [
                {"command": "pytest -q", "project": "PROJ", "expected_exit_code": 0},
                {"command": "git push origin main", "project": "proj"},
                {"rule": "README говорит, как запускать"},
            ],
            "constraints": ["не трогать .github"],
            "limits": {"max_turns": 999, "max_minutes": 30},
        },
        ensure_ascii=False,
    )
    worker = use(hub, Gated("agent", ["Вот договор: " + answer]))
    _pid, conv = chat_with_project(hub, repo, worker)
    url = f"/v1/conversations/{conv}/goal/draft"
    found = hub.post(url, json={"text": "сделай, чтобы тесты проходили", "model_id": "agent/m"}).json()
    assert found["model_id"] == "agent/m"  # no model for «Проверка цели»: the chat's own drafts
    assert found["draft"] == {
        "objective": "Тесты проходят", "constraints": ["не трогать .github"], "limits": {"max_turns": 500,
                                                                                       "max_minutes": 30},
        "criteria": [{"kind": "command", "command": "pytest -q", "project": "proj", "expected_exit_code": 0},
                     {"kind": "semantic", "rule": "README говорит, как запускать"}]}  # fmt: skip
    assert found["notes"] == ["команда «git push origin main» убрана: проверка только смотрит"]
    asked = worker.prompts[0][1]["content"]
    assert '<project name="proj">' in asked and "TOP LEVEL:" in asked and "pyproject.toml:\n[tool.pytest" in asked
    assert "<wish>\nсделай, чтобы тесты проходили\n</wish>" in asked
    assert worker.prompts[0][0]["content"].startswith("You turn what a user wants into a goal contract")
    referee = use(hub, Scripted("judge", ["не JSON", "опять не JSON"]))
    hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": {"judge": "judge/m"}}})
    failed = hub.post(url, json={"text": "ещё раз", "model_id": "agent/m"})
    assert failed.status_code == 422 and "the draft did not come" in failed.json()["detail"]
    assert len(referee.prompts) == 2  # the model of «Проверка цели» drafts now; asked once more, then given up
    metas = hub.app.state.db.call(lambda c: [json.loads(r[0])["purpose"] for r in c.execute(
        "SELECT meta FROM usage WHERE model_id IN ('agent/m', 'judge/m')")])  # fmt: skip
    assert metas.count("goal_draft") == 3


def test_a_project_without_git_shows_the_hubs_own_writes(hub, tmp_path):  # noqa: F811
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "README.md").write_text("# Без git\n", encoding="utf-8")
    referee = use(hub, Scripted("judge", [verdict("ACHIEVED")]))
    worker = use(hub, Gated("agent", [[("write_file", {"project": "plain", "path": "done.txt", "content": "ok\n"})],
                                      "Записал."]))  # fmt: skip
    _pid, conv = chat_with_project(hub, folder, worker)
    set_goal(hub, conv, judge_model_id="judge/m", criteria=[{"command": DONE}])
    settled(hub, conv, "achieved")
    asked = referee.prompts[0][1]["content"]
    assert "CHANGED (the hub's own writes): added done.txt" in asked and "+ok" in asked
    assert "«plain»: not a git repository - only the hub's own writes are listed" in asked


def test_what_a_goal_refuses(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", ["Работаю."]))
    worker.gate.clear()
    _pid, conv = chat_with_project(hub, repo, worker)
    url = f"/v1/conversations/{conv}/goal"
    base = {"objective": "x", "model_id": "agent/m", "criteria": [{"command": "exit 0"}]}

    def refused(body, code=422):
        response = hub.put(url, json={**base, **body})
        assert response.status_code == code, response.text
        return str(response.json()["detail"])

    refused({"criteria": []})  # at least one
    assert "changes things or goes to the network" in refused({"criteria": [{"command": "git push origin main"}]})
    assert "changes things or goes to the network" in refused({"criteria": [{"command": "pip install requests"}]})
    assert "name the project" in refused({"criteria": [{"command": "exit 0", "project": "чужой"}]})
    assert "say the rule" in refused({"criteria": [{"kind": "semantic"}]})
    assert "max_turns" in refused({"limits": {"max_turns": 0}})
    assert "soft_ratio" in refused({"limits": {"soft_ratio": 0.1}})
    assert "choose the model" in refused({"model_id": "nope/x"})
    assert hub.put("/v1/conversations/nope/goal", json=base).status_code == 404
    token = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.put(url, json=base, headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert hub.get(url, headers={"Authorization": f"Bearer {token}"}).status_code == 404  # not its chat (D46)
    assert hub.post(f"{url}/pause").status_code == 404  # no goal yet
    set_goal(hub, conv, **base)
    wait(worker.waiting.is_set)
    assert "answering now" in refused({}, 409)
    hub.delete(url)
    worker.gate.set()
    wait(lambda: idle(hub, conv) and not goal(hub, conv)["checking"])
    set_goal(hub, conv, **base)  # the cleared one is history: a new goal may be set
    settled(hub, conv, "achieved")
    set_goal(hub, conv, **{**base, "criteria": [{"rule": "всё понятно"}]})  # stops for the human: still open
    settled(hub, conv, "blocked")
    assert "has a goal already" in refused({}, 409)
    assert len([g for g in hub.app.state.db.call(lambda c: c.execute("SELECT status FROM goals").fetchall())]) == 3


def test_an_active_goal_goes_on_after_a_restart(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", [WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker)

    def left_at_work(conn):  # what the last hub left: an active goal three turns in, no turn running
        contract = goals.check_contract(conn, conv, {"objective": "Создать done.txt", "model_id": "agent/m",
                                                     "criteria": [{"command": DONE, "project": "proj"}]})  # fmt: skip
        goal_id = goals.create_goal(conn, conv, contract, by="app")
        conn.execute("UPDATE goals SET turns_used = 3 WHERE id = ?", (goal_id,))

    hub.app.state.db.call(left_at_work)
    assert hub.portal.call(hub.app.state.goals.start) == [conv]
    done = settled(hub, conv, "achieved")
    note = hub_note(worker.prompts[0])
    assert "The hub restarted" in note and "Turn 4 of 25" in note and done["goal"]["turns_used"] == 4


def test_a_goal_cut_off_in_its_first_turn_gets_its_contract_again(hub, repo):  # noqa: F811
    worker = use(hub, Gated("agent", [WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker)

    def cut_off(conn):  # the last hub died in the first turn: nothing counted, the user's word waits
        contract = goals.check_contract(conn, conv, {"objective": "Создать done.txt", "model_id": "agent/m",
                                                     "criteria": [{"command": DONE}]})  # fmt: skip
        goal_id = goals.create_goal(conn, conv, contract, by="app")
        conn.execute("INSERT INTO goal_steering (goal_id, text, by) VALUES (?, 'Файл — в корне', 'app')", (goal_id,))

    hub.app.state.db.call(cut_off)
    hub.portal.call(hub.app.state.goals.start)
    settled(hub, conv, "achieved")
    note = hub_note(worker.prompts[0])
    assert note.startswith('[LocalChat hub] <goal_loop type="start">\nThe hub restarted')
    assert "GOAL: Создать done.txt" in note and "THE USER ADDED:\nФайл — в корне" in note
