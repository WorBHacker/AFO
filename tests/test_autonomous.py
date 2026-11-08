"""The autonomous agent (stage 9.1, D40, Q22): a goal, a plan as data with versions, steps that need their check,
failures that call for another way, the user approving the plan (or each step), a question and its answer, nudges
when the model stops before its report, a pause, the user's own plan, the planner handing over to the run's model."""

import json
import sqlite3
import sys
import time

import pytest

from hub import autonomous, store
from hub.db import Database, available_migrations, migrate
from hub.gateway.base import Model, StreamEvent
from tests.test_changes import wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture


class Scripted:
    """A model that answers as scripted: a list of tool calls, or a text (it stops). Remembers what it was sent."""

    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.prompts = []
        self.models = [Model(f"{name}/m", name, "m", name.title(), "local", {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.prompts.append(messages)
        step = self.script.pop(0) if self.script else "Готово."
        if isinstance(step, str):
            yield StreamEvent("content", step)
            yield StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 2}, stop_reason="end_turn")
            return
        yield StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 2}, stop_reason="tool_use",
                          tool_calls=[{"id": f"c{len(self.prompts)}{n}", "name": c[0], "input": c[1]}
                                      for n, c in enumerate(step)])  # fmt: skip


def use(hub, provider):  # noqa: F811
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    return provider


