"""The judge of goals (stage 11.2, D44, Q26): its answer read from JSON; tests lost found by the hub itself; the files'
starting point as a git tree that leaves the user's index, branches and log alone; the syntax of changed files in a
process of its own; the agent's «goal met» checked - sent back while the judge disagrees, then kept as «not met»."""

import json
import subprocess

from hub import judge, syntax
from hub.gateway.base import Model
from tests.test_autonomous import PLAN, Scripted, ended, start, step, use
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def test_the_judges_answer_is_read_from_its_json():
    plain = judge.parse('{"status": "achieved", "missing_items": [], "feedback_for_worker": ""}')
    assert plain == {"status": "ACHIEVED", "missing": [], "feedback": ""}
    fenced = judge.parse('Вот:\n```json\n{"status": "INCOMPLETE", "missing_items": ["нет  теста"], '
                         '"feedback_for_worker": "Допиши тест"}\n```')  # fmt: skip
    assert fenced == {"status": "INCOMPLETE", "missing": ["нет теста"], "feedback": "Допиши тест"}
    assert judge.parse('{"status": "MAYBE"}') is None and judge.parse("всё хорошо") is None
    assert judge.parse('{"status": "REGRESSED", "missing_items": "удалён тест"}')["missing"] == ["удалён тест"]


def test_tests_lost_are_found_without_a_model():
    removed = "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1,4 +1,1 @@\n-def test_one():\n-    assert f()\n" \
              " import app\n"  # fmt: skip
    assert judge.sabotage({"tests/test_app.py": removed}) == ["tests/test_app.py: test(s) removed - test_one"]
    moved = {"tests/test_app.py": removed, "tests/test_more.py": "+def test_one():\n+    assert f()\n"}
    assert judge.sabotage(moved) == []  # moved to another file is not lost
    skipped = "+@pytest.mark.skip(reason='later')\n def test_two():\n"
    assert "skipped now" in judge.sabotage({"tests/test_app.py": skipped})[0]
    hidden = "-def test_three():\n+# def test_three():\n"
    found = judge.sabotage({"src/app.test.js": "+it.skip('works', () => {})\n", "tests/test_x.py": hidden})
    assert len(found) == 2 and "commented out" in found[1] and "removed" not in " ".join(found)
    assert judge.sabotage({"src/app.py": "+@pytest.mark.skip\n-def test_old():\n"}) == []  # not a test file


