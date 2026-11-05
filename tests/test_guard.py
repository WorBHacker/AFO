"""The goal's guard (stage 11.4, D44; the user's plan 6 and its idea 3): a failure's fingerprint without its noise,
the three signals of going round in circles, and the constraints the hub can check itself at a tool's call."""

from hub import guard

TAIL = ("FAILED tests/test_x.py::test_y - AssertionError: 2 != 3\n1 failed in 0.53s\n12:01:33 "
        "C:\\Users\\U\\AppData\\Local\\Temp\\pytest-17\\x at 0x7ffde01a2b30")  # fmt: skip


def failed(tail, n=1):
    return [{"kind": "command", "n": n, "met": False, "tail": tail, "why": "ended with exit code 1 (expected 0)"}]


def test_a_failure_has_a_fingerprint_without_its_noise():
    again = TAIL.replace("0.53s", "1.20s").replace("12:01:33", "12:05:02").replace("pytest-17", "pytest-18")
    again = again.replace("0x7ffde01a2b30", "0x1234abcd")
    first = guard.fingerprint(failed(TAIL), [])
    assert first == guard.fingerprint(failed(again), []) and len(first) == 16  # the same failure, another moment
    assert first != guard.fingerprint(failed(TAIL.replace("test_y", "test_z")), [])  # another test
    assert first != guard.fingerprint(failed(TAIL.replace("2 != 3", "2 != 4")), [])  # another value
    assert guard.fingerprint([{"kind": "command", "n": 1, "met": True}], []) is None  # nothing failed
    broken = guard.fingerprint([], ["proj/a.py, line 3: does not parse (invalid syntax)"])
    assert broken and broken != first  # a file that does not parse is a failure too
    assert guard.failing(failed(TAIL) + failed("x", 2), ["proj/a.py, line 3: does not parse"]) == 3


def check(fp, route="continue", failing=1, at=""):
    return {"route": route, "fingerprint": fp, "failing": failing, "created_at": at}


def test_the_three_signals():
    assert guard.signal([], "a", 1, 0) is None
    assert guard.signal([check("a")], "a", 1, 0)[0] == "investigate"  # twice in a row: look for the cause first
    assert guard.signal([check("a"), check("b")], "a", 1, 0) is None
    seen = [check("a"), check("a", "investigate"), check(None, "investigated")]
    action, why = guard.signal(seen, "a", 1, 0)
    assert action == "blocked" and "3" in why  # the third time, after a look for the cause: the human
    assert guard.signal(seen, "b", 1, 0) is None  # another failure: it moved
    assert guard.signal([check("a"), check("b"), check("a")], "b", 1, 0)[0] == "investigate"  # a↔b
    swinging = [check("a"), check("b"), check("a", "investigate"), check(None, "investigated"), check("b"),
                check("a")]  # fmt: skip
    assert guard.signal(swinging, "b", 1, 0)[0] == "blocked"  # a↔b again after the look
    assert guard.signal([check("a")], "b", 1, 5)[0] == "investigate"  # one file edited 5 times, nothing better
    assert guard.signal([check("a")], "b", 0, 5) is None  # nothing fails: no thrashing
    assert guard.signal([check(None, "investigated")], "b", 1, 5)[0] == "blocked"  # and again after the look
    assert guard.signal([check(None)], None, 0, 9) is None


def test_where_progress_was_last_made():
    history = [check("a", failing=3, at="t1"), check("b", failing=2, at="t2"), check("c", failing=2, at="t3")]
    assert guard.no_progress_since(history, 2) == "t2"  # 3 → 2 at t2, then no better
    assert guard.no_progress_since(history, 1) is None  # better now: nothing to count
    assert guard.no_progress_since([check("a", failing=2, at="t1")], 2) == ""  # never better: since the goal began


def test_the_constraints_the_hub_can_check():
    said = ["Не трогать: migrations/, *.lock", "только: src/, tests/", "не запускать: git push, npm publish",
            "Пиши понятно", "Don't touch: `build/`", "never run: rm"]  # fmt: skip
    rules = guard.rules(said)
    assert [(r.kind, r.text) for r in rules] == [
        ("paths_no", said[0]), ("paths_only", said[1]), ("commands_no", said[2]), ("paths_no", said[4]),
        ("commands_no", said[5])]  # fmt: skip
    assert [guard.checkable(s) for s in said] == [True, True, True, False, True, True]
    assert "«Не трогать: migrations/, *.lock»" in guard.refusal(rules, "write", "migrations/0001_init.py")
    assert guard.refusal(rules, "write", "src/poetry.lock") is not None  # a pattern matches the name anywhere
    assert guard.refusal(rules, "write", "src/app.py") is None and guard.refusal(rules, "write", "tests/a.py") is None
    assert "«только: src/, tests/»" in guard.refusal(rules, "write", "README.md")
    assert guard.refusal(rules, "write", "src/build/x.py") is None
    assert guard.refusal(rules, "write", "build/x") is not None
    assert guard.refusal(rules, "command", "git status") is None
    assert "«не запускать: git push, npm publish»" in guard.refusal(rules, "command", "cd sub && git push origin main")
    assert guard.refusal(rules, "command", "rm -r out") is not None
    assert guard.refusal(rules, "command", "rmdir x") is None
    assert guard.refusal(guard.rules(["Пиши понятно"]), "write", "anything.py") is None


def test_claude_codes_calls_as_the_guard_sees_them(tmp_path):
    """11.7: Claude Code asks for its edits and commands through the hub's permission tool - a goal's guard reads
    them there: a write with the path inside the project, a command with its text; the rest is not its business."""
    root = tmp_path / "proj"
    assert guard.claude_call("Edit", {"file_path": str(root / "src" / "a.py")}, root) == ("write", "src/a.py")
    assert guard.claude_call("Write", {"file_path": "notes.txt"}, root) == ("write", "notes.txt")
    assert guard.claude_call("NotebookEdit", {"notebook_path": str(root / "n.ipynb")}, root) == ("write", "n.ipynb")
    outside = guard.claude_call("Edit", {"file_path": str(tmp_path / "other.txt")}, root)
    assert outside[0] == "write" and outside[1].endswith("other.txt")  # «только: …» refuses it
    assert guard.claude_call("Bash", {"command": "git push"}, root) == ("command", "git push")
    assert guard.claude_call("PowerShell", {"command": "rm x"}, None) == ("command", "rm x")
    assert guard.claude_call("Read", {"file_path": "a.py"}, root) is None
    assert guard.claude_call("WebFetch", {"url": "https://example.com"}, root) is None