def start(hub, **fields):  # noqa: F811
    body = {"goal": "Посчитать строки в README", "done_when": "число строк названо", "model_id": "agent/m",
            "autonomy": "free", "project_ids": [], **fields}  # fmt: skip
    response = hub.post("/v1/runs/autonomous", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ended(hub, run_id):  # noqa: F811
    return wait(lambda: (r := hub.get(f"/v1/runs/{run_id}").json())["status"] in ("done", "error", "cancelled") and r)


def results(provider):
    """What the hub answered each tool call, in order."""
    out = []
    for prompt in provider.prompts:
        for turn in prompt:
            if turn["role"] == "tool" and (turn["tool_call_id"], turn["content"]) not in out:
                out.append((turn["tool_call_id"], turn["content"]))
    return [content for _id, content in out]


PLAN = ("set_plan", {"steps": [{"title": "Прочитать README", "check": "файл прочитан"},
                               {"title": "Посчитать строки", "check": "число сверено"}]})  # fmt: skip


def step(n, status, **more):
    return ("update_step", {"step": n, "status": status, **more})


def test_a_cloud_model_never_gets_a_run_with_a_local_only_project(hub, repo):  # noqa: F811
    """The audit (D46) feared a local planner would read a local-only project and hand it to a cloud run model: the
    run is refused before anything is read (D10) - the handover checks again all the same."""
    project = hub.post("/v1/projects", json={"path": str(repo)}).json()  # local-only, as every new project
    planner = use(hub, Scripted("planner", [[PLAN]]))
    cloudy = Scripted("cloudy", [[step(1, "running")]])
    cloudy.models = [Model("cloudy/m", "cloudy", "m", "Cloudy", "cloud", {}, {})]
    use(hub, cloudy)
    body = {"goal": "x", "model_id": "cloudy/m", "planner_model_id": "planner/m", "project_ids": [project["id"]]}
    refused = hub.post("/v1/runs/autonomous", json=body)
    assert refused.status_code == 422 and "only local models may see it" in refused.json()["detail"]
    assert planner.prompts == [] and cloudy.prompts == []


def test_a_cloud_model_sees_other_programs_windows_only_in_a_task_that_lets_it(hub, monkeypatch):  # noqa: F811
    """Q20 as the user decided it (22.3): a cloud model reads other programs' windows only in a task where that is
    turned on. The switch in «Папки и сайты для агента» is what a new task starts with: turned on later, it does not
    open the windows to a task that began without it."""
    use(hub, Scripted("agent", []))
    computer = hub.app.state.computer

    async def look(run_id, args):
        return "Window 7 «Заметки» - Notes; [3] edit «Текст» value «план на завтра»"

    async def cloud_looks(run_id):
        return await computer.handle(run_id, {"name": "screen_look", "input": {"window": 7}}, None, None, cloud=True)

    monkeypatch.setattr(computer, "_look", look)
    closed = start(hub)
    assert ended(hub, closed)["screen_cloud"] == 0
    output, error = hub.portal.call(cloud_looks, closed)
    assert error and "in this task" in output
    let = start(hub, screen_cloud=True)
    assert ended(hub, let)["screen_cloud"] == 1
    output, error = hub.portal.call(cloud_looks, let)
    assert not error and "план на завтра" in output

    assert hub.put("/v1/agent/access", json={"screen_cloud": True}).status_code == 200
    assert hub.portal.call(cloud_looks, closed)[1] is True  # began without it: stays so
    assert ended(hub, start(hub))["screen_cloud"] == 1  # a new task starts with the switch
    assert ended(hub, start(hub, screen_cloud=False))["screen_cloud"] == 0  # unless it says otherwise


def test_a_goal_planned_carried_out_checked_and_reported(hub):  # noqa: F811
    agent = use(hub, Scripted("agent", [
        [PLAN],
        [step(1, "running")],
        [step(1, "done", result="прочитан")],  # no check: refused
        [step(1, "done", result="прочитан", check="read_file вернул 12 строк")],
        [step(2, "failed")],  # no why: refused
        [step(2, "failed", result="сбился")], [step(2, "failed", result="снова")], [step(2, "failed", result="опять")],
        "Похоже, всё.",  # stops before its report: nudged
        [("set_plan", {"steps": ["Пересчитать"]})],  # a new plan without a reason: refused
        [("set_plan", {"steps": [{"title": "Пересчитать", "check": "два раза одно число"}], "reason": "сбивался"})],
        [step(1, "running")], [step(1, "done", result="12", check="посчитал дважды: 12 и 12")],
        [("finish", {"summary": "В README 12 строк.", "goal_met": True, "done": ["посчитал"], "files": []})],
        "В README 12 строк.",
    ]))  # fmt: skip
    run_id = start(hub)
    run = ended(hub, run_id)
    assert run["status"] == "done" and run["kind"] == "autonomous" and run["report"]["goal_met"] is True
    said = results(agent)
    assert said[0].startswith("Plan version 1 saved. Start step 1 «Прочитать README»")
    assert "say how you checked it" in said[2] and "Next: step 2 «Посчитать строки»" in said[3]
    assert "say why it failed" in said[4] and "failed (1 of 3)" in said[5]
    assert "failed 3 times" in said[7] and "change the plan" in said[7]
    assert "needs its reason" in said[8] and said[9].startswith("Plan version 2 saved")
    assert "Every step is done" in said[11] and said[12].startswith("Report saved")
    nudged = [t for t in agent.prompts[9] if t["role"] == "user" and t["content"].startswith("[AFO hub]")]
    assert "steps left - 2. Посчитать строки (failed)" in nudged[0]["content"]  # told what is left
    first = agent.prompts[0]
    assert first[0]["content"] == "Цель: Посчитать строки в README\nГотово, когда: число строк названо"
    assert "You work on your own toward a goal" in first[1]["content"] and "Autonomy: you go on" in first[1]["content"]
    sent = [t["name"] for t in hub.app.state.db.call(lambda c: __import__("json").loads(
        c.execute("SELECT tools FROM conversations WHERE id = ?", (run_id,)).fetchone()[0]))]  # fmt: skip
    at = sent.index("set_plan")  # its own tools, together, after the profile's
    assert sent[at : at + 6] == ["set_plan", "update_step", "finish", "note", "remember", "recall"]
    assert sent.count("ask_user") == 1  # the chat's and the agent's are one definition (2026-10-28)
    plan = hub.get(f"/v1/runs/{run_id}/plan").json()
    assert [p["version"] for p in plan["plans"]] == [1, 2] and plan["plans"][1]["reason"] == "сбивался"
    assert [(s["status"], s["attempts"]) for s in plan["plans"][0]["steps"]] == [("done", 0), ("failed", 3)]
    assert plan["plans"][1]["steps"][0]["note"] == "12 · проверка: посчитал дважды: 12 и 12"


def test_a_model_that_never_reports_is_nudged_only_so_often(hub):  # noqa: F811
    use(hub, Scripted("agent", ["Думаю."] * 6))
    run = ended(hub, start(hub))
    messages = hub.get(f"/v1/conversations/{run['id']}").json()["messages"]
    notes = [m for m in messages if m["role"] == "user" and m["content"][0].get("hub")]
    assert len(notes) == autonomous.MAX_NUDGES and "not made a plan" in notes[0]["content"][0]["text"]
    assert run["status"] == "done" and run["detail"] == "закончил без отчёта" and run["report"] is None


def test_the_user_approves_the_plan_and_answers_a_question(hub):  # noqa: F811
    agent = use(hub, Scripted("agent", [
        [PLAN], [PLAN],
        [("ask_user", {"question": "Считать пустые строки?"})],
        [("finish", {"summary": "Спросил и посчитал.", "goal_met": True})],
    ]))  # fmt: skip
    run_id = start(hub, autonomy="plan")
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "set_plan" and asked["conversation_id"] == run_id
    assert asked["preview"]["text"].startswith("1. Прочитать README — проверка: файл прочитан\n2. Посчитать строки")
    assert asked["preview"]["note"] == "Цель: Посчитать строки в README"
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "сначала спроси про пустые строки"})
    again = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["id"] != asked["id"]])[0]
    hub.post(f"/v1/approvals/{again['id']}", json={"allow": True})
    question = wait(lambda: [q for q in hub.get(f"/v1/runs/{run_id}/plan").json()["questions"]
                             if q["status"] == "pending"])[0]  # fmt: skip
    assert question["question"] == "Считать пустые строки?" and hub.get(f"/v1/runs/{run_id}").json()["waiting"]
    assert hub.post(f"/v1/runs/{run_id}/questions/nope", json={"answer": "да"}).status_code == 404
    assert (
        hub.post(f"/v1/runs/{run_id}/questions/{question['id']}", json={"answer": "Нет, без пустых"}).status_code == 200
    )
    run = ended(hub, run_id)
    assert run["status"] == "done"
    said = results(agent)
    assert "did not approve this plan: сначала спроси про пустые строки" in said[0]
    assert "approved by the user" in said[1] and said[2] == "The user answered: Нет, без пустых"
    plan = hub.get(f"/v1/runs/{run_id}/plan").json()
    assert plan["plans"][0]["approved"] == "user" and plan["questions"][0]["status"] == "answered"