def test_the_starting_point_leaves_the_users_git_alone(repo):
    before = (git(repo, "status", "--porcelain"), git(repo, "for-each-ref"), git(repo, "log", "--oneline"))
    base = judge.snapshot(repo)
    (repo / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8", newline="\n")
    (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".env").write_text("API_KEY=sk-secret-value-1234567890\n", encoding="utf-8")
    (repo / "README.md").unlink()
    head = judge.snapshot(repo)
    found = {c["path"]: c["status"] for c in judge.tree_changes(repo, base, head)}
    assert found == {"src/app.py": "changed", "new.py": "added", "README.md": "deleted"}  # no secret file
    patch = judge.tree_patch(repo, base, head, list(found))
    assert "+print('changed')" in patch and "+x = 1" in patch and "sk-secret" not in patch
    after = (git(repo, "status", "--porcelain"), git(repo, "for-each-ref"), git(repo, "log", "--oneline"))
    assert after[1:] == before[1:] and git(repo, "diff", "--cached") == ""  # no ref, no commit, the index as it was
    assert "?? new.py" in after[0] and "?? .env" in after[0]  # still the user's untracked files


def test_changed_files_must_still_parse(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.js").write_text("function f() {\n  return 1;\n}\nconst a = ;\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("(((", encoding="utf-8")
    found = syntax.check([tmp_path / n for n in ("bad.py", "ok.py", "bad.js", "notes.txt")])
    assert [(e["path"].rsplit("\\", 1)[-1], e["line"]) for e in found] == [("bad.py", 1), ("bad.js", 4)]


def test_the_evidence_from_a_git_project(repo):
    from hub.projects import Project

    project = Project("p1", "proj", str(repo), "", False, "", "")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("def test_one():\n    assert True\n", encoding="utf-8", newline="\n")
    base = judge.snapshot(repo)
    skipped = "import pytest\n\n@pytest.mark.skip\ndef test_one():\n    assert True\n"
    (repo / "tests" / "test_app.py").write_text(skipped, encoding="utf-8", newline="\n")
    (repo / "src" / "app.py").write_text("def f(:\n", encoding="utf-8", newline="\n")
    (repo / "config.py").write_text("KEY = 'sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789'\n", encoding="utf-8")
    found = judge.gather([project], {"p1": base}, {})
    assert found.syntax == [{"project": "proj", "path": "src/app.py", "line": 1, "error": "invalid syntax"}]
    assert len(found.sabotage) == 1 and "skipped now" in found.sabotage[0]
    shown = judge.workspace_text(found)
    assert "changed src/app.py" in shown and "added config.py" in shown and "sk-ant-api03" not in shown
    assert "does not parse" in shown and found.summary()[0]["how"] == "git"


class Cloudy(Scripted):
    """A scripted judge that is a cloud model."""

    def __init__(self, name, script):
        super().__init__(name, script)
        self.models = [Model(f"{name}/m", name, "m", "Облачный судья", "cloud", {}, {})]


def roles(hub, **chosen):  # noqa: F811 - the fixture's name
    response = hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": chosen}})
    assert response.status_code == 200, response.text


def test_the_agents_goal_met_is_checked_by_the_judge(hub):  # noqa: F811
    no = json.dumps({"status": "INCOMPLETE", "missing_items": ["число не сверено с файлом"],
                     "feedback_for_worker": "Перечитай README"})  # fmt: skip
    referee = use(hub, Scripted("judge", [no, no, no, '{"status": "ACHIEVED"}']))
    finish = ("finish", {"summary": "В README 12 строк.", "goal_met": True})
    agent = use(hub, Scripted("agent", [[PLAN], [step(1, "running")], [step(1, "done", result="ok", check="прочитал")],
                                        [finish], [finish], [finish], "Готово."]))  # fmt: skip
    roles(hub, judge="judge/m")
    run = ended(hub, start(hub))
    said = [t["content"] for p in agent.prompts for t in p if t["role"] == "tool" and "judge" in t["content"]]
    assert said[0].startswith("not saved: the judge does not agree that the goal is met (INCOMPLETE). Missing: "
                              "число не сверено с файлом. Перечитай README")  # fmt: skip
    assert run["report"]["goal_met"] is False and run["report"]["judge"]["status"] == "INCOMPLETE"  # it insisted
    assert len(referee.prompts) == 3
    asked = referee.prompts[0][1]["content"]
    assert "OBJECTIVE: Посчитать строки в README" in asked and "[done] Прочитать README - ok" in asked
    assert '<worker_claims unverified="true">\nВ README 12 строк.' in asked
    assert referee.prompts[0][0]["content"].startswith("You are an independent auditor")

    other = use(hub, Scripted("agent", [[PLAN], [step(1, "done", result="ok", check="прочитал")], [finish], "Всё."]))
    agreed = ended(hub, start(hub))
    assert agreed["report"]["goal_met"] is True and agreed["report"]["judge"]["status"] == "ACHIEVED"
    assert other.prompts and len(referee.prompts) == 4
    metas = hub.app.state.db.call(lambda c: [json.loads(r[0]) for r in c.execute(
        "SELECT meta FROM usage WHERE model_id = 'judge/m'")])  # fmt: skip
    assert len(metas) == 4 and all(m["purpose"] == "judge" and m["run_id"] for m in metas)  # counted like any answer


def test_a_cloud_judge_sees_no_local_only_project(hub, repo):  # noqa: F811
    use(hub, Cloudy("sky", []))
    use(hub, Scripted("agent", []))
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": False})
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})
    body = {"objective": "x", "model_id": "agent/m", "criteria": [{"command": "exit 0"}], "judge_model_id": "sky/m"}
    refused = hub.put(f"/v1/conversations/{conv}/goal", json=body)
    assert refused.status_code == 422 and "only local models may see" in refused.json()["detail"]
    roles(hub, judge="sky/m")  # the role says the same when the goal names none
    body.pop("judge_model_id")
    assert "cloud model" in hub.put(f"/v1/conversations/{conv}/goal", json=body).json()["detail"]


def test_a_file_with_cyrillic_in_its_name_is_seen_as_it_is(repo):
    """The audit (D46): git wrote such a path as "\320\276..." - the judge then could not read or check the file."""
    before = judge.snapshot(repo)
    (repo / "отчёт за год.md").write_text("# Итог\n", encoding="utf-8")
    after = judge.snapshot(repo)
    assert {"path": "отчёт за год.md", "status": "added"} in judge.tree_changes(repo, before, after)
