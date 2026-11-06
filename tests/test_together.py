"""Stages 11, 17 and 18 together (2026-11-06, the user's word «проводи общий тест»): what each does when it meets the
others - a goal in «Песочница» with a part applied meanwhile and a dead end after it; a goal's stages with the loop
guard and with two models; a rollback card over the editor's unsaved edits; Claude Code's goal with its edit allowed
in part; the symbol index of a sandbox and of its project.

One passed, six showed one part breaking another: each is a step of stage 19 (D53) and waits for it, marked
`step(...)` - strict, so a scenario that starts to pass says so and its step takes the mark away."""

import pytest

from hub.claude_code import ClaudeCodeProvider
from hub.projects import get_project
from tests import test_claude_code as cc
from tests.test_autonomous import use
from tests.test_changes import wait
from tests.test_goals import DEAD_END, DONE, WRITE, Gated, chat_with_project, set_goal, settled
from tests.test_tools import hub  # noqa: F401 - the hub fixture

A_TXT = "if (Test-Path a.txt) { exit 0 } else { exit 1 }"


def step(n, why):
    return pytest.mark.xfail(strict=True, reason=f"stage 19.{n} (D53): {why}")


def written(path, content):
    return [("write_file", {"project": "proj", "path": path, "content": content})]


def test_a_dead_end_after_a_part_was_applied_does_not_take_it_back(hub, repo):  # noqa: F811
    """17.1 × 11.6: the user pauses a goal in «Песочница», applies one of the files the model made, resumes; a dead
    end later goes back to the goal's start - the file the user took must not come back as a change that deletes it."""
    two = [*written("a.txt", "нужное\n"), *written("b.txt", "черновик\n")]
    worker = use(hub, Gated("agent", [two, "Сделал a.txt и b.txt.", *DEAD_END, WRITE, "Готово."]))
    worker.gate.clear()
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    set_goal(hub, conv)
    wait(worker.waiting.is_set)
    assert hub.post(f"/v1/conversations/{conv}/goal/pause").status_code < 300
    worker.gate.set()
    settled(hub, conv, "paused")
    applied = hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply", json={"files": ["a.txt"]})
    assert applied.status_code == 200 and applied.json()["applied"] == ["a.txt"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "нужное\n"
    assert hub.post(f"/v1/conversations/{conv}/goal/resume", json={}).status_code < 300
    done = settled(hub, conv, "achieved", "blocked")
    assert [c["route"] for c in done["checks"]] == ["continue", "investigate", "investigated", "rolled_back",
                                                    "achieved"]  # fmt: skip
    files = hub.get(f"/v1/conversations/{conv}/sandbox").json()["projects"][0]["files"]
    shown = {f["path"]: f["status"] for f in files}
    assert "a.txt" not in shown, f"after the rollback the sandbox offers to undo what the user applied: {shown}"


def test_a_goal_goes_on_with_what_the_user_applied(hub, repo):  # noqa: F811
    """17.1 × 11: the user pauses a goal in «Песочница» and applies all it made - the sandbox is taken away; resumed,
    the goal works in a new one. What the user took is the project's now: the goal must not lose it."""
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "Сделал a.txt.", WRITE, "Готово."]))
    worker.gate.clear()
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    set_goal(hub, conv, criteria=[{"command": A_TXT}, {"command": DONE}])
    wait(worker.waiting.is_set)
    assert hub.post(f"/v1/conversations/{conv}/goal/pause").status_code < 300
    worker.gate.set()
    settled(hub, conv, "paused")
    assert hub.post(f"/v1/conversations/{conv}/sandbox/{pid}/apply").json()["applied"] == ["a.txt"]
    assert (repo / "a.txt").exists()
    assert hub.post(f"/v1/conversations/{conv}/goal/resume", json={}).status_code < 300
    done = settled(hub, conv, "achieved", "blocked", "budget")
    first = done["checks"][0]
    assert done["goal"]["status"] == "achieved", (
        f"{done['goal']['detail']!r}; after it: {[(c['route'], c['missing']) for c in done['checks']][:2]}"
    )
    assert first["route"] == "achieved"


def test_stages_that_go_on_are_not_going_round_in_circles(hub, repo):  # noqa: F811
    """11.5 × 11.4: a goal in three stages whose only criterion is met at the end - each turn closes a stage, the
    criterion's failure stays the same until the last one. That is progress, not a loop."""
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "STAGE DONE: создан a.txt", written("b.txt", "b\n"),
                                      "STAGE DONE: создан b.txt", WRITE, "STAGE DONE: создан done.txt"]))  # fmt: skip
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, milestones=["Создать a.txt", "Создать b.txt", "Создать done.txt"])
    done = settled(hub, conv, "achieved", "blocked")
    routes = [c["route"] for c in done["checks"]]
    assert routes == ["continue", "continue", "achieved"], f"routes {routes}, the goal {done['goal']['detail']!r}"