def test_each_step_waits_for_the_user(hub):  # noqa: F811
    use(hub, Scripted("agent", [[PLAN], [step(1, "running")], [("finish", {"summary": "—", "goal_met": False})]]))
    run_id = start(hub, autonomy="each_step")
    plan = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    hub.post(f"/v1/approvals/{plan['id']}", json={"allow": True})
    first = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "start_step"])[0]
    assert first["preview"] == {"kind": "text", "text": "1. Прочитать README", "note": "проверка: файл прочитан"}
    hub.post(f"/v1/approvals/{first['id']}", json={"allow": False})
    run = ended(hub, run_id)
    assert run["status"] == "done" and run["report"]["goal_met"] is False
    steps = hub.get(f"/v1/runs/{run_id}/plan").json()["plans"][0]["steps"]
    assert steps[0]["status"] == "pending"  # not let start


def test_an_agent_going_round_in_circles_hears_where_it_is(hub):  # noqa: F811
    """2026-10-28 (the user's check: the message was sent, and the agent went on checking it for 60 more actions,
    step 1 still «running», until the step limit): every 20 actions with the plan standing still, the agent hears it
    - mark the step, or say what could not be confirmed and go on."""
    agent = use(hub, Scripted("agent", [[PLAN], [step(1, "running")],
                                        [("note", {"text": f"смотрю ещё раз {n}"}) for n in range(20)],
                                        [("finish", {"summary": "Сделано.", "goal_met": True})]]))  # fmt: skip
    run = ended(hub, start(hub))
    assert run["status"] == "done"
    told = json.dumps(agent.prompts[3:], ensure_ascii=False, default=str)
    assert "20 actions since the plan last moved (step 1 «Прочитать README» is still running)" in told
    assert "20 actions since" not in json.dumps(agent.prompts[:3], ensure_ascii=False, default=str)


