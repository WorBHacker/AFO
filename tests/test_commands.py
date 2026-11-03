"""Commands the hub's models run (stage 5.2, D17, D19): asked, whitelisted per project, network and destructive
ones every time, run in a real hub terminal and waited for."""

import os
import time

from hub.approvals import grant_key, network
from hub.commands import asks, tree_work, working
from tests.test_changes import finished, new_chat, result_of, say, setup_chat, wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def run(command, **extra):
    return {"id": "r1", "name": "run_command", "input": {"project": "proj", "command": command, **extra}}


def answer_next(hub, **body):  # noqa: F811 - the fixture's name
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True, **body})
    return asked


def test_network_commands_are_never_covered():
    for command in ("pip install requests", "npm ci", "git pull", "curl https://example.com", "Invoke-WebRequest x",
                    "winget install x", "git -C x pull", "git ls-remote origin", "npx create-vite", "uvx ruff",
                    "npm exec x", "(New-Object Net.WebClient).DownloadString('u')", "Start-BitsTransfer -Source u",
                    "bitsadmin /transfer j u f", "certutil -urlcache -f http://x a", "docker pull nginx"):  # fmt: skip
        assert network("run_command", {"command": command}) and grant_key("run_command", {"command": command}) is None
    assert grant_key("run_command", {"command": "pytest  -q"}) == "run_command:pytest -q"
    for command in ("git status", "npm run build", "uv run pytest", "python x.py"):
        assert not network("run_command", {"command": command}), command


def test_a_command_is_asked_run_in_a_terminal_and_whitelisted(hub, repo):  # noqa: F811
    command = "Write-Output 'привет из модели'; exit 3"
    pid, conv, provider = setup_chat(hub, repo, [[run(command)]])
    say(hub, conv)
    asked = answer_next(hub, scope="project")
    assert asked["tool"] == "run_command" and asked["kind"] == "command" and asked["preview"]["text"] == command
    result = result_of(finished(hub, conv))
    assert result["error"] is False
    assert result["output"].startswith(f"$ {command}\nexit code 3;") and "привет из модели" in result["output"]
    (terminal,) = [t for t in hub.get("/v1/terminals", params={"project_id": pid}).json()["terminals"]]
    assert terminal["title"].startswith("Модель: Write-Output") and terminal["created_by"] == "model"

    provider.steps.append([run(command)])  # the same command in another chat: the project's whitelist answers
    other = new_chat(hub, pid)
    say(hub, other)
    assert result_of(finished(hub, other))["error"] is False
    assert hub.get(f"/v1/projects/{pid}/rules").json()["rules"][0]["grant_key"] == f"run_command:{command}"


def test_network_and_destructive_commands_are_asked_every_time(hub, repo):  # noqa: F811
    command = "pip install --dry-run nothing-at-all"
    pid, conv, provider = setup_chat(hub, repo, [[run(command)]])
    say(hub, conv)
    answer_next(hub, scope="project")  # taken as this once: no rule for the network
    finished(hub, conv)
    assert hub.get(f"/v1/projects/{pid}/rules").json()["rules"] == []
    provider.steps.append([run("git push origin main")])
    other = new_chat(hub, pid)
    say(hub, other)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["destructive"] is True
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "без push"})
    assert result_of(finished(hub, other))["output"] == "not done: the user said no - без push"


def test_a_trusted_chat_runs_commands_without_asking_but_destructive_ones(hub, repo):  # noqa: F811
    """2026-10-28, the user's word: «Доверенный» runs commands - the network ones too - without a card; a destructive
    one is still asked, every time."""
    _pid, conv, provider = setup_chat(hub, repo, [[run("Write-Output trusted")]], mode="trusted")
    say(hub, conv)
    result = result_of(finished(hub, conv))
    assert result["error"] is False and "trusted" in result["output"]
    provider.steps.append([run("pip install --dry-run nothing-at-all")])
    say(hub, conv)
    finished(hub, conv)
    assert hub.get("/v1/approvals").json()["approvals"] == []
    cards = hub.app.state.db.call(lambda c: c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0])
    assert cards == 0  # neither was asked
    provider.steps.append([run("git push origin main")])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["destructive"] is True and asked["conversation_id"] == conv
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": False})
    finished(hub, conv)


def test_a_question_on_the_last_line():
    """What run_command takes for a command waiting for an answer (D46, step 13.8) - and what it does not."""
    for line in ("Do you want to continue? [Y/n] ", "Proceed (y/n)? ", "Удалить? [y/N]: ", "Продолжить? [Д/н]",
                 "Overwrite a.txt? (Yes/No/All):", "Need to install the following packages: x Ok to proceed? (y) ",
                 "Password: ",
                 "Password for 'https://me@github.com': ", "Enter passphrase for key 'C:/Users/me/.ssh/id_ed25519': ",
                 "Username for 'https://github.com': ", "Press any key to continue . . . ", "Are you sure?",
                 '[Y] Yes  [A] Yes to All  [N] No  [L] No to All  [S] Suspend  [?] Help (default is "Y"):',
                 "Continue? ", "Пароль:", "(END)", "-- More  --", ":",
                 # what the list did not know (D46, step 13.13)
                 "Введите имя пользователя:", "Введите логин:", "Нажмите любую клавишу для продолжения . . .",
                 "Перезаписать файл a.txt?", "Вы уверены?", "Password (again):", "[sudo] password for alice:",
                 "Enter PIN for token:", "New password:", "Enter a value:",
                 "Пароль для ключа 'id_ed25519':"):  # fmt: skip
        assert asks(line), line
    for line in ("", "Building...", "passed: 12, failed: 0", "Password changed for user x.", "y/n", "What is 2+2?",
                 "error: password: too short (see docs)", "result = f(y)", "Replace 3 occurrences", "test_password_ok",
                 "Collecting requests",
                 # log lines that only mention the word (D46, step 13.13)
                 "Running password reset tests:", "Checking username validation:", "Fetching PIN codes:",
                 "Login succeeded:", "Entering directory 'src':", "Сборка пароля завершена:"):  # fmt: skip
        assert not asks(line), line