@step(5, "the plan is folded away with the first stage")
def test_the_strong_models_plan_outlives_the_first_stage(hub, repo):  # noqa: F811
    """11.7 × 11.5: the strong model writes the plan in its first turn «for the lighter model»; once the first stage
    is done its turns go to the model as one line - the plan must still reach the next turns."""
    use(hub, Gated("strong", ["ПЛАН: сначала a.txt, потом done.txt в корне проекта."]))
    worker = use(hub, Gated("agent", [written("a.txt", "a\n"), "STAGE DONE: создан a.txt", WRITE, "Готово."]))
    _pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv, criteria=[{"command": A_TXT}, {"command": DONE}], strong_model_id="strong/m",
             milestones=["Создать a.txt", "Создать done.txt"])  # fmt: skip
    done = settled(hub, conv, "achieved", "blocked")
    assert done["goal"]["milestones"][0]["status"] == "done"
    later = worker.prompts[2]  # the first request of the turn after stage 1
    assert "ПЛАН" in str(later), "the plan was folded away with stage 1"


@step(6, "the rollback card does not look at the editor's unsaved files")
def test_a_rollback_card_over_the_editors_unsaved_edits_says_so(hub, repo):  # noqa: F811
    """11.6 × D47 (14.2c): in the project a dead end asks to go back; a file it would remove or put back is open in
    the window's editor with unsaved edits - the card must say so, as a model's write into it does."""
    junk = written("junk.txt", "мусор\n")
    worker = use(hub, Gated("agent", [junk, *DEAD_END, WRITE, "Готово."]))
    pid, conv = chat_with_project(hub, repo, worker)
    set_goal(hub, conv)
    settled(hub, conv, "blocked")
    assert hub.put("/v1/editor/dirty", json={"files": [{"project_id": pid, "path": "junk.txt"}]}).status_code == 200
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "goal_rollback" and "junk.txt" in asked["preview"]["text"]
    assert asked["unsaved"] is True, "the rollback would remove a file the editor holds unsaved, and the card is silent"


def test_a_sandboxs_definitions_stay_out_of_its_projects_index(hub, repo):  # noqa: F811
    """«Песочница» × the symbol index (18.3 reads it by meaning): the sandbox's copy and the project share the
    project's id - a definition only the sandbox has must not turn up in the project's own chat."""
    worker = use(hub, Gated("agent", []))
    pid, conv = chat_with_project(hub, repo, worker, mode="sandbox")
    (copy,) = hub.portal.call(hub.app.state.chat.tool_projects, conv)
    real = hub.app.state.db.call(lambda c: get_project(c, pid))
    assert copy.root != real.root
    (copy.root / "src" / "helper.py").write_text('def sandbox_only_helper():\n    """Helps in the sandbox only."""\n',
                                                encoding="utf-8")  # fmt: skip
    symbols = hub.app.state.chat.symbols
    assert symbols.find(copy, "sandbox_only_helper")["total"] == 1  # the sandbox's chat finds it
    by_name = symbols.find(real, "sandbox_only_helper")["total"]
    by_meaning = [s["name"] for s in symbols.find(real, "helps sandbox")["symbols"]]
    assert (by_name, by_meaning) == (0, []), "the project's chat finds what only the sandbox has"


# --- Claude Code (a live hub, the stand-in CLI) ---


@pytest.fixture
def live(repo, tmp_path, monkeypatch):
    log = tmp_path / "fake-claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    server = cc.LiveHub()
    try:
        server.app.state.claude_code._find = lambda: cc.FAKE
        server.app.state.gateway.register(ClaudeCodeProvider(lambda: cc.FAKE))
        assert server.http.post("/v1/models/sync").status_code == 200
        server.repo, server.log = repo, log
        server.pid = server.http.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        server.http.patch(f"/v1/projects/{server.pid}", json={"cloud_ok": True})
        yield server
    finally:
        server.close()


def test_claude_codes_goal_with_its_edit_allowed_in_part(live):
    """11.7 × 17.3: Claude Code works on a goal in a chat that asks; the user allows two of its edit's three hunks -
    the hub writes them, Claude Code hears «no» with what went, and the goal's check finds the criterion met."""
    lines = "".join("x = 1\n" if n in (1, 20, 40) else f"# {n}\n" for n in range(1, 41))
    (live.repo / "conf.py").write_text(lines, encoding="utf-8", newline="\n")
    cid = cc.chat_with_project(live)
    cc.set_goal(live, cid)
    asked = live.wait_approval()
    assert asked["tool"] == "Edit" and len(asked["preview"]["hunks"]) == 3
    assert live.http.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "hunks": [0, 2]}).status_code == 200
    done = cc.goal_settled(live, cid, "achieved", "blocked", "budget")
    assert done["goal"]["status"] == "achieved", done["goal"]["detail"]
    now = (live.repo / "conf.py").read_text(encoding="utf-8").splitlines()
    assert (now[0], now[19], now[39]) == ("x = 2", "x = 1", "x = 2")
