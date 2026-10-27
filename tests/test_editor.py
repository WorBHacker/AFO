"""What VS Code reports (stage 6.6): the open file and the commands of its terminals reach the hub, its feed, and the
live state the models of an attached chat read."""

from datetime import UTC, datetime
from types import SimpleNamespace

from hub import context, editor
from tests.test_tools import hub  # noqa: F401 - the hub fixture
from tests.test_tools import project as make_project

RED = "\x1b[31m"


def test_output_becomes_plain_text():
    numbered = "".join(f"line {n}\r\n" for n in range(40))
    raw = f"{RED}FAILED\x1b[0m tests/test_a.py\r\n" + numbered + "\x1b]633;D;1\x07\n\n"  # colours, CRs, a VS Code mark
    out = editor.plain_output(raw + "token sk-ant-api03-" + "k" * 40 + "\n")
    lines = out.splitlines()
    assert len(lines) == editor.OUTPUT_LINES and "\x1b" not in out and "\r" not in out
    assert lines[-1] == "token [REDACTED]" and lines[0] == "line 18"


def test_the_project_a_path_is_in():
    outer = SimpleNamespace(id="o", root="D:\\Work")
    inner = SimpleNamespace(id="i", root="D:\\Work\\app")
    assert editor.project_of("d:/work/APP/src/main.py", [outer, inner]) == (inner, "src/main.py")
    assert editor.project_of("D:\\Work\\notes.md", [outer, inner]) == (outer, "notes.md")
    assert editor.project_of("D:\\Work\\app", [outer, inner]) == (inner, "")
    assert editor.project_of("D:\\Workshop\\x.py", [outer, inner]) == (None, "")


def test_what_vs_code_reports(hub, repo):  # noqa: F811
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    shown = hub.post("/v1/editor/focus", json={"path": str(repo / "src" / "app.py"), "line": 3}).json()
    assert shown["project_id"] == pid and shown["rel"] == "src/app.py" and shown["line"] == 3
    assert hub.app.state.editor.open_in(pid) == "src/app.py:3"
    hub.app.state.editor._focus["at"] -= editor.FOCUS_FRESH_S + 1
    assert hub.app.state.editor.open_in(pid) == ""  # reported over an hour ago: not "open now"

    failed = {"terminal": "pwsh", "cwd": str(repo), "command": "pytest  -q", "exit_code": 1,
              "output": f"{RED}E   assert 1 == 2\x1b[0m\r\n1 failed\r\n", "duration_s": 2.345}  # fmt: skip
    payload = hub.post("/v1/editor/command", json=failed).json()
    assert payload["command"] == "pytest -q" and payload["output"] == "E   assert 1 == 2\n1 failed"
    assert payload["project"] == repo.name and payload["duration_s"] == 2.3
    hub.post("/v1/editor/command", json={"cwd": "C:\\Elsewhere", "command": "dir", "exit_code": 0})
    listed = hub.get("/v1/editor").json()
    assert [c["command"] for c in listed["commands"]] == ["dir", "pytest -q"]
    assert listed["commands"][1]["project_id"] == pid and listed["commands"][0]["project_id"] is None
    feed = hub.get("/v1/feed", params={"types": "editor.command", "project_id": pid}).json()["events"]
    assert [e["payload"]["command"] for e in feed] == ["pytest -q"]


def test_the_live_state_tells_the_model(repo):
    project = make_project(repo)
    event = {"ts": datetime.now(UTC).isoformat(), "type": "editor.command",
             "payload": {"command": "pytest -q", "exit_code": 1, "output": "E   assert 1 == 2\n1 failed"}}  # fmt: skip
    text = context.live_layer(project, processes=[], terminals=[], events=[event], now=datetime.now(UTC),
                              editor_file="src/app.py:3")  # fmt: skip
    assert "VS Code: the user has src/app.py:3 open" in text
    assert "VS Code terminal: `pytest -q` exited with code 1\n    | E   assert 1 == 2\n    | 1 failed" in text