def test_at_the_step_limit_the_agent_still_reports(hub):  # noqa: F811
    """At the step limit an autonomous run may still mark its step and finish: it ends with a report, not with
    «the step limit was reached» and nothing said."""
    agent = use(hub, Scripted("agent", [[PLAN], [step(1, "running")], [("note", {"text": "смотрю"})],
                                        [("note", {"text": "ещё"})],
                                        [step(1, "done", result="прочитан", check="файл открыт"),
                                         ("finish", {"summary": "Шаги кончились: README прочитан, строки не "
                                                                "посчитаны.", "goal_met": False})]]))  # fmt: skip
    run = ended(hub, start(hub, max_steps=3))
    said = results(agent)
    assert said[2] == "Noted." and said[3] == autonomous.LIMIT  # an action at the limit: not run
    assert run["status"] == "done" and run["report"]["goal_met"] is False
    assert run["report"]["summary"].startswith("Шаги кончились")
    steps = hub.get(f"/v1/runs/{run['id']}/plan").json()["plans"][0]["steps"]
    assert steps[0]["status"] == "done"


def test_the_user_writes_to_a_running_task(hub):  # noqa: F811
    """The input under a running task: a word the agent reads before its next step, or the answer it waits on."""
    agent = use(hub, Scripted("agent", [[PLAN], [("ask_user", {"question": "Какой файл?"})],
                                        [("finish", {"summary": "Сделано.", "goal_met": True})]]))  # fmt: skip
    run_id = start(hub, autonomy="plan")
    plan = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert hub.post(f"/v1/runs/{run_id}/message", json={"text": "   "}).status_code == 422
    noted = hub.post(f"/v1/runs/{run_id}/message", json={"text": " Только README, другие файлы не трогай "})
    assert noted.json() == {"noted": True}
    hub.post(f"/v1/approvals/{plan['id']}", json={"allow": True})
    question = wait(lambda: [q for q in hub.get(f"/v1/runs/{run_id}/plan").json()["questions"]
                             if q["status"] == "pending"])[0]  # fmt: skip
    assert hub.post(f"/v1/runs/{run_id}/message", json={"text": "README.md"}).json() == {"answered": question["id"]}
    run = ended(hub, run_id)
    assert run["status"] == "done"
    seen = json.dumps(agent.prompts[1:], ensure_ascii=False, default=str)
    assert "The user writes to you now - take it into account from your next step: Только README, другие" in seen
    assert results(agent)[1] == "The user answered: README.md"
    assert hub.post(f"/v1/runs/{run_id}/message", json={"text": "ещё"}).status_code in (404, 409)  # it ended


def test_the_level_changes_on_the_way(hub):  # noqa: F811
    """«Каждый шаг» waits on its plan; switched to «Сам», the plan it waits on is answered and the steps go on."""
    use(hub, Scripted("agent", [[PLAN], [step(1, "running")], [step(1, "done", result="прочитан", check="да")],
                                [("finish", {"summary": "Сделано.", "goal_met": True})]]))  # fmt: skip
    run_id = start(hub, autonomy="each_step")
    plan = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert plan["tool"] == "set_plan"
    assert hub.put(f"/v1/runs/{run_id}/autonomy", json={"autonomy": "turbo"}).status_code == 422
    changed = hub.put(f"/v1/runs/{run_id}/autonomy", json={"autonomy": "free"}).json()
    assert changed == {"autonomy": "free", "answered": [plan["id"]]}
    run = ended(hub, run_id)
    assert run["status"] == "done" and run["autonomy"] == "free"
    assert not [a for a in hub.get("/v1/approvals").json()["approvals"] if a["conversation_id"] == run_id]
    assert hub.put(f"/v1/runs/{run_id}/autonomy", json={"autonomy": "plan"}).status_code in (404, 409)  # it ended


def test_a_pause_the_users_plan_and_the_planner_handing_over(hub):  # noqa: F811
    planner = use(hub, Scripted("planner", [[PLAN]]))
    agent = use(hub, Scripted("agent", [[step(1, "running")], [step(1, "done", result="ok", check="видно")],
                                        [("finish", {"summary": "Сделано.", "goal_met": True})]]))  # fmt: skip
    run_id = start(hub, planner_model_id="planner/m", autonomy="plan")
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]  # the planner's plan waits for the user
    assert hub.post(f"/v1/runs/{run_id}/pause").status_code == 202
    assert hub.get(f"/v1/runs/{run_id}/plan").json()["paused"]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True})
    wait(lambda: hub.get(f"/v1/runs/{run_id}/plan").json()["plans"])
    time.sleep(0.3)
    assert agent.prompts == []  # paused: the run's model has not been asked yet
    edited = hub.put(f"/v1/runs/{run_id}/plan", json={"steps": [{"title": "Только посчитать", "check": "число"}],
                                                      "reason": "README уже прочитан"})  # fmt: skip
    assert edited.json()["version"] == 2 and edited.json()["made_by"] == "app"
    hub.post(f"/v1/runs/{run_id}/resume")
    run = ended(hub, run_id)
    assert run["status"] == "done" and len(planner.prompts) == 1  # the planner planned; the run's model went on
    heard = [t["content"] for t in agent.prompts[0] if t["role"] == "user" and "changed your plan" in t["content"]]
    assert heard and "1. Только посчитать — проверка: число" in heard[0]
    assert hub.put(f"/v1/runs/{run_id}/plan", json={"steps": ["x"]}).status_code == 409  # it ended
    bad = hub.post("/v1/runs/autonomous", json={"goal": "x", "model_id": "agent/m", "autonomy": "wild"})
    assert bad.status_code == 422 and "unknown autonomy" in bad.json()["detail"]
    assert hub.post("/v1/runs/autonomous", json={"goal": "x"}).status_code == 422  # no model, no profile


