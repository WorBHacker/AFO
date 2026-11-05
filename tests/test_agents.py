"""The hub's own agents (stage 5.3, D16): profiles with versions, runs with their role, tools, rights and limits, the
GPU queue, stopping, and going on after a restart."""

import asyncio
import time

from hub import agents
from hub.chat import Limits
from hub.gateway.base import Model, StreamEvent
from tests.test_changes import edit, wait
from tests.test_tools import ToolProvider, hub  # noqa: F401 - the hub fixture

INSTRUCTIONS = "Правь аккуратно, по одному файлу."


def register(hub, provider, name="fake", locality="local", prices=None):  # noqa: F811 - the fixture's name
    provider.name = name
    provider.models = [Model(f"{name}/echo", name, "echo", "Echo", locality, {}, prices or {})]
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    return provider


def profile(hub, **fields):  # noqa: F811
    body = {"name": "Правщик", "instructions": INSTRUCTIONS, "model_id": "fake/echo", "mode": "ask",
            "tools": ["read_file", "edit_file"], "project_ids": [], "max_steps": 10, **fields}  # fmt: skip
    response = hub.post("/v1/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def run_of(hub, run_id):  # noqa: F811
    return hub.get(f"/v1/runs/{run_id}").json()


def ended(hub, run_id, timeout=20.0):  # noqa: F811
    return wait(lambda: (r := run_of(hub, run_id))["status"] in ("done", "error", "cancelled") and r, timeout)


def test_profiles_are_checked_and_versioned(hub, repo):  # noqa: F811
    register(hub, ToolProvider([]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid])
    assert made["version"] == 1 and made["tools"] == ["read_file", "edit_file"] and made["project_ids"] == [pid]
    bad = {"name": "x", "model_id": "fake/echo"}
    wrong = [({"name": " "}, "needs a name"), ({"model_id": "nope/x"}, "unknown model"),
             ({"model_id": "claude-code/default"}, "agent of its own"), ({"tools": ["rm"]}, "unknown tools"),
             ({"mode": "root"}, "unknown mode"), ({"max_steps": 0}, "max_steps"),
             ({"project_ids": ["ghost"]}, "unknown project 'ghost'"), ({"budget_usd": -1}, "positive"),
             ({"name": "Правщик"}, "exists already")]  # fmt: skip
    for change, error in wrong:
        response = hub.post("/v1/agents", json={**bad, **change})
        assert response.status_code == 422 and error in response.json()["detail"], (change, response.text)
    changed = hub.put(f"/v1/agents/{made['id']}", json={**made, "instructions": "Только тесты.", "max_steps": 5})
    assert changed.json()["version"] == 2
    versions = hub.get(f"/v1/agents/{made['id']}/versions").json()["versions"]
    assert [(v["version"], v["instructions"]) for v in versions] == [(2, "Только тесты."), (1, INSTRUCTIONS)]
    assert hub.get("/v1/agents").json()["tools"][:2] == ["list_files", "read_file"]
    assert hub.delete(f"/v1/agents/{made['id']}").status_code == 204
    assert hub.get(f"/v1/agents/{made['id']}").status_code == 404


def test_a_run_works_within_its_profile(hub, repo):  # noqa: F811
    provider = register(hub, ToolProvider([[{"id": "w1", "name": "edit_file", "input": edit()}]]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid])
    started = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "Поправь приветствие в src/app.py"})
    assert started.status_code == 201
    run_id = started.json()["id"]
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["conversation_id"] == run_id and run_of(hub, run_id)["shown_status"] == "waiting"
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True})
    run = ended(hub, run_id)
    assert run["status"] == "done" and run["steps"] == 2 and run["profile_version"] == 1
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").startswith("print('привет')")

    sent_tools = [t["name"] for t in provider.calls[0][1]]
    assert sent_tools == ["read_file", "edit_file"]  # the profile's tools only, fixed for the run
    first_prompt = provider.calls[0][0]
    assert first_prompt[0] == {"role": "user", "content": "Поправь приветствие в src/app.py"}
    assert "<agent_role>\nYou are the agent «Правщик»" in first_prompt[1]["content"]
    chat = hub.get(f"/v1/conversations/{run_id}").json()
    assert chat["title"].startswith("Правщик: Поправь приветствие")
    feed = [e["type"] for e in hub.get("/v1/feed", params={"types": "run.started,run.finished"}).json()["events"]]
    assert feed == ["run.finished", "run.started"]
    assert hub.get("/v1/runs").json()["runs"][0]["id"] == run_id