def test_a_command_waiting_for_an_answer_is_stopped_early(hub, repo, monkeypatch):  # noqa: F811
    """A command that asks (`[y/N]`, a password) got no answer - nobody types into a model's terminal - and hung until
    its timeout, 120 s by default (D46, step 13.8). A few seconds of silence with a question on the last line stop
    it; the model reads why and what to do instead."""
    from hub import commands

    monkeypatch.setattr(commands, "QUIET_S", 1.0)
    question = "Read-Host 'Удалить всё? [y/N]'"
    pid, conv, provider = setup_chat(hub, repo, [[run(question, timeout_s=60)]], mode="trusted")
    started = time.monotonic()
    say(hub, conv)
    result = result_of(finished(hub, conv))
    assert time.monotonic() - started < 20
    assert "waited for an answer" in result["output"] and "Удалить всё? [y/N]" in result["output"]
    assert "--yes" in result["output"]
    stopped = wait(lambda: [t for t in hub.get("/v1/terminals").json()["terminals"] if t["status"] != "running"])
    assert stopped

    # what a command sees: git and pip never ask, git shows no pager and opens no editor
    names = ("GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GIT_PAGER", "GIT_EDITOR", "PIP_NO_INPUT")
    shown = 'Write-Output "' + "|".join(f"$env:{name}" for name in names) + '"'
    provider.steps.append([run(shown)])
    env = new_chat(hub, pid, "trusted")
    say(hub, env)
    assert "0|never|cat|true|1" in result_of(finished(hub, env))["output"]

    provider.steps.append([run("Write-Output 'собираю'; Start-Sleep 3; Write-Output 'готово'")])  # quiet, no question
    quiet = new_chat(hub, pid, "trusted")
    say(hub, quiet)
    output = result_of(finished(hub, quiet))["output"]
    assert "exit code 0" in output and "готово" in output


def test_work_is_told_from_waiting():
    """The second sign (D46, step 13.13): a command's processes that used a processor, or started or ended one, through
    the quiet were working; an unknown look leaves the text alone to decide."""
    me = tree_work(os.getpid())
    assert me is not None and os.getpid() in me[1] and me[0] > 0
    assert tree_work(None) is None and tree_work(2**30) is None
    shell = (1.0, frozenset({10}))
    assert not working(shell, (1.05, frozenset({10})), 5.0)  # 1% of a processor: waiting
    assert working(shell, (1.5, frozenset({10})), 5.0)
    assert working(shell, (1.0, frozenset({10, 11})), 5.0) and working(shell, (0.2, frozenset()), 5.0)
    assert not working(None, shell, 5.0) and not working(shell, None, 5.0)


def test_a_command_at_work_is_left_alone_and_an_unknown_question_heard_early(hub, repo, monkeypatch):  # noqa: F811
    """The text alone misread both ways (D46, step 13.13): a build that printed "password for tests:" and then worked a
    few seconds without a word was stopped; a question the list did not know held the model the whole timeout. Now a
    command that works through its quiet is left alone, and one idle on a line that may be a question goes on while
    the model hears of it."""
    from hub import commands

    monkeypatch.setattr(commands, "QUIET_S", 1.0)
    monkeypatch.setattr(commands, "SILENT_S", 3.0)
    busy = (
        "Write-Output 'password for tests:'; $end = (Get-Date).AddSeconds(4); while ((Get-Date) -lt $end) {}; "
        "Write-Output 'готово'"
    )
    pid, conv, provider = setup_chat(hub, repo, [[run(busy, timeout_s=60)]], mode="trusted")
    say(hub, conv)
    output = result_of(finished(hub, conv))["output"]
    assert "exit code 0" in output and "готово" in output

    provider.steps.append([run("Read-Host 'Номер заказа'", timeout_s=60)])
    unknown = new_chat(hub, pid, "trusted")
    started = time.monotonic()
    say(hub, unknown)
    output = result_of(finished(hub, unknown))["output"]
    assert time.monotonic() - started < 30
    assert "silent and idle" in output and "Номер заказа:" in output and "terminal_output" in output
    assert [t for t in hub.get("/v1/terminals").json()["terminals"] if t["status"] == "running"]  # it goes on


def test_what_is_refused_and_a_command_that_goes_on(hub, repo):  # noqa: F811
    pid, conv, provider = setup_chat(hub, repo, [[run("Write-Output x")]], mode="read_only")
    say(hub, conv)
    assert "read-only" in result_of(finished(hub, conv))["output"]

    provider.steps.append([run("Write-Output a\nWrite-Output b")])
    multi = new_chat(hub, pid)
    say(hub, multi)
    assert result_of(finished(hub, multi))["output"].startswith("one command line at a time")

    provider.steps.append([run("Write-Output started; Start-Sleep 30", timeout_s=2)])
    server = new_chat(hub, pid)
    say(hub, server)
    answer_next(hub)
    result = result_of(finished(hub, server))
    assert "still running after 2 s" in result["output"] and "started" in result["output"]
    running = [t for t in hub.get("/v1/terminals", params={"project_id": pid}).json()["terminals"]
               if t["status"] == "running"]  # fmt: skip
    assert len(running) == 1
    assert hub.post(f"/v1/terminals/{running[0]['id']}/stop").status_code == 204
