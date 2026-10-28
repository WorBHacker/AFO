"""The hub's own agents (stage 5.3, D16): profiles with versions, runs with their role, tools, rights and limits, the
GPU queue, stopping, and going on after a restart."""

import asyncio
import time

from hub import agents
from hub.chat import Limits
from hub.gateway.base import Model
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
    assert "delegate_task" not in names and "web_fetch" in names
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