def test_the_limits_of_a_run(hub, repo):  # noqa: F811
    looping = [[{"id": f"r{n}", "name": "read_file", "input": {"project": "proj", "path": "README.md"}}]
               for n in range(10)]  # fmt: skip
    register(hub, ToolProvider(looping))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid], max_steps=2)
    run = ended(hub, hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "читай"}).json()["id"])
    assert run["status"] == "error" and run["detail"] == "the step limit (2) was reached"

    paid = register(hub, ToolProvider(looping), name="paid", prices={"input": 1000.0, "output": 1000.0})
    budgeted = profile(hub, name="Дорогой", model_id="paid/echo", project_ids=[pid], budget_usd=0.01)
    run = ended(hub, hub.post(f"/v1/agents/{budgeted['id']}/runs", json={"task": "читай"}).json()["id"])
    assert run["status"] == "error" and "budget ($0.01) was reached" in run["detail"] and run["cost_usd"] > 0.01
    assert len(paid.calls) == 1  # stopped after the step that crossed it

    # the time limit, straight through the chat's loop: a deadline already gone stops it after its first step
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})
    ends = []

    async def go():
        async def record(outcome):
            ends.append(outcome)

        await hub.app.state.chat.start(conv, "fake/echo", "читай", limits=Limits(deadline=time.monotonic() - 1),
                                       on_end=record)  # fmt: skip

    hub.portal.call(go)
    wait(lambda: ends)
    assert ends[0].status == "error" and "time limit" in ends[0].detail


def test_local_runs_queue_for_the_gpu_and_can_be_stopped(hub, repo):  # noqa: F811
    register(hub, ToolProvider([[{"id": "w1", "name": "edit_file", "input": edit()}]]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid])
    first = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "первая"}).json()["id"]
    wait(lambda: hub.get("/v1/approvals").json()["approvals"])  # the first holds the GPU, waiting for the user
    second = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "вторая"}).json()["id"]
    time.sleep(0.3)
    assert run_of(hub, second)["status"] == "queued"  # one local model at a time
    assert hub.post(f"/v1/runs/{second}/cancel").json() == {"cancelled": True}
    assert ended(hub, second)["status"] == "cancelled"
    assert [m["role"] for m in hub.get(f"/v1/conversations/{second}").json()["messages"]] == []  # it never began
    assert hub.post(f"/v1/runs/{first}/cancel").json() == {"cancelled": True}
    assert ended(hub, first)["status"] == "cancelled"
    assert hub.get("/v1/approvals").json()["approvals"] == []  # its open request closed with it


def test_a_cloud_agent_and_a_local_only_project(hub, repo):  # noqa: F811
    register(hub, ToolProvider([]), name="cloudy", locality="cloud")
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local-only by default (D10)
    made = profile(hub, model_id="cloudy/echo", project_ids=[pid])
    refused = hub.post(f"/v1/agents/{made['id']}/runs", json={"task": "посмотри"})
    assert refused.status_code == 422 and "only local models may see it (D10)" in refused.json()["detail"]