def test_the_plan_comes_before_acting(hub):  # noqa: F811
    agent = use(hub, Scripted("agent", [
        [("run_command", {"project": "x", "command": "echo hi"})],  # acting before a plan: refused
        [("finish", {"summary": "Сделал.", "goal_met": True})],  # "met" with no plan: refused
        [("finish", {"summary": "Нельзя: нет проекта.", "goal_met": False})],  # giving up honestly: fine
        "Не получилось.",
    ]))  # fmt: skip
    run = ended(hub, start(hub))
    said = results(agent)
    assert said[0] == autonomous.PLAN_FIRST and "there is no plan" in said[1] and said[2].startswith("Report saved")
    assert run["report"]["goal_met"] is False


def trusted_agent(hub, repo):  # noqa: F811
    """A project, and the agent's profile that writes and runs commands there without asking."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    body = {"name": "Правщик", "instructions": "", "model_id": "agent/m", "mode": "trusted", "max_steps": 30,
            "tools": ["read_file", "write_file", "run_command"], "project_ids": [pid]}  # fmt: skip
    made = hub.post("/v1/agents", json=body)
    assert made.status_code == 201, made.text
    return made.json()["id"]


def test_a_step_is_checked_against_what_it_changed(hub, repo):  # noqa: F811
    """25.1 (§5.4: «coder» changed a line its condition «готово» forbade, and wrote that it had not): a step marked
    done gets what it actually changed - the project's files where the step began against now, so a command's writes
    count as the hub's own. The condition's forms the hub checks refuse at the call, as a goal's constraints (11.4);
    what a command did against them goes into the step's record and on the card, and «goal met» is not taken while
    it stands. The report's files are compared with the run's diff."""
    agent = use(hub, Scripted("agent", [
        [PLAN],
        [step(1, "running")],
        [("write_file", {"project": "proj", "path": "README.md", "content": "x\n"})],  # refused at the call
        [("write_file", {"project": "proj", "path": "count.txt", "content": "12\n"})],
        [step(1, "done", result="прочитан", check="README не трогал")],
        [("run_command", {"project": "proj", "command": "Add-Content README.md 'more'"})],  # unseen at the call
        [step(2, "done", result="12", check="посчитал")],  # never «running»: from where step 1 ended
        [("finish", {"summary": "12 строк.", "goal_met": True, "files": ["count.txt"]})],  # not taken
        [("finish", {"summary": "12 строк, но README изменён.", "goal_met": False,
                     "files": ["proj/count.txt", "docs/guide.md"]})],
        "Готово.",
    ]))  # fmt: skip
    condition = "число строк названо; не трогать: README.md"
    run_id = start(hub, profile_id=trusted_agent(hub, repo), done_when=condition, project_ids=None)
    run = ended(hub, run_id)
    said = results(agent)
    assert said[0].startswith("Plan version 1 saved") and "the hub refuses what breaks «не трогать: README.md»" in \
        said[0]  # fmt: skip
    assert "forbids writing readme.md" in said[2] and (repo / "README.md").read_text(encoding="utf-8").startswith("#")
    assert "What the step changed, by the hub's diff: count.txt (added, +1 -0)." in said[4]
    assert "Next: step 2" in said[4] and "does not fit" not in said[4]
    assert "README.md (changed, +1 -0)" in said[6] and "It does not fit the condition «не трогать: README.md»" in \
        said[6]  # fmt: skip
    assert said[7].startswith("not saved: the condition «не трогать: README.md» is broken") and "README.md" in said[7]
    assert said[8].startswith("Report saved")
    first, second = hub.get(f"/v1/runs/{run_id}/plan").json()["plans"][0]["steps"]
    assert first["files"] == [{"path": "count.txt", "status": "added", "added": 1, "removed": 0}]
    assert first["mismatch"] == "" and first["note"] == "прочитан · проверка: README не трогал"
    assert second["files"] == [{"path": "README.md", "status": "changed", "added": 1, "removed": 0}]
    assert second["mismatch"] == "условие «не трогать: README.md» — изменён README.md"
    report = run["report"]
    assert report["goal_met"] is False and report["changed"] == ["README.md", "count.txt"]
    assert report["unlisted"] == ["README.md"] and report["unchanged"] == ["docs/guide.md"]
    assert report["broken"] == ["условие «не трогать: README.md» — изменён README.md"]


def test_questions_come_with_answers_to_pick_in_a_task_and_in_a_chat(hub):  # noqa: F811
    """2026-10-28 (the user's word): ask_user offers answers (options) - an agent's task shows them as buttons, and
    an ordinary chat's model has the same tool now; a chat's question waits in the chat and its answer goes back."""
    agent = use(hub, Scripted("agent", [
        [PLAN], [("ask_user", {"question": "Считать пустые строки?", "options": ["Да", "Нет", "Да", " "]})],
        [("finish", {"summary": "Посчитал.", "goal_met": True})],
    ]))  # fmt: skip
    run_id = start(hub, autonomy="free")
    asked = wait(lambda: [q for q in hub.get(f"/v1/runs/{run_id}/plan").json()["questions"]
                          if q["status"] == "pending"])[0]  # fmt: skip
    assert asked["options"] == [{"label": "Да"}, {"label": "Нет"}]  # the same once, no empty one
    hub.post(f"/v1/runs/{run_id}/questions/{asked['id']}", json={"answer": "Нет"})
    assert ended(hub, run_id)["status"] == "done" and results(agent)[1] == "The user answered: Нет"

    chat = use(hub, Scripted("chatty", [
        [("ask_user", {"question": "Какой файл?", "options": ["README.md", "notes.txt"]})], "Беру README.md.",
    ]))  # fmt: skip
    cid = hub.post("/v1/conversations", json={}).json()["id"]
    assert hub.post(f"/v1/conversations/{cid}/messages", json={"content": "посчитай строки",
                                                               "model_id": "chatty/m"}).status_code == 202  # fmt: skip
    waiting = wait(lambda: hub.get(f"/v1/conversations/{cid}/questions").json()["questions"])[0]
    tools = json.loads(hub.app.state.db.call(lambda c: c.execute("SELECT tools FROM conversations WHERE id = ?",
                                                                 (cid,)).fetchone()["tools"]))  # fmt: skip
    assert "ask_user" in [t["name"] for t in tools]
    assert waiting["question"] == "Какой файл?"
    assert [o["label"] for o in waiting["options"]] == ["README.md", "notes.txt"]
    hub.post(f"/v1/runs/{cid}/questions/{waiting['id']}", json={"answer": "README.md"})
    wait(lambda: not hub.get(f"/v1/conversations/{cid}").json().get("busy") and len(chat.prompts) == 2)
    assert results(chat)[0] == "The user answered: README.md"


def test_full_asks_nothing_and_refuses_what_is_never_done_unasked(hub, repo):  # noqa: F811
    """«Полный» (33.1, D56; the user's word 2026-11-08: «не должен спросить о разрешение даже когда оно пишет или
    что-то делает»): a write, a command, a personal fact - done with no card, each kept in the approvals as allowed by
    the level; what deletes or cannot be undone, and a command that reaches the hub's own data, are refused - not
    asked (the user's choice)."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    agent = use(hub, Scripted("agent", [
        [PLAN], [step(1, "running")],
        [("write_file", {"project": "proj", "path": "count.txt", "content": "12\n"})],
        [("run_command", {"project": "proj", "command": "Add-Content count.txt '13'"})],
        [("run_command", {"project": "proj", "command": "git push origin main"})],
        [("run_command", {"project": "proj", "command": "Get-Content ..\\data\\hub.db"})],
        [("remember", {"fact": "Телефон Анны +998 90 123 45 67", "personal": True})],
        [("finish", {"summary": "Записал и запомнил.", "goal_met": True})],
        "Готово.",
    ]))  # fmt: skip
    run_id = start(hub, autonomy="full", project_ids=[pid])
    run = ended(hub, run_id)
    said = results(agent)
    assert run["status"] == "done" and run["autonomy"] == "full"
    assert "the user can undo it" in said[2] and not said[3].startswith("not done")
    assert (repo / "count.txt").read_text(encoding="utf-8").split() == ["12", "13"]
    assert said[4].startswith("not done: «Полный» asks nothing") and "deletes or cannot be undone" in said[4]
    assert said[5].startswith("not done: «Полный» asks nothing") and "the hub's own data" in said[5]
    assert said[6].startswith("Kept for good")
    rows = hub.app.state.db.call(lambda c: [dict(r) for r in c.execute(
        "SELECT tool, status, scope, decided_by FROM approvals WHERE conversation_id = ? ORDER BY created_at",
        (run_id,))])  # fmt: skip
    assert [r["tool"] for r in rows] == ["write_file", "run_command", "remember"]
    assert all((r["status"], r["scope"], r["decided_by"]) == ("allowed", "auto", "full") for r in rows)
    told = json.dumps(agent.prompts[0], ensure_ascii=False)
    assert "Autonomy: nothing is asked" in told and "never ask the user for permission" in told


