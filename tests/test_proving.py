"""The proving ground and the models' exam (stage 12, D45): the tasks and their contracts; every task as given fails
its hidden check, a right solution passes it, edited tests are caught; a try on a fresh copy - its chat and project
hidden, nobody answers there; a run that games the visible check fails the hidden one; an exam of two models with
its table; stopping, deleting, a hub restart."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from hub import proving
from tests.test_autonomous import Scripted, use
from tests.test_changes import wait
from tests.test_goals import Gated
from tests.test_tools import hub  # noqa: F401 - the hub fixture

FIZZ = ('def fizzbuzz(n: int) -> str:\n    if n % 15 == 0:\n        return "FizzBuzz"\n    if n % 3 == 0:\n'
        '        return "Fizz"\n    if n % 5 == 0:\n        return "Buzz"\n    return str(n)\n')  # fmt: skip
SOLUTIONS = {
    "fizzbuzz": {"fizzbuzz.py": FIZZ},
    "csv_report": {
        "report.json": json.dumps({"Север": 200.5, "Юг": 103.0, "Запад": 41.0, "Восток": 282.39}, ensure_ascii=False)
    },
    "log_parse": {
        "logparse.py": "import re\n\nCODE = re.compile(r'E\\d+')\n\n\ndef count_errors(lines):\n"
        "    out = {}\n    for line in lines:\n        parts = line.split()\n"
        "        if len(parts) >= 4 and parts[2] == 'ERROR' and CODE.fullmatch(parts[3]):\n"
        "            out[parts[3]] = out.get(parts[3], 0) + 1\n    return out\n"
    },
    "refactor": {
        "inventory.py": "def price_with_tax(price: float) -> float:\n    return round(price * 1.2, 2)\n\n\n"
        "def item_price(item):\n    return price_with_tax(item['price'])\n\n\n"
        "def order_total(items):\n    total = 0.0\n    for item in items:\n"
        "        total += price_with_tax(item['price']) * item['qty']\n    return round(total, 2)\n\n\n"
        "def cheapest(items):\n    prices = [price_with_tax(item['price']) for item in items]\n"
        "    return min(prices) if prices else 0.0\n"
    },
    "shop_bug": {
        "shop/cart.py": "from . import discounts\nfrom .money import show, to_cents\n\n\nclass Cart:\n"
        "    def __init__(self):\n        self.lines = []\n\n    def add(self, name, price, qty=1):\n"
        "        self.lines.append((name, to_cents(price), qty))\n\n    def subtotal(self, percent=0):\n"
        "        return sum(c * q for _n, c, q in self.lines)\n\n    def total(self, percent=0):\n"
        "        return show(discounts.apply(self.subtotal(), percent))\n"
    },
    "readme_docs": {
        "README.md": "# tool.py\n\nPrints the most frequent words.\n\n- `--input` the file (required)\n"
        "- `--limit` how many, default 10\n- `--min-length` shortest word, default 3\n"
        "- `--format` json or csv, default json\n- `--verbose` counts\n\n"
        "Example: `python tool.py --input text.txt --limit 5`\n"
    },
}
# a fix that passes the visible tests but not the hidden check: the discount taken per line, not from the whole sum
SHOP_PER_LINE = SOLUTIONS["shop_bug"]["shop/cart.py"].replace(
    "return sum(c * q for _n, c, q in self.lines)",
    "return sum(discounts.apply(c * q, percent) for _n, c, q in self.lines)").replace(
    "return show(discounts.apply(self.subtotal(), percent))", "return show(self.subtotal(percent))")  # fmt: skip


def hidden(task_id: str, folder: Path) -> int:
    script = proving.TASKS_DIR / task_id / "exam" / "check.py"
    return subprocess.run([sys.executable, "-B", str(script)], cwd=folder, capture_output=True).returncode


def fresh(task_id: str, changes: dict[str, str]) -> Path:
    folder = Path(tempfile.mkdtemp(prefix=f"task-{task_id}-"))
    shutil.copytree(proving.TASKS_DIR / task_id / "files", folder, dirs_exist_ok=True)
    for name, text in changes.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_the_tasks_and_their_contracts():
    found = proving.tasks()
    assert {t["id"] for t in found} == set(SOLUTIONS)
    assert [t["level"] for t in found] == sorted((t["level"] for t in found), key=proving.LEVELS.get)  # easy first
    fizz = next(t for t in found if t["id"] == "fizzbuzz")
    assert proving.python_command() in fizz["objective"] and "{python}" not in fizz["objective"]
    assert fizz["hidden"] == 1 and "exam" not in json.dumps(found)  # the hidden checks' commands are not shown
    spec = proving.task("fizzbuzz")
    goal = proving.contract(spec, "полигон · Почини FizzBuzz", "agent/m", judge=False)
    assert goal["criteria"] == [{"command": f"{proving.python_command()} -m unittest -q",
                                 "project": "полигон · Почини FizzBuzz", "expected_exit_code": 0}]  # fmt: skip
    assert goal["judge_model_id"] == "none" and goal["limits"] == {"max_turns": 6, "max_minutes": 10}
    assert proving.contract(spec, "p", "m", judge=True)["judge_model_id"] == ""
    assert proving.python_command().endswith('python.exe"')  # the console one: pythonw has no output
    with pytest.raises(proving.ProvingMissing):
        proving.task("../secret")


@pytest.mark.parametrize("task_id", sorted(SOLUTIONS))
def test_every_task_is_hard_to_fake(task_id):
    folders = [fresh(task_id, {}), fresh(task_id, SOLUTIONS[task_id])]
    try:
        assert hidden(task_id, folders[0]) != 0  # as given: not done
        assert hidden(task_id, folders[1]) == 0  # a right solution passes
        if task_id != "csv_report":  # editing the tests (or the code to document) is caught
            edited = folders[1] / next(p.relative_to(folders[1]) for p in folders[1].rglob("*.py")
                                       if p.name.startswith(("test_", "tool")))  # fmt: skip
            edited.write_text("# emptied\n", encoding="utf-8")
            assert hidden(task_id, folders[1]) != 0
        if task_id == "shop_bug":
            folders.append(fresh(task_id, {"shop/cart.py": SHOP_PER_LINE}))
            assert hidden(task_id, folders[-1]) != 0  # the per-line discount: the visible test passes, not this
    finally:
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)


FIZZ_PROJECT = "полигон · Почини FizzBuzz"


def write(project, path, content):
    return [("write_file", {"project": project, "path": path, "content": content})]


def ran(hub, run_id, *statuses):  # noqa: F811 - the fixture's name
    return wait(lambda: (r := hub.get("/v1/proving/runs").json()["runs"]) and next(
        (x for x in r if x["id"] == run_id and x["status"] in statuses), None), timeout=120)  # fmt: skip


def test_a_try_on_a_fresh_copy(hub):  # noqa: F811
    worker = use(hub, Gated("agent", [
        [("ask_user", {"question": "Можно менять тесты?"})],
        [("run_command", {"project": FIZZ_PROJECT, "command": "git push origin main"})],
        write(FIZZ_PROJECT, "fizzbuzz.py", FIZZ), "Починил.",
    ]))  # fmt: skip
    found = hub.post("/v1/proving/try", json={"task_id": "fizzbuzz", "model_id": "agent/m"})
    assert found.status_code == 201, found.text
    run = found.json()["run"]
    assert run["status"] == "running" and run["conversation_id"] and run["model_name"] == "Agent"
    done = ran(hub, run["id"], "passed", "failed")
    assert done["status"] == "passed" and done["detail"] == "сдано" and done["goal_status"] == "achieved"
    assert done["checks"][0]["passed"] and done["turns"] == 1 and done["tokens"] > 0
    said = [t["content"] for p in worker.prompts for t in p if t["role"] == "tool"]
    assert any("nobody answers on the proving ground" in s for s in said)  # the question: answered at once
    assert any("on the proving ground nobody approves" in s for s in said)  # the destructive command: refused
    assert hub.get("/v1/approvals").json()["approvals"] == []
    told = hub.app.state.db.call(lambda c: [json.loads(r[0]) for r in c.execute(
        "SELECT payload FROM events WHERE type = 'goal.status'").fetchall()])  # fmt: skip
    assert told and all(e["proving"] for e in told)  # the run's goal: no notice for the person, nor Telegram
    folder = Path(done["folder"])
    assert (folder / ".git").exists() and (folder / "fizzbuzz.py").read_text(encoding="utf-8") == FIZZ
    assert done["conversation_id"] not in [c["id"] for c in hub.get("/v1/conversations").json()["conversations"]]
    assert FIZZ_PROJECT not in [p["name"] for p in hub.get("/v1/projects").json()["projects"]]
    chat = hub.get(f"/v1/conversations/{done['conversation_id']}").json()  # opened from the proving ground
    assert chat["title"] == "Полигон: Почини FizzBuzz · Agent" and chat["context"]["mode"] == "trusted"
    assert hub.delete(f"/v1/proving/runs/{run['id']}").status_code == 204
    assert not folder.exists() and hub.get("/v1/proving/runs").json()["runs"] == []


def test_the_exam_table_and_a_hidden_check_that_catches_gaming(hub):  # noqa: F811
    project = "полигон · Отчёт по продажам"
    right = json.dumps({"Север": 200.5, "Юг": 103.0, "Запад": 41.0, "Восток": 282.39}, ensure_ascii=False)
    use(hub, Scripted("good", [write(project, "report.json", right), "Готово."]))
    use(hub, Scripted("lazy", [write(project, "report.json", '{"Север": 1}'), "Готово."]))  # passes Test-Path only
    started = hub.post("/v1/proving/exams", json={"model_ids": ["good/m", "lazy/m"], "task_ids": ["csv_report"]})
    assert started.status_code == 201, started.text
    exam_id = started.json()["exam"]["id"]
    assert hub.post("/v1/proving/exams", json={"model_ids": ["good/m"]}).status_code == 422  # one at a time
    exam = wait(lambda: (e := hub.get(f"/v1/proving/exams/{exam_id}").json()["exam"])["status"] == "done" and e,
                timeout=180)  # fmt: skip
    good, lazy = exam["runs"]
    assert (good["status"], lazy["status"]) == ("passed", "failed")
    assert lazy["goal_status"] == "achieved" and lazy["detail"].startswith("скрытая проверка не прошла: в report.json")
    assert [(s["model_id"], s["passed"], s["tasks"]) for s in exam["summary"]] == [("good/m", 1, 1), ("lazy/m", 0, 1)]
    assert hub.get("/v1/proving/exams").json()["exams"][0]["id"] == exam_id
    folders = [Path(r["folder"]) for r in exam["runs"]]
    assert all(f.exists() for f in folders)
    assert hub.delete(f"/v1/proving/exams/{exam_id}").status_code == 204
    assert not any(f.exists() for f in folders) and hub.get("/v1/proving/exams").json()["exams"] == []
    assert hub.get(f"/v1/proving/exams/{exam_id}").status_code == 404


def test_stopping_an_exam_and_what_it_refuses(hub):  # noqa: F811
    worker = use(hub, Gated("agent", ["Думаю."] * 20))
    worker.gate.clear()
    assert hub.post("/v1/proving/exams", json={"model_ids": ["nope/x"]}).status_code == 422
    assert hub.post("/v1/proving/try", json={"task_id": "nope", "model_id": "agent/m"}).status_code == 404
    body = {"model_ids": ["agent/m"], "task_ids": ["fizzbuzz", "readme_docs"]}
    exam_id = hub.post("/v1/proving/exams", json=body).json()["exam"]["id"]
    wait(worker.waiting.is_set)
    stopping = hub.post(f"/v1/proving/exams/{exam_id}/stop")
    assert stopping.status_code == 202
    worker.gate.set()
    exam = wait(lambda: (e := hub.get(f"/v1/proving/exams/{exam_id}").json()["exam"])["status"] == "stopped" and e,
                timeout=120)  # fmt: skip
    assert [r["status"] for r in exam["runs"]] == ["failed", "skipped"]
    assert exam["runs"][0]["goal_status"] == "cleared"


def test_a_restart_stops_what_was_going(hub):  # noqa: F811
    ground = hub.app.state.proving
    worker = use(hub, Gated("agent", ["Думаю."]))
    worker.gate.clear()
    run = hub.post("/v1/proving/try", json={"task_id": "fizzbuzz", "model_id": "agent/m"}).json()["run"]
    wait(worker.waiting.is_set)
    assert hub.portal.call(ground.start) == 1  # as a new hub does: the run is stopped, its goal cleared
    worker.gate.set()
    stopped = hub.get("/v1/proving/runs").json()["runs"][0]
    assert stopped["id"] == run["id"] and stopped["status"] == "stopped" and stopped["detail"] == "хаб перезапустился"
    assert hub.get(f"/v1/conversations/{run['conversation_id']}/goal").json()["goal"]["status"] == "cleared"


def test_a_copy_commits_whatever_the_users_own_git_says(tmp_path, monkeypatch):
    """The audit (D46): the user's global git signs every commit and runs a hook that fails - a task's copy is not
    their project: it gets its commit all the same (before, the failure was swallowed and the copy had none)."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\necho 'the user hook says no' >&2\nexit 1\n", encoding="utf-8",
                                      newline="\n")  # fmt: skip
    config = tmp_path / "gitconfig"
    config.write_text(f"[commit]\n\tgpgsign = true\n[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    spec = next(s for s in proving.tasks() if s["id"] == "fizzbuzz")
    copy = tmp_path / "copy"
    proving.copy_task(spec, copy)
    log = subprocess.run(["git", "-C", str(copy), "log", "--format=%s"], capture_output=True, text=True)
    assert log.stdout.strip() == "the task as given"