def test_a_run_goes_on_after_the_hub_restarts(hub, repo):  # noqa: F811
    provider = register(hub, ToolProvider([]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    made = profile(hub, project_ids=[pid])

    def interrupted(conn):  # as if the hub had stopped in the middle of it
        db_profile = agents.get_profile(conn, made["id"])
        run_id = agents.create_run(conn, db_profile, "долгая задача", [pid])
        conn.execute("UPDATE agent_runs SET status = 'running' WHERE conversation_id = ?", (run_id,))
        return run_id

    run_id = hub.app.state.db.call(interrupted)
    assert hub.portal.call(hub.app.state.agents.start) == [run_id]
    run = ended(hub, run_id)
    assert run["status"] == "done"
    assert provider.calls[0][0][0]["content"] == agents.RESUMED
    started = hub.get("/v1/feed", params={"types": "run.started"}).json()["events"][0]
    assert started["payload"]["resumed"] is True
    asyncio.run(asyncio.sleep(0))


# --- a coordinator (stage 6.4, D20) ---


def delegate(agent, task, call_id="d1"):
    return {"id": call_id, "name": "delegate_task", "input": {"agent": agent, "task": task}}


def test_a_coordinator_gives_a_task_and_gets_the_answer(hub, repo):  # noqa: F811
    provider = register(hub, ToolProvider([[delegate("Писатель", "Напиши одно слово")]]))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    profile(hub, name="Писатель", instructions="Пишешь коротко.\nВторая строка не видна координатору.",
            tools=["read_file"], project_ids=[pid])  # fmt: skip
    boss = profile(hub, name="Координатор", instructions="Делишь задачу.", tools=["delegate_task", "read_file"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай дело"}).json()["id"]
    run = ended(hub, run_id)
    assert run["status"] == "done"  # both local: the coordinator gave up the GPU while it waited (no deadlock)
    role = provider.calls[0][0][1]["content"]
    assert "Agents you can give tasks to with delegate_task:\n- Писатель: Пишешь коротко." in role
    assert "Вторая строка" not in role and "- Координатор" not in role
    (child,) = [r for r in hub.get("/v1/runs").json()["runs"] if r["parent_run"] == run_id]
    assert child["status"] == "done" and child["profile_name"] == "Писатель" and child["parent_name"] == "Координатор"
    assert child["task"] == "Напиши одно слово"
    results = [b for m in hub.get(f"/v1/conversations/{run_id}").json()["messages"] if m["role"] == "tool"
               for b in m["content"]]  # fmt: skip
    assert results[0]["output"] == "agent «Писатель» finished. Its answer:\nГотово" and results[0]["error"] is False


def test_one_level_only_and_unknown_agents(hub, repo):  # noqa: F811
    register(hub, ToolProvider([[delegate("Никто", "x")], [delegate("Помощник", "x", "d2")],
                                [delegate("Координатор", "x", "d3")]]))  # fmt: skip
    profile(hub, name="Помощник", tools=["delegate_task"])
    boss = profile(hub, name="Координатор", tools=["delegate_task"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    assert ended(hub, run_id)["status"] == "done"
    results = [b["output"] for m in hub.get(f"/v1/conversations/{run_id}").json()["messages"] if m["role"] == "tool"
               for b in m["content"]]  # fmt: skip
    assert results[0].startswith("no agent 'Никто'; the agents are: Координатор, Помощник")
    (child,) = [r for r in hub.get("/v1/runs").json()["runs"] if r["parent_run"] == run_id]
    child_results = [b["output"] for m in hub.get(f"/v1/conversations/{child['id']}").json()["messages"]
                     if m["role"] == "tool" for b in m["content"]]  # fmt: skip
    assert child_results == ["you are a sub-agent yourself: you cannot give tasks further (one level, D20)"]


def test_all_tools_by_default_means_no_delegation(hub):  # noqa: F811
    provider = register(hub, ToolProvider([]))
    plain = profile(hub, name="Обычный", tools=[])
    ended(hub, hub.post(f"/v1/agents/{plain['id']}/runs", json={"task": "x"}).json()["id"])
    names = [t["name"] for t in provider.calls[0][1]]
    assert "delegate_task" not in names and "delegate_tasks" not in names and "web_fetch" in names
    assert "run_command" not in names and "read_file" not in names  # no projects: no project tools (2026-10-28)
    assert len(names) == len(set(names))  # one definition per name


def test_stopping_the_coordinator_stops_its_sub_run(hub, repo):  # noqa: F811
    register(
        hub, ToolProvider([[delegate("Правщик", "Поправь")], [{"id": "w1", "name": "edit_file", "input": edit()}]])
    )
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    profile(hub, name="Правщик", tools=["edit_file"], project_ids=[pid])
    boss = profile(hub, name="Координатор", tools=["delegate_task"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]  # the sub-agent waits for the user
    (child,) = [r for r in hub.get("/v1/runs").json()["runs"] if r["parent_run"] == run_id]
    assert asked["conversation_id"] == child["id"] and child["shown_status"] == "waiting"
    hub.post(f"/v1/runs/{run_id}/cancel")
    assert ended(hub, child["id"])["status"] == "cancelled"
    assert ended(hub, run_id)["status"] == "cancelled"
    assert hub.get("/v1/approvals").json()["approvals"] == []


# --- a coordinator's batch (stage 16.1, D50) ---


def _first_text(messages):
    content = messages[0]["content"]
    return content if isinstance(content, str) else "".join(b.get("text", "") for b in content)


class ByTask:
    """Answers by the run's task, its first message: the coordinator's asks for tools as scripted; any other is a
    worker's - it works for `pause` seconds (stopping when asked) and answers; «зациклись» calls a tool forever. Who
    worked when is recorded."""

    def __init__(self, name, locality, scripts, pause=0.3, refs=("echo",)):
        self.name = name
        self.scripts = {task: list(steps) for task, steps in scripts.items()}
        self.pause = pause
        self.calls = []  # (model_ref, messages, tools) per request
        self.active = self.peak = 0
        self.started = []  # (model_ref, task) in the order the workers began
        self.models = [Model(f"{name}/{ref}", name, ref, ref.title(), locality, {}, {}) for ref in refs]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        task = _first_text(messages)
        self.calls.append((model_ref, messages, tools))
        usage = {"input_tokens": 10, "output_tokens": 2}
        steps = self.scripts.get(task)
        if steps:
            yield StreamEvent("content", "Делю. ")
            yield StreamEvent("end", usage=usage, stop_reason="tool_use", tool_calls=steps.pop(0))
            return
        if steps is None and "зациклись" in task:
            yield StreamEvent("end", usage=usage, stop_reason="tool_use",
                              tool_calls=[{"id": f"n{len(self.calls)}", "name": "nothing", "input": {}}])  # fmt: skip
            return
        if steps is None:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started.append((model_ref, task))
            try:
                until = time.monotonic() + self.pause
                while time.monotonic() < until and not cancel.is_set():
                    await asyncio.sleep(0.02)
            finally:
                self.active -= 1
        yield StreamEvent("content", f"ответ: {task.splitlines()[0][:40]}" if steps is None else "Итог готов")
        yield StreamEvent("end", usage=usage, stop_reason="end_turn")


def plug(hub, provider):  # noqa: F811
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    return provider


def batch(*tasks, call_id="b1"):
    return {"id": call_id, "name": "delegate_tasks", "input": {"tasks": [
        {"task_id": t[0], "agent_profile": t[1], "prompt": t[2], **({"dependencies": t[3]} if len(t) > 3 else {})}
        for t in tasks]}}  # fmt: skip


def outputs(hub, run_id):  # noqa: F811
    return [b for m in hub.get(f"/v1/conversations/{run_id}").json()["messages"] if m["role"] == "tool"
            for b in m["content"]]  # fmt: skip


def test_a_batch_runs_cloud_tasks_at_once_and_a_dependent_after_them(hub):  # noqa: F811
    provider = plug(hub, ByTask("cloudy", "cloud", {"Сделай дело": [[batch(
        ("a", "Писатель", "Напиши введение"), ("b", "Читатель", "Прочти план"),
        ("c", "Ревьюер", "Сведи всё", ["a", "b"]))]]}))  # fmt: skip
    for name in ("Писатель", "Читатель", "Ревьюер"):
        profile(hub, name=name, instructions=f"{name} делает своё.", model_id="cloudy/echo", tools=[])
    boss = profile(hub, name="Координатор", model_id="cloudy/echo", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай дело"}).json()["id"]
    assert ended(hub, run_id)["status"] == "done"
    role = provider.calls[0][1][1]["content"]
    assert "Agents you can give tasks to with delegate_tasks:\n" in role and "- Ревьюер: Ревьюер делает" in role
    assert provider.peak == 2  # a and b at once (cloud), c after both
    assert sorted(t for _, t in provider.started[:2]) == ["Напиши введение", "Прочти план"]
    reviewer = provider.started[2][1]
    assert reviewer.startswith("Сведи всё\n\n") and "[a] «Писатель»:\nответ: Напиши введение" in reviewer
    assert "[b] «Читатель»:\nответ: Прочти план" in reviewer  # what the tasks it waited for found
    (board,) = outputs(hub, run_id)
    assert board["error"] is False and board["output"].startswith("3 tasks: 3 done.")
    assert "[c] «Ревьюер» — done. Answer:\nответ: Сведи всё" in board["output"]
    tasks = hub.get(f"/v1/runs/{run_id}/tasks").json()["tasks"]
    assert [(t["task_id"], t["agent"], t["status"], t["dependencies"]) for t in tasks] == [
        ("a", "Писатель", "done", []), ("b", "Читатель", "done", []), ("c", "Ревьюер", "done", ["a", "b"])]  # fmt: skip
    children = {r["id"]: r for r in hub.get("/v1/runs").json()["runs"] if r["parent_run"] == run_id}
    assert {t["run_id"] for t in tasks} == set(children) and tasks[0]["answer"] == "ответ: Напиши введение"


def test_a_batch_is_checked_before_anything_starts(hub):  # noqa: F811
    nine = [(f"t{n}", "Писатель", "x") for n in range(9)]
    wrong = [
        (batch(), "`tasks`: a list of 1 to 8 tasks"),
        (batch(*nine), "`tasks`: a list of 1 to 8 tasks"),
        (batch(("a b", "Писатель", "x")), "task_id 'a b': 1 to 40 letters, digits, - or _"),
        (batch(("a", "Писатель", " ")), "task 'a' needs `agent_profile` and `prompt`"),
        (batch(("a", "Писатель", "x"), ("a", "Писатель", "y")), "task_id 'a' is used twice"),
        (batch(("a", "Писатель", "x", ["z"])), "task 'a' depends on 'z', which is not in this batch"),
        (batch(("a", "Писатель", "x", ["b"]), ("b", "Писатель", "y", ["a"])),
         "the tasks wait for each other in a circle: a, b"),
        (batch(("a", "Писатель", "x", ["a"])), "the tasks wait for each other in a circle: a"),
        (batch(("a", "Никто", "x")), "no agent 'Никто'; the agents are: Координатор, Писатель"),
    ]  # fmt: skip
    plug(hub, ByTask("fake", "local", {"Сделай": [[dict(call, id=f"c{n}")] for n, (call, _) in enumerate(wrong)]}))
    profile(hub, name="Писатель", tools=[])
    boss = profile(hub, name="Координатор", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    assert ended(hub, run_id)["status"] == "done"
    said = outputs(hub, run_id)
    assert [(b["output"], b["error"]) for b in said] == [(error, True) for _, error in wrong]
    assert [r for r in hub.get("/v1/runs").json()["runs"] if r["parent_run"] == run_id] == []  # nothing began
    assert hub.get(f"/v1/runs/{run_id}/tasks").json()["tasks"] == []


def test_a_failed_task_skips_the_tasks_after_it(hub):  # noqa: F811
    tasks = batch(("a", "Зацикленный", "зациклись"), ("b", "Писатель", "Напиши"), ("c", "Ревьюер", "Сведи", ["a"]))
    plug(hub, ByTask("cloudy", "cloud", {"Сделай дело": [[tasks]]}))
    profile(hub, name="Зацикленный", model_id="cloudy/echo", tools=[], max_steps=1)
    for name in ("Писатель", "Ревьюер"):
        profile(hub, name=name, model_id="cloudy/echo", tools=[])
    boss = profile(hub, name="Координатор", model_id="cloudy/echo", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай дело"}).json()["id"]
    assert ended(hub, run_id)["status"] == "done"
    tasks = {t["task_id"]: t for t in hub.get(f"/v1/runs/{run_id}/tasks").json()["tasks"]}
    assert (tasks["a"]["status"], tasks["a"]["detail"]) == ("failed", "the step limit (1) was reached")
    assert tasks["b"]["status"] == "done"  # it did not wait for a
    assert (tasks["c"]["status"], tasks["c"]["detail"], tasks["c"]["run_id"]) == ("skipped", "a failed", None)
    (board,) = outputs(hub, run_id)
    assert board["error"] is True and board["output"].startswith("3 tasks: 1 done, 1 failed, 1 skipped.")
    assert "[c] «Ревьюер» — skipped: a failed" in board["output"]


def test_local_tasks_take_the_gpu_one_at_a_time_grouped_by_model(hub):  # noqa: F811
    tasks = batch(("x", "Первый", "x"), ("y", "Второй", "y"), ("z", "Третий", "z"))
    provider = plug(hub, ByTask("fake", "local", {"Сделай дело": [[tasks]]}, pause=0.1, refs=("echo", "other")))
    profile(hub, name="Первый", tools=[])
    profile(hub, name="Второй", model_id="fake/other", tools=[])
    profile(hub, name="Третий", tools=[])
    boss = profile(hub, name="Координатор", tools=["delegate_tasks"])  # local too: it gives up the GPU meanwhile
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай дело"}).json()["id"]
    assert ended(hub, run_id)["status"] == "done"
    assert provider.peak == 1  # one model on the GPU at a time
    assert provider.started == [("echo", "x"), ("echo", "z"), ("other", "y")]  # llama-swap loads «other» once


def test_stopping_the_coordinator_stops_the_whole_batch(hub):  # noqa: F811
    provider = plug(hub, ByTask("cloudy", "cloud", {"Сделай дело": [[batch(
        ("a", "Писатель", "долго"), ("b", "Ревьюер", "потом", ["a"]))]]}, pause=30))  # fmt: skip
    for name in ("Писатель", "Ревьюер"):
        profile(hub, name=name, model_id="cloudy/echo", tools=[])
    boss = profile(hub, name="Координатор", model_id="cloudy/echo", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай дело"}).json()["id"]
    wait(lambda: provider.active == 1)
    hub.post(f"/v1/runs/{run_id}/cancel")
    assert ended(hub, run_id)["status"] == "cancelled"
    tasks = {t["task_id"]: t for t in hub.get(f"/v1/runs/{run_id}/tasks").json()["tasks"]}
    assert tasks["a"]["status"] == "cancelled" and ended(hub, tasks["a"]["run_id"])["status"] == "cancelled"
    assert (tasks["b"]["status"], tasks["b"]["run_id"]) == ("cancelled", None)  # it never began


def test_tasks_not_begun_when_the_hub_stopped_are_cancelled(hub):  # noqa: F811
    plug(hub, ByTask("fake", "local", {}))
    made = profile(hub, name="Координатор", tools=["delegate_tasks"])

    def interrupted(conn):  # a batch whose second task had not begun when the hub stopped
        run_id = agents.create_run(conn, agents.get_profile(conn, made["id"]), "Сделай", [])
        conn.execute("UPDATE agent_runs SET status = 'done' WHERE conversation_id = ?", (run_id,))
        agents.save_batch(conn, run_id, [
            {"task_id": "a", "agent_profile": "Координатор", "prompt": "x", "dependencies": []},
            {"task_id": "b", "agent_profile": "Координатор", "prompt": "y", "dependencies": ["a"]}])  # fmt: skip
        conn.execute("UPDATE delegated_tasks SET status = 'done', answer = 'ok' WHERE task_id = 'a'")
        return run_id

    run_id = hub.app.state.db.call(interrupted)
    hub.portal.call(hub.app.state.agents.start)
    tasks = hub.get(f"/v1/runs/{run_id}/tasks").json()["tasks"]
    assert [(t["task_id"], t["status"], t["detail"]) for t in tasks] == [
        ("a", "done", None), ("b", "cancelled", "the hub restarted before it began")]  # fmt: skip
    assert hub.get("/v1/runs/nope/tasks").status_code == 404