def test_switched_to_full_the_card_it_waits_on_is_answered(hub, repo):  # noqa: F811
    """«Авто» asks a write; switched to «Полный» on the way, the card it waits on is answered and the rest goes
    unasked."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    use(hub, Scripted("agent", [
        [PLAN], [step(1, "running")],
        [("write_file", {"project": "proj", "path": "count.txt", "content": "12\n"})],  # «Авто»: a card
        [("write_file", {"project": "proj", "path": "more.txt", "content": "13\n"})],  # «Полный»: none
        [("finish", {"summary": "Записал.", "goal_met": True})],
        "Готово.",
    ]))  # fmt: skip
    run_id = start(hub, autonomy="free", project_ids=[pid])
    write = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "write_file"])[0]
    changed = hub.put(f"/v1/runs/{run_id}/autonomy", json={"autonomy": "full"}).json()
    assert changed == {"autonomy": "full", "answered": [write["id"]]}
    assert ended(hub, run_id)["status"] == "done"
    assert (repo / "count.txt").exists() and (repo / "more.txt").exists()


def test_the_runs_table_takes_full_and_keeps_its_rows(tmp_path, monkeypatch):
    """Migration 051: SQLite keeps a CHECK as it was made - the table is made again, its runs kept."""
    migrating = sys.modules["hub.db.migrate"]  # hub.db re-exports the function `migrate`, hiding the module
    every = available_migrations()
    monkeypatch.setattr(migrating, "available_migrations", lambda: [m for m in every if m[0] < 51])
    db = Database(tmp_path / "hub.db")
    db.call(migrate)

    def run(conn):
        cid = store.create_conversation(conn, kind="agent_run", title="Задача")
        conn.execute("INSERT INTO agent_runs (conversation_id, profile_name, model_id, max_steps, max_minutes, task,"
                     " kind, goal, autonomy) VALUES (?, 'Агент', 'agent/m', 100, 60, 'Цель: x', 'autonomous', 'x',"
                     " 'free')", (cid,))  # fmt: skip
        return cid

    cid = db.call(run)
    full = "UPDATE agent_runs SET autonomy = 'full' WHERE conversation_id = ?"
    with pytest.raises(sqlite3.IntegrityError):
        db.call(lambda c: c.execute(full, (cid,)))
    monkeypatch.setattr(migrating, "available_migrations", lambda: every)
    assert 51 in db.call(migrate)
    kept = db.call(lambda c: dict(c.execute("SELECT * FROM agent_runs WHERE conversation_id = ?", (cid,)).fetchone()))
    assert (kept["goal"], kept["autonomy"], kept["kind"], kept["max_steps"]) == ("x", "free", "autonomous", 100)
    db.call(lambda c: c.execute(full, (cid,)))
    assert db.call(lambda c: c.execute("SELECT autonomy FROM agent_runs").fetchone()[0]) == "full"
    db.close()
