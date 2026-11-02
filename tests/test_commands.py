"""Commands the hub's models run (stage 5.2, D17, D19): asked, whitelisted per project, network and destructive
ones every time, run in a real hub terminal and waited for."""

from hub.approvals import grant_key, network
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
