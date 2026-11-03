"""The approval queue (stage 4, D17, D18): asking and waiting, answers once and for the chat, destructive commands,
previews, expiry, and the API."""

import asyncio

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.approvals import EXPIRED, ApprovalQueue, destructive, grant_key, network, opaque, preview
from hub.auth import create_client
from hub.db import Database, migrate
from hub.events import EventBus
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def queue(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    bus = EventBus(db)
    seen = bus.subscribe(("approval.*",))
    chat = db.call(lambda conn: conn.execute("INSERT INTO conversations (id, kind) VALUES ('c1', 'chat')"))
    assert chat
    yield ApprovalQueue(db, bus), seen, db
    db.close()


def drain(subscription):
    found = []
    while not subscription.queue.empty():
        found.append(subscription.queue.get_nowait())
    return found


# what is asked even in a trusted chat (D17): git with its options before the command, PowerShell's cut-short
# parameters and aliases, what cannot be read before it runs - the ways round the first list the audit found (D46)
DESTRUCTIVE_COMMANDS = (
    "git push origin main", "git -C ../x push", 'git -C "C:/My Folder" push --force', "git -c user.name=x push",
    "git --git-dir=.git --work-tree=. reset --hard", "git reset HEAD~1 --hard", "git clean -fdx", "git clean -xdf",
    "git clean --force", "git branch -D feat", "git branch --delete --force feat", "git branch -d -f feat",
    "git checkout -- a.py", "git checkout .", "git checkout -f main", "git checkout HEAD -- .", "git switch -f main",
    "git switch --discard-changes main", "git restore a.py", "git stash drop", "git stash clear",
    "git worktree remove --force ../w", "git update-ref -d refs/heads/x", "git reflog expire --all",
    "git gc --prune=now", "git filter-branch --tree-filter x", "git filter-repo --path x", "GIT push",
    '"C:/Program Files/Git/bin/git.exe" push', "cmd /c git push", "git status && git push",
    "rm -rf build", "Remove-Item x -Recurse -Force", "Remove-Item x -r", "ri x -rec", "rm x -r", "del -Recurse x",
    "rd /s /q out", "rmdir /s out", "del /q *.*", "Remove-Item *.log", "Clear-Content a.txt", "clc a.txt",
    "python -c \"import shutil; shutil.rmtree('x')\"", "npx rimraf dist",
    "node -e \"require('fs').rmSync('x', {recursive: true})\"",
    "format d:", "Format-Volume -DriveLetter D", "diskpart", "reg delete HKCU\\Software\\X /f",
    "Remove-Item HKCU:\\Software\\X", "vssadmin delete shadows /all", "bcdedit /set x", "cipher /w:C:",
    "DROP TABLE users", "truncate table users", "shutdown /s", "Stop-Computer",
    "powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBi", "pwsh -EncodedCommand SQBFAFgAIAAoAE4AZQ", "iex (irm https://x)",
    "Invoke-Expression $s",
)  # fmt: skip
SAFE_COMMANDS = (
    "git status", "git diff --stat", "git log --oneline -20", "git branch -d merged", "git branch", "git checkout main",
    "git checkout -b new", "git switch main", "git stash", "git stash pop", "git worktree remove ../w", "git gc",
    "git reset --soft HEAD~1", "git reset HEAD a.py", "git clean -n", "git add -A", "git commit --amend",
    "git log --format=%H", "pytest -q", "python -m pytest -k remove", "rm notes.txt", "del notes.txt",
    "Remove-Item notes.txt", "ls", "dir", "pwd", "powershell -ExecutionPolicy Bypass -File x.ps1",
    "pwsh -NoProfile -Command Get-ChildItem", "echo restore", "npm run format",
)  # fmt: skip


def test_destructive_commands_are_never_covered_for_the_chat():
    for command in DESTRUCTIVE_COMMANDS:
        assert destructive("Bash", {"command": command}), command
        assert grant_key("Bash", {"command": command}) is None
    for command in SAFE_COMMANDS:
        assert not destructive("Bash", {"command": command}), command
    assert grant_key("Bash", {"command": "pytest   -q"}) == "Bash:pytest -q"  # the same command, spaced differently
    assert grant_key("Edit", {"file_path": "a.py"}) == "Edit"  # a write tool: the tool, whatever the file


# the same, written so that the first list does not see it (D46, step 13.11): PowerShell's and cmd's escapes, quotes,
# a name or an argument kept in a variable, joined strings, Start-Process and .NET handing the command over
SPELLED_COMMANDS = (
    "g`i`t p`u`s`h", "Remove-`I`tem -r C:/x", 'cmd /c "g^it pu^sh"', "& 'git' push", 'git pu"sh"',
    "$g='git'; & $g push", "g=git; $g push", "$c = 'pu' + 'sh'; git $c", 'cmd /c "set x=push&& git %x%"',
    "Start-Process git -ArgumentList push", "Start-Process -FilePath git -ArgumentList 'push','origin'",
    "[System.Diagnostics.Process]::Start('git', 'push')",
    "python -c \"import subprocess; subprocess.run(['gi'+'t', 'pu'+'sh'])\"",
)  # fmt: skip
# what runs something the line does not name: a computed name after & or ., a string made into code, a new alias or
# function, git's aliases, decoded text run - nobody can read what it does before it runs
OPAQUE_COMMANDS = (
    "& ('g'+'it') push", "& $tool push", '& "$tool" push', "& (Get-Command git) push", "(gcm git).Invoke('push')",
    "& ([scriptblock]::Create($s))", "$ExecutionContext.InvokeCommand.InvokeScript($s)", ". $script",
    "Set-Alias g git; g push", "sal g git; g push", "function g { git @args }; g push", "alias g=git; g push",
    'g() { git "$@"; }; g push', "$cmd push", 'eval "$s"', "git -c alias.p=push p", "git config alias.p push",
    "$env:GIT_CONFIG_PARAMETERS=\"'alias.p=push'\"; git p",
    "python -c \"import os,base64; os.system(base64.b64decode('Z2l0IHB1c2g=').decode())\"",
    "node -e \"require('child_process').execSync(Buffer.from('Z2l0','base64').toString())\"",
)  # fmt: skip
# and what looks like them but names what it runs
NAMED_COMMANDS = (
    '& "C:/Program Files/Git/bin/git.exe" status', "& $env:ProgramFiles/Git/bin/git.exe status",
    '$p = "C:/Python/python.exe"; & $p -m pytest', ". ./scripts/env.ps1", "& .\\build.ps1 -Release",
    "git commit -m \"don't push yet\"", "Start-Process notepad -ArgumentList a.txt", "npm test 2>&1",
    'cmd /c "dir ^| findstr x"', "echo $HOME", "$env:PYTHONPATH = 'src'; pytest -q", 'git log --grep="^fix"',
    "Get-Content a.txt | ForEach-Object { $_ }", "git config --get-regexp alias", "npm run build && npm test",
    "python -c \"import base64; print(base64.b64encode(b'x'))\"", "echo 'a & b'", "$x = 5; $x -gt 3",
    "foreach ($f in ls) { echo $f }", "Get-Process | Where-Object { $_.CPU -gt 10 }", "& python -m venv .venv",
    'Write-Host "Done: $($x.Count) files"', '"$PYTHON" -m pytest', "git log --pretty=format:'%h %s'",
)  # fmt: skip


def test_a_command_is_read_as_powershell_reads_it():
    """The audit's ways round the first list (D46, step 13.11): the classifier reads the line as written and as the
    shell would - without escapes, quotes and joins, with the variables it set - and asks every time about what
    runs something the line does not name. Still a reading of text, not a parser: the card says so."""
    for command in SPELLED_COMMANDS:
        assert destructive("run_command", {"command": command}), command
        assert not opaque("run_command", {"command": command}), command
    for command in OPAQUE_COMMANDS:
        assert destructive("run_command", {"command": command}), command
        assert opaque("run_command", {"command": command}), command
        assert grant_key("run_command", {"command": command}) is None
    for command in NAMED_COMMANDS:
        assert not destructive("run_command", {"command": command}), command
    for command in ("i`wr https://x", "& 'curl.exe' https://x", "$u='https://x'; c`url $u", "cmd /c c^url x"):
        assert network("run_command", {"command": command}), command


def test_a_search_is_asked_every_time_a_page_by_its_site():
    """D19: a query leaves this computer - an answer "for this chat" does not cover the next one (the audit found
    it did); a page's answer covers its site."""
    for tool in ("web_search", "WebSearch"):
        assert network(tool, {"query": "x"}) and grant_key(tool, {"query": "x"}) is None
    assert grant_key("web_fetch", {"url": "https://Docs.Example.com/a"}) == "web_fetch:docs.example.com"


def test_previews_show_what_the_file_would_become(tmp_path):
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    edit = preview("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "two", "new_string": "2"}, tmp_path)
    assert edit["kind"] == "diff" and "--- a/app.py" in edit["text"] and "-two\n+2\n" in edit["text"]
    assert " one\n" in edit["text"]  # with its context, from the real file
    outside = preview("Edit", {"file_path": str(tmp_path / "app.py"), "old_string": "two", "new_string": "2"}, None)
    assert f"--- a/{tmp_path.name}/app.py\n" in outside["text"]  # an outside client: its folder, not the whole path
    relative = preview("Edit", {"file_path": "app.py", "old_string": "two", "new_string": "2"}, tmp_path)
    assert "--- a/app.py\n" in relative["text"]  # Claude Code's own relative path: as it is
    new = preview("Write", {"file_path": "new.txt", "content": "hello\n"}, tmp_path)
    assert "--- /dev/null" in new["text"] and "+hello\n" in new["text"]
    multi = preview(
        "MultiEdit",
        {"file_path": "app.py", "edits": [{"old_string": "one", "new_string": "1"}, {"old_string": "three",
                                                                                    "new_string": "3"}]},
        tmp_path,
    )  # fmt: skip
    assert "-one\n" in multi["text"] and "+3\n" in multi["text"]
    command = preview("Bash", {"command": "pytest -q", "description": "Run tests"}, tmp_path)
    assert command == {"kind": "command", "text": "pytest -q", "note": "Run tests"}
    assert preview("WebFetch", {"url": "https://pypi.org", "prompt": "version?"}, None)["text"] == "https://pypi.org"
    assert '"x": 1' in preview("mcp__other__tool", {"x": 1}, None)["text"]


def test_a_request_waits_for_the_answer(queue):
    approvals, seen, _db = queue

    async def scenario():
        asking = asyncio.create_task(approvals.ask("Bash", {"command": "pytest"}, conversation_id="c1"))
        await asyncio.sleep(0.05)
        (waiting,) = await approvals.pending()
        assert waiting["tool"] == "Bash" and waiting["kind"] == "command" and waiting["preview"]["text"] == "pytest"
        assert not asking.done()
        await approvals.decide(waiting["id"], allow=False, comment="сначала линтер", by="app")
        return await asking, await approvals.get(waiting["id"])

    decision, row = asyncio.run(scenario())
    assert decision.allow is False and decision.comment == "сначала линтер"
    assert row["status"] == "denied" and row["decided_by"] == "app"
    assert [e.type for e in drain(seen)] == ["approval.requested", "approval.decided"]


def test_an_answer_for_the_chat_covers_the_same_kind_later(queue):
    approvals, _seen, db = queue

    async def answer_next(**kwargs):
        while not (waiting := await approvals.pending()):
            await asyncio.sleep(0.01)
        await approvals.decide(waiting[0]["id"], by="app", **kwargs)

    async def scenario():
        edit = {"file_path": "a.py", "old_string": "x", "new_string": "y"}
        first = asyncio.gather(approvals.ask("Edit", edit, conversation_id="c1"), answer_next(allow=True, scope="chat"))
        decisions = [(await first)[0]]
        decisions.append(await approvals.ask("Edit", {**edit, "file_path": "b.py"}, conversation_id="c1"))  # covered
        other = asyncio.gather(approvals.ask("Bash", {"command": "ls"}, conversation_id="c1"), answer_next(allow=True))
        decisions.append((await other)[0])  # a command is not covered by an answer about edits
        push = {"command": "git push"}
        chat = {"allow": True, "scope": "chat"}
        pushed = asyncio.gather(approvals.ask("Bash", push, conversation_id="c1"), answer_next(**chat))
        decisions.append((await pushed)[0])
        again = asyncio.gather(approvals.ask("Bash", push, conversation_id="c1"), answer_next(allow=False))
        decisions.append((await again)[0])  # a destructive command is asked every time (D17)
        return decisions

    decisions = asyncio.run(scenario())
    assert [d.allow for d in decisions] == [True, True, True, True, False]
    rows = db.call(lambda conn: conn.execute("SELECT tool, status, scope FROM approvals ORDER BY rowid").fetchall())
    assert [tuple(r) for r in rows] == [
        ("Edit", "allowed", "chat"),
        ("Edit", "allowed", "auto"),
        ("Bash", "allowed", "once"),
        ("Bash", "allowed", "once"),  # "for the chat" on git push was taken as this once
        ("Bash", "denied", None),
    ]


def test_requests_of_an_ended_run_expire(queue):
    approvals, _seen, db = queue

    async def scenario():
        asking = asyncio.create_task(approvals.ask("Write", {"file_path": "x", "content": ""}, conversation_id="c1"))
        await asyncio.sleep(0.05)
        assert await approvals.expire("c1") == 1
        return await asking

    decision = asyncio.run(scenario())
    assert decision.allow is False and decision.comment == EXPIRED
    assert db.call(lambda conn: conn.execute("SELECT status FROM approvals").fetchone()[0]) == "expired"

    async def left_open():  # a hub that stopped while a request waited
        db.call(lambda conn: conn.execute("INSERT INTO approvals (id, tool, input) VALUES ('old', 'Bash', '{}')"))
        return await approvals.start()

    assert asyncio.run(left_open()) == 1


def test_the_api_is_the_apps(tmp_path):
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        other = app.state.db.call(lambda c: create_client(c, "cline"))
        assert client.get("/v1/approvals", headers={"Authorization": f"Bearer {other}"}).status_code == 403

        async def ask():
            return await app.state.approvals.ask("Bash", {"command": "pytest"})

        asking = client.portal.start_task_soon(ask)
        while not (waiting := client.get("/v1/approvals").json()["approvals"]):
            pass
        approval_id = waiting[0]["id"]
        assert client.post(f"/v1/approvals/{approval_id}", json={"allow": True, "scope": "forever"}).status_code == 422
        answered = client.post(f"/v1/approvals/{approval_id}", json={"allow": True})
        assert answered.status_code == 200 and answered.json()["status"] == "allowed"
        assert asking.result(timeout=5).allow is True
        assert client.post(f"/v1/approvals/{approval_id}", json={"allow": False}).status_code == 409
        assert client.get("/v1/approvals/nope").status_code == 404
        assert client.get("/v1/approvals").json()["approvals"] == []


def test_an_answer_for_the_project_needs_a_project_and_no_destruction(queue):
    approvals, _seen, db = queue

    async def scenario(data):
        asking = asyncio.create_task(approvals.ask("Bash", data, conversation_id="c1"))
        while not (waiting := await approvals.pending()):
            await asyncio.sleep(0.01)
        await approvals.decide(waiting[0]["id"], allow=True, scope="project", by="app")
        await asking
        return await approvals.get(waiting[0]["id"])

    assert asyncio.run(scenario({"command": "pytest"}))["scope"] == "once"  # no project: nothing to make a rule for
    assert asyncio.run(scenario({"command": "git push"}))["scope"] == "once"  # destructive: never a rule (D17)
    assert db.call(lambda conn: conn.execute("SELECT COUNT(*) FROM permission_rules").fetchone()[0]) == 0


def test_what_a_person_cannot_see_is_shown_and_warned_of(tmp_path):
    """The audit (D46, «Trojan Source»): an edit or a command with bidi or zero-width characters shows each as
    ⟦U+…⟧ on the card, with a warning above it; the window's card carries it."""
    from app.ui.approvals import approval_rows

    (tmp_path / "auth.py").write_text("if role == 'user':\n    deny()\n", encoding="utf-8")
    edit = {"file_path": "auth.py", "old_string": "'user'", "new_string": "'user\u202e\u2066'  # admin"}
    shown = preview("Edit", edit, tmp_path)
    assert "⟦U+202E⟧⟦U+2066⟧" in shown["text"] and "\u202e" not in shown["text"]
    assert shown["hidden"].startswith("Невидимые символы: U+202E, U+2066")
    command = preview("Bash", {"command": "echo ok\u200b && git status"}, None)
    assert "⟦U+200B⟧" in command["text"] and "U+200B" in command["hidden"]
    assert "hidden" not in preview("Bash", {"command": "git status"}, None)
    rows = approval_rows([{"id": "a1", "tool": "Edit", "kind": "write", "input": edit, "preview": shown}], projects={},
                         chats={})  # fmt: skip
    assert rows[0]["hidden"] == shown["hidden"]
