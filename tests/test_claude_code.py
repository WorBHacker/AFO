"""Claude Code under the hub (stage 4.3, D8, D18): finding the CLI, reading its stream, and whole turns against a real
hub over HTTP - with a stand-in CLI (tests/fake_claude.py), never the user's Claude Code."""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from common.paths import data_paths
from hub import guard, store
from hub.chat import _to_prompt
from hub.claude_code import (
    CONVERSATION_HEADER,
    PERMISSION_TOOL,
    ClaudeCodeProvider,
    Translator,
    child_env,
    earlier_text,
    effort_for,
    error_text,
    find_cli,
    usage_of,
    user_line,
)
from hub.server import create_app
from hub.settings import HubSettings

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


# --- finding the CLI ---


def test_the_cli_is_found_where_its_installers_put_it(tmp_path):
    empty = {"USERPROFILE": str(tmp_path / "home"), "APPDATA": str(tmp_path / "roaming"), "PATH": ""}
    assert find_cli({**empty, "LOCALCHAT_CLAUDE": str(tmp_path / "missing.exe")}) is None
    bundled = tmp_path / "roaming" / "Claude" / "claude-code"
    for version in ("2.1.99", "2.1.308", "2.1.304"):
        (bundled / version / "abc").mkdir(parents=True)
        (bundled / version / "abc" / "claude.exe").write_bytes(b"")
    assert find_cli(empty) == [str(bundled / "2.1.308" / "abc" / "claude.exe")]  # the desktop app's newest copy
    native = tmp_path / "home" / ".local" / "bin"
    native.mkdir(parents=True)
    (native / "claude.exe").write_bytes(b"")
    assert find_cli(empty) == [str(native / "claude.exe")]  # the native installer's comes first
    chosen = tmp_path / "my-claude.exe"
    chosen.write_bytes(b"")
    assert find_cli({**empty, "LOCALCHAT_CLAUDE": str(chosen)}) == [str(chosen)]


def test_without_the_cli_there_are_no_claude_code_models():
    import asyncio

    assert asyncio.run(ClaudeCodeProvider(lambda: None).list_models()) == []
    models = asyncio.run(ClaudeCodeProvider(lambda: FAKE).list_models())
    assert [m.id for m in models] == ["claude-code/default", "claude-code/opus", "claude-code/sonnet",
                                      "claude-code/haiku"]  # fmt: skip
    assert all(m.locality == "cloud" and m.prices == {} for m in models)  # a cloud model, paid by the subscription


# --- its stream ---


def assistant(message_id, *blocks, parent=None):
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": message_id, "content": list(blocks)}}


def test_the_blocks_of_one_message_become_one_message():
    t = Translator()
    steps = [
        *t.feed({"type": "system", "subtype": "init", "session_id": "s1"}),
        *t.feed({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "Сейчас"}}}),
        *t.feed(assistant("m1", {"type": "thinking", "thinking": "думаю"})),
        *t.feed(assistant("m1", {"type": "text", "text": "Сейчас посмотрю."})),
        *t.feed(assistant("m1", {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}})),
        *t.feed(assistant("m1", {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}})),
        *t.feed(assistant("x", {"type": "text", "text": "subagent"}, parent="t9")),
        *t.feed({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "print(1)"}]},
            {"type": "tool_result", "tool_use_id": "t2", "content": "x" * 40_000, "is_error": True}]}}),
        *t.feed(assistant("m2", {"type": "text", "text": "Готово."})),
        *t.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}),
        *t.feed({"type": "result", "subtype": "success", "is_error": False}),
    ]  # fmt: skip
    kinds = [(s.kind, s.role) for s in steps]
    assert kinds == [("session", ""), ("delta", "content"), ("message", "assistant"), ("message", "assistant"),
                     ("sub", ""), ("message", "tool"), ("message", "assistant"), ("limit", ""),
                     ("result", "")]  # fmt: skip
    first, second, results, last = (s.data for s in steps if s.kind == "message")
    assert [b["type"] for b in first] == ["reasoning", "text", "tool_call"]  # gathered, up to the first call
    assert second == [{"type": "tool_call", "id": "t2", "name": "Bash", "input": {"command": "ls"}}]
    assert results[0] == {"type": "tool_result", "id": "t1", "name": "Read", "output": "print(1)", "error": False}
    assert results[1]["error"] is True and results[1]["name"] == "Bash" and "ещё 10000 символов" in results[1]["output"]
    assert last == [{"type": "text", "text": "Готово."}]
    messages = [s.data for s in steps if s.kind == "message"]
    assert "subagent" not in json.dumps(messages, ensure_ascii=False)  # a subagent's text is not the chat's own


def test_a_subagents_steps_go_with_the_call_that_started_it():
    """21.3: a subagent's texts, calls and results come as steps under the call that started it, and go with that
    call's result; a long text is cut, a call's input keeps its short values."""
    t = Translator()
    t.feed(assistant("m1", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"description": "Найти"}}))
    sub = [*t.feed(assistant("s1", {"type": "text", "text": "Ищу. " + "x" * 3000}, parent="a1")),
           *t.feed(assistant("s1", {"type": "tool_use", "id": "r1", "name": "Write",
                                    "input": {"file_path": "a.py", "content": "y" * 5000}}, parent="a1")),
           *t.feed({"type": "user", "parent_tool_use_id": "a1", "message": {"content": [
               {"type": "tool_result", "tool_use_id": "r1", "content": "denied", "is_error": True}]}})]  # fmt: skip
    assert [(s.kind, s.data["parent"], s.data["step"]["type"]) for s in sub] == [
        ("sub", "a1", "text"), ("sub", "a1", "call"), ("sub", "a1", "result")]  # fmt: skip
    text, call, result = (s.data["step"] for s in sub)
    assert len(text["text"]) == 2000 and text["text"].endswith("…")
    assert call["name"] == "Write" and call["input"]["file_path"] == "a.py" and len(call["input"]["content"]) == 200
    assert result == {"type": "result", "name": "Write", "error": True, "output": "denied"}
    (done,) = t.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a1",
                                                                "content": "Нашёл"}]}})  # fmt: skip
    assert done.data[0]["output"] == "Нашёл" and done.data[0]["sub"] == [text, call, result]


def test_usage_errors_input_and_environment():
    result = {"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20,
                        "output_tokens": 30}}  # fmt: skip
    assert usage_of(result) == {"input_tokens": 130, "cached_input_tokens": 100, "cache_write_tokens": 20,
                                "output_tokens": 30, "estimated": 1}  # fmt: skip
    assert error_text({"errors": ["limit", "again"]}) == "limit; again"
    assert error_text({"result": "boom", "errors": ["x"]}) == "boom"
    assert error_text({"subtype": "error_max_turns"}) == "error_max_turns"
    line = user_line("строка\nвторая")
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == {"type": "user", "message": {"role": "user", "content": "строка\nвторая"},
                                "parent_tool_use_id": None}  # fmt: skip
    billing = {"ANTHROPIC_API_KEY": "sk", "anthropic_auth_token": "t", "CLAUDE_CODE_USE_VERTEX": "1"}
    env = child_env({"PATH": "x", **billing})
    assert env["PATH"] == "x" and not set(billing) & set(env)
    assert int(env["MCP_TOOL_TIMEOUT"]) >= 3600_000 and env["CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"] == "0"


def test_the_chats_depth_and_the_models_that_have_none():
    """21.2: the chat's depth goes as --effort - not to a session whose model has no effort levels (as the CLI
    named it); the user's CLAUDE_CODE_EFFORT_LEVEL would win over the flag, so it stays out when the hub gives one."""
    assert effort_for("high", None) == "high" and effort_for("xhigh", "claude-opus-4-6") == "xhigh"
    assert effort_for("max", "claude-opus-5-5") == "max" and effort_for("low", "claude-haiku-5-5") == "low"
    for old in ("claude-3-5-sonnet-20241022", "claude-opus-4-5-20251101", "claude-sonnet-4-20250514",
                "claude-haiku-4-5-20251001", "claude-opus-4-1-20250805"):  # fmt: skip
        assert effort_for("high", old) is None, old
    assert effort_for("high", "claude-opus-4-8[1m]") == "high" and effort_for("", "claude-opus-5-5") is None
    env = {"PATH": "x", "CLAUDE_CODE_EFFORT_LEVEL": "low"}
    assert child_env(env)["CLAUDE_CODE_EFFORT_LEVEL"] == "low"  # no depth from the hub: the user's own stands
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in child_env(env, effort=True)


def message(seq, role, content, model_id=None):
    return store.MessageRow(seq, seq, role, model_id, content, "")


def test_other_models_get_claude_codes_turns_as_text_only():
    call = {"type": "tool_call", "id": "t1", "name": "Edit", "input": {}}
    history = [
        message(1, "user", [{"type": "text", "text": "поправь"}]),
        message(2, "assistant", [{"type": "text", "text": "Правлю."}, call], "claude-code/default"),
        message(3, "tool", [{"type": "tool_result", "id": "t1", "name": "Edit", "output": "ok", "error": False}]),
        message(4, "assistant", [{"type": "text", "text": "Готово."}], "claude-code/default"),
        message(5, "user", [{"type": "text", "text": "а теперь ты"}]),
    ]  # fmt: skip
    prompt = _to_prompt(history)
    expected = [("user", "поправь"), ("assistant", "Правлю."), ("assistant", "Готово."), ("user", "а теперь ты")]
    assert [(p["role"], p["content"]) for p in prompt] == expected
    assert not any("tool_calls" in p or "provider_raw" in p for p in prompt)
    assert earlier_text(history[:1] + history[4:]) == "User: поправь\n\nUser: а теперь ты"


# --- whole turns, through a real hub ---


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveHub:
    """The hub app served by uvicorn on a free port in a thread: the stand-in CLI reaches its /mcp over HTTP."""

    def __init__(self):
        port = free_port()
        self.app = create_app(HubSettings(paths=data_paths(), port=port, llama_swap_url="http://127.0.0.1:9",
                                          status_interval_s=0, process_interval_s=0))  # fmt: skip
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_config=None, lifespan="on",
                                ws="websockets-sansio")  # fmt: skip
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            assert time.monotonic() < deadline, "the hub did not start"
            time.sleep(0.05)
        # the catalog read at start: a test's own sync comes after it, or a late one disables the stand-in's models
        while not self.app.state.initial_sync.done():
            assert time.monotonic() < deadline, "the hub's first catalog read did not end"
            time.sleep(0.05)
        token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"},
                                 timeout=30)  # fmt: skip

    def close(self):
        self.http.close()
        self.server.should_exit = True
        self.thread.join(30)

    def wait_idle(self, cid, timeout=60):
        deadline = time.monotonic() + timeout
        while self.http.get(f"/v1/conversations/{cid}").json()["busy"]:
            assert time.monotonic() < deadline, "the turn did not end"
            time.sleep(0.05)
        return self.http.get(f"/v1/conversations/{cid}").json()

    def wait_approval(self, timeout=30):
        deadline = time.monotonic() + timeout
        while not (pending := self.http.get("/v1/approvals").json()["approvals"]):
            assert time.monotonic() < deadline, "no approval request came"
            time.sleep(0.05)
        return pending[0]

    def feed(self, types):
        events = self.http.get("/v1/feed", params={"limit": 200, "types": types}).json()["events"]
        return [e["type"] for e in events]


@pytest.fixture
def hub(repo, tmp_path, monkeypatch):
    log = tmp_path / "fake-claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-not-for-claude-code")  # must not reach the CLI (D8)
    live = LiveHub()
    try:
        live.app.state.claude_code._find = lambda: FAKE
        live.app.state.gateway.register(ClaudeCodeProvider(lambda: FAKE))
        assert live.http.post("/v1/models/sync").status_code == 200
        live.repo, live.log = repo, log
        pid = live.http.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        live.http.patch(f"/v1/projects/{pid}", json={"cloud_ok": True})
        live.pid = pid
        yield live
    finally:
        live.close()


def runs(hub):
    return [json.loads(line) for line in hub.log.read_text(encoding="utf-8").splitlines()]


def chat_with_project(hub):
    cid = hub.http.post("/v1/conversations", json={}).json()["id"]
    hub.http.put(f"/v1/conversations/{cid}/projects", json={"project_ids": [hub.pid]})
    return cid


def send(hub, cid, text, model="claude-code/default"):
    response = hub.http.post(f"/v1/conversations/{cid}/messages", json={"content": text, "model_id": model})
    assert response.status_code == 202, response.text


def test_a_turn_asks_the_user_and_edits_when_allowed(hub):
    cid = chat_with_project(hub)
    send(hub, cid, "edit notes.txt please")
    asked = hub.wait_approval()
    assert asked["tool"] == "Edit" and asked["conversation_id"] == cid and asked["project_id"] == hub.pid
    assert "-свежий файл" in asked["preview"]["text"] and "+правленый файл" in asked["preview"]["text"]  # the real file
    assert hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": True}).status_code == 200
    chat = hub.wait_idle(cid)
    roles = [(m["role"], [b["type"] for b in m["content"]]) for m in chat["messages"]]
    assert roles == [("user", ["text"]), ("assistant", ["reasoning", "text", "tool_call"]), ("tool", ["tool_result"]),
                     ("assistant", ["text"])]  # fmt: skip
    assert chat["messages"][-1]["content"][0]["text"] == "Готово." and chat["messages"][-1]["model_id"] == (
        "claude-code/default"
    )
    assert (hub.repo / "notes.txt").read_text(encoding="utf-8") == "правленый файл\n"
    (first,) = runs(hub)
    assert first["cwd"] == str(hub.repo) and "--resume" not in first["argv"]
    assert first["argv"][first["argv"].index("--permission-prompt-tool") + 1] == PERMISSION_TOOL
    # its own file tools stay out of the hub's data folder (D46, step 13.12): the last flag, the rules after it
    data = "//" + data_paths().root.resolve().as_posix().replace(":", "", 1)
    data = data[:2] + data[2].lower() + data[3:]
    assert first["argv"][-3:] == ["--disallowedTools", f"Read({data}/**)", f"Edit({data}/**)"]
    assert first["env"]["ANTHROPIC_API_KEY"] is None  # the subscription, never an API key (D8)
    assert int(first["env"]["MCP_TOOL_TIMEOUT"]) > 60_000
    assert not list((data_paths().root / "claude-code").glob(f"{cid}*"))  # its MCP config (with a token) is gone
    usage = hub.http.get("/v1/usage").json()
    assert usage["today_cost_usd"] == 0  # the subscription pays: no money counted (D12)
    assert "approval.requested" in hub.feed("approval.*") and "approval.decided" in hub.feed("approval.*")

    send(hub, cid, "and now something else")
    chat = hub.wait_idle(cid)
    second = runs(hub)[1]
    assert second["argv"][second["argv"].index("--resume") + 1]  # the same Claude Code session goes on
    assert chat["messages"][-1]["content"][0]["text"].startswith("Ответ на:")


def test_a_subagents_steps_are_kept_under_its_call(hub, monkeypatch):
    """21.3: Claude Code's subagent reads a file; the chat keeps what it did with the call that started it - its text
    too, with a CLI that knows --forward-subagent-text (2.1.211+); an older one sends its calls and results only."""
    cid = chat_with_project(hub)
    send(hub, cid, "use an agent")
    chat = hub.wait_idle(cid)
    roles = [(m["role"], [b["type"] for b in m["content"]]) for m in chat["messages"]]
    assert roles == [("user", ["text"]), ("assistant", ["tool_call"]), ("tool", ["tool_result"]),
                     ("assistant", ["text"])]  # the subagent's steps are no messages of their own  # fmt: skip
    result = chat["messages"][2]["content"][0]
    assert result["output"] == "Конфиг — conf.py"
    assert result["sub"] == [{"type": "text", "text": "Ищу конфиг."},
                             {"type": "call", "name": "Read", "input": {"file_path": "conf.py"}},
                             {"type": "result", "name": "Read", "error": False, "output": "x = 1"}]  # fmt: skip
    assert "--forward-subagent-text" in runs(hub)[0]["argv"]

    hub.app.state.claude_code._versions.clear()
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.200")
    send(hub, cid, "use an agent again")
    chat = hub.wait_idle(cid)
    assert "--forward-subagent-text" not in runs(hub)[1]["argv"]
    assert [s["type"] for s in chat["messages"][-2]["content"][0]["sub"]] == ["call", "result"]


def test_the_chats_depth_reaches_claude_code(hub, monkeypatch):
    """21.2: the depth the chat sends goes as --effort, and the user's CLAUDE_CODE_EFFORT_LEVEL - which would win
    over it - does not reach the CLI then."""
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "low")
    cid = chat_with_project(hub)
    body = {"content": "hello", "model_id": "claude-code/default", "effort": "xhigh"}
    assert hub.http.post(f"/v1/conversations/{cid}/messages", json=body).status_code == 202
    hub.wait_idle(cid)
    (run,) = runs(hub)
    assert run["argv"][run["argv"].index("--effort") + 1] == "xhigh"
    assert run["env"]["CLAUDE_CODE_EFFORT_LEVEL"] is None
    assert run["argv"][-3] == "--disallowedTools"  # still the last flag: the rules follow it


def test_a_denial_with_a_comment_reaches_claude_code(hub):
    cid = chat_with_project(hub)
    send(hub, cid, "edit it")
    asked = hub.wait_approval()
    hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "не трогай заметки"})
    chat = hub.wait_idle(cid)
    result = chat["messages"][2]["content"][0]
    assert result["error"] is True and result["output"] == "не трогай заметки"
    assert chat["messages"][-1]["content"][0]["text"] == "Не стал править: не трогай заметки"
    assert (hub.repo / "notes.txt").read_text(encoding="utf-8") == "свежий файл\n"


def test_a_part_of_claude_codes_edit_is_written_by_the_hub(hub):
    """17.3 (D51): Claude Code's Edit of several hunks shows them on the card as the hub's own writes do; allowed in
    part, the hub writes that part itself (a change in «Изменения», undone the same) and answers Claude Code with a
    «no» that says what went, where the rest is and to read the file again - its own Edit would have put it all."""
    (hub.repo / "conf.py").write_text("".join("x = 1\n" if n in (1, 20, 40) else f"# {n}\n" for n in range(1, 41)),
                                      encoding="utf-8", newline="\n")  # fmt: skip
    cid = chat_with_project(hub)
    send(hub, cid, "edit every x")
    asked = hub.wait_approval()
    assert asked["tool"] == "Edit" and [h["index"] for h in asked["preview"]["hunks"]] == [0, 1, 2]
    decided = hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": True, "hunks": [0, 2],
                                                                  "comment": "20-ю не трогай"})  # fmt: skip
    assert decided.status_code == 200
    chat = hub.wait_idle(cid)
    said = chat["messages"][2]["content"][0]["output"]
    assert said.startswith("Not run as asked: the user allowed a part of it, and the hub wrote that part. conf.py: "
                           "2 of 3 hunks applied")  # fmt: skip
    assert "line 20" in said and "20-ю не трогай" in said and "read it again" in said
    lines = (hub.repo / "conf.py").read_text(encoding="utf-8").splitlines()
    assert (lines[0], lines[19], lines[39]) == ("x = 2", "x = 1", "x = 2")
    (change,) = hub.http.get(f"/v1/projects/{hub.pid}/changes").json()["changes"]
    assert (change["tool"], change["path"], change["approval_id"]) == ("Edit", "conf.py", asked["id"])


def test_earlier_turns_of_other_models_go_with_the_first_message(hub):
    cid = chat_with_project(hub)
    hub.app.state.db.call(lambda c: store.add_message(c, cid, "user", [{"type": "text", "text": "что в проекте?"}]))
    hub.app.state.db.call(lambda c: store.add_message(c, cid, "assistant", [{"type": "text", "text": "Файлы."}],
                                                      model_id="llama-swap/coder"))  # fmt: skip
    send(hub, cid, "hello")
    hub.wait_idle(cid)
    (run,) = runs(hub)
    assert run["text"].startswith("<earlier_in_this_chat>\nUser: что в проекте?\n\nAssistant: Файлы.")
    assert run["text"].endswith("\n\nhello")


def test_a_limit_and_an_error_end_the_turn(hub):
    cid = chat_with_project(hub)
    send(hub, cid, "limit")
    hub.wait_idle(cid)
    feed = hub.http.get("/v1/feed", params={"types": "chat.error,claude_code.limit"}).json()["events"]
    error = next(e for e in feed if e["type"] == "chat.error")
    assert error["payload"]["detail"] == "Claude Code: You've hit your session limit"
    limit = next(e for e in feed if e["type"] == "claude_code.limit")
    assert limit["payload"]["info"]["status"] == "rejected"


def test_a_turn_can_be_stopped(hub):
    cid = chat_with_project(hub)
    send(hub, cid, "hang")
    deadline = time.monotonic() + 30
    while not runs_started(hub):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert hub.http.post(f"/v1/conversations/{cid}/cancel").status_code in (200, 202)
    hub.wait_idle(cid, timeout=30)
    done = hub.http.get("/v1/feed", params={"types": "chat.done"}).json()["events"]
    assert done[0]["payload"]["cancelled"] is True


def runs_started(hub):
    return hub.log.exists() and hub.log.read_text(encoding="utf-8").strip()


def test_projects_only_for_local_models_stay_closed(hub, tmp_path):
    cid = hub.http.post("/v1/conversations", json={}).json()["id"]
    send(hub, cid, "hello")  # no project: it works in a folder of the chat's own (2026-10-28)
    hub.wait_idle(cid)
    assert len(runs(hub)) == 1
    hub.http.patch(f"/v1/projects/{hub.pid}", json={"cloud_ok": False})
    hub.http.put(f"/v1/conversations/{cid}/projects", json={"project_ids": [hub.pid]})
    send(hub, cid, "hello")
    hub.wait_idle(cid)
    errors = [e["payload"]["detail"] for e in
              hub.http.get("/v1/feed", params={"types": "chat.error"}).json()["events"]]  # fmt: skip
    assert any("only local models may see it (D10)" in e for e in errors)
    assert len(runs(hub)) == 1  # with the local-only project Claude Code was not started
    refused = hub.http.post(f"/v1/projects/{hub.pid}/claude-code/terminal", json={})
    assert refused.status_code == 409 and "local models only" in refused.json()["detail"]


def test_status_and_a_terminal(hub):
    status = hub.http.get("/v1/claude-code").json()
    assert status["installed"] is True and status["command"] == FAKE
    terminal = hub.http.post(f"/v1/projects/{hub.pid}/claude-code/terminal", json={"cols": 100, "rows": 30})
    assert terminal.status_code == 201, terminal.text
    info = terminal.json()
    assert info["title"] == "Claude Code" and "--mcp-config" in info["command"]
    deadline = time.monotonic() + 30
    while "interactive" not in hub.http.get(f"/v1/terminals/{info['id']}/output").json().get("data", ""):
        assert time.monotonic() < deadline, "the terminal printed nothing"
        time.sleep(0.1)
    config = json.loads((data_paths().root / "claude-code" / f"terminal-{hub.pid}.mcp.json").read_text("utf-8"))
    server = config["mcpServers"]["localchat"]
    assert server["type"] == "http" and server["url"].endswith("/mcp") and CONVERSATION_HEADER not in server["headers"]
    hub.app.state.claude_code._find = lambda: None
    assert hub.http.get("/v1/claude-code").json()["installed"] is False


def test_a_chat_with_no_project_its_rights_and_claude_codes_questions(hub):
    """2026-10-28 (the user's word): Claude Code in a chat with no project - an empty folder of the chat's own; its
    AskUserQuestion comes to the chat with the options to pick, the answer goes back as its input's `answers`; the
    chat's rights hold for it: «Только чтение» refuses its edit, «Доверенный» lets it through without a card."""
    from tests.test_changes import wait

    cid = hub.http.post("/v1/conversations", json={}).json()["id"]
    send(hub, cid, "hello")
    chat = hub.wait_idle(cid)
    assert chat["messages"][-1]["content"][0]["text"].startswith("Ответ на:")
    (first,) = runs(hub)
    folder = data_paths().root / "claude-code" / "chats" / cid
    assert first["cwd"] == str(folder) and folder.is_dir() and not any(folder.iterdir())
    assert "no project attached" in first["argv"][first["argv"].index("--append-system-prompt") + 1]
    send(hub, cid, "a question please")
    asked = wait(lambda: hub.http.get(f"/v1/conversations/{cid}/questions").json()["questions"])[0]
    assert asked["question"] == "Какой цвет?"
    assert asked["options"] == [{"label": "Синий", "description": "спокойный"},
                                {"label": "Красный", "description": "яркий"}]  # fmt: skip
    assert hub.http.post(f"/v1/runs/{cid}/questions/{asked['id']}", json={"answer": "Синий"}).status_code == 200
    chat = hub.wait_idle(cid)
    assert chat["messages"][-1]["content"][0]["text"] == "Выбрано: Синий"
    assert hub.http.get(f"/v1/conversations/{cid}/questions").json()["questions"] == []
    assert runs(hub)[1]["argv"][runs(hub)[1]["argv"].index("--resume") + 1]  # the same session in the same folder

    read_only = chat_with_project(hub)
    hub.http.put(f"/v1/conversations/{read_only}/context", json={"mode": "read_only"})
    send(hub, read_only, "edit notes.txt")
    chat = hub.wait_idle(read_only)
    assert chat["messages"][-1]["content"][0]["text"].startswith("Не стал править: This chat is read-only")
    assert (hub.repo / "notes.txt").read_text(encoding="utf-8") == "свежий файл\n"
    trusted = chat_with_project(hub)
    hub.http.put(f"/v1/conversations/{trusted}/context", json={"mode": "trusted"})
    send(hub, trusted, "edit notes.txt")
    chat = hub.wait_idle(trusted)
    assert chat["messages"][-1]["content"][0]["text"] == "Готово."
    assert (hub.repo / "notes.txt").read_text(encoding="utf-8") == "правленый файл\n"
    cards = hub.app.state.db.call(lambda c: c.execute("SELECT COUNT(*) FROM approvals WHERE conversation_id IN"
                                                        " (?, ?)", (read_only, trusted)).fetchone()[0])  # fmt: skip
    assert cards == 0  # no card for either: the chat's rights answered

    # a command (2026-10-28, the user's word): a trusted chat runs it without a card, a read-only one refuses it -
    # PowerShell too, Claude Code's shell on Windows; a destructive one is asked even in a trusted chat
    send(hub, trusted, "run it")
    assert hub.wait_idle(trusted)["messages"][-1]["content"][0]["text"] == "Выполнил: node make_docx.js"
    send(hub, read_only, "run it")
    assert hub.wait_idle(read_only)["messages"][-1]["content"][0]["text"].startswith("Не стал: This chat is read-only")
    send(hub, trusted, "run push")
    asked = wait(lambda: hub.http.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "PowerShell" and asked["destructive"] is True
    hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "не сейчас"})
    assert hub.wait_idle(trusted)["messages"][-1]["content"][0]["text"] == "Не стал: не сейчас"

    # D19 holds for Claude Code too (the audit, D46): a trusted chat lets it read a page without a card, but the edit
    # after it is asked - the page may have told it what to do
    later = chat_with_project(hub)
    hub.http.put(f"/v1/conversations/{later}/context", json={"mode": "trusted"})
    send(hub, later, "fetch the docs, then edit notes.txt")
    asked = wait(lambda: hub.http.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "Edit" and asked["conversation_id"] == later
    hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "сначала проверю"})
    assert hub.wait_idle(later)["messages"][-1]["content"][0]["text"] == "Не стал править: сначала проверю"

    # a file the window's editor holds with unsaved edits (D47, step 14.2c): Claude Code's edit of it - its path
    # counts from the project - is asked in a trusted chat too
    hub.http.put("/v1/editor/dirty", json={"files": [{"project_id": hub.pid, "path": "notes.txt"}]})
    send(hub, trusted, "edit notes.txt")
    asked = wait(lambda: hub.http.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "Edit" and asked["unsaved"] is True and asked["conversation_id"] == trusted
    hub.http.post(f"/v1/approvals/{asked['id']}", json={"allow": False, "comment": "там мои правки"})
    assert hub.wait_idle(trusted)["messages"][-1]["content"][0]["text"] == "Не стал править: там мои правки"


# --- a goal's worker (11.7, D44) ---

X_IS_2 = "if ((Get-Content conf.py -Raw) -match 'x = 2') { exit 0 } else { exit 1 }"


def goal_settled(hub, cid, *statuses, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        found = hub.http.get(f"/v1/conversations/{cid}/goal").json()
        busy = cid in hub.http.get("/v1/conversations").json()["busy"]
        if found["goal"] and found["goal"]["status"] in statuses and not found["checking"] and not busy:
            return found
        assert time.monotonic() < deadline, f"the goal did not settle: {found['goal'] and found['goal']['status']}"
        time.sleep(0.1)


def set_goal(hub, cid, **fields):
    body = {"objective": "edit every x in conf.py", "criteria": [{"command": X_IS_2}],
            "model_id": "claude-code/default", **fields}  # fmt: skip
    response = hub.http.put(f"/v1/conversations/{cid}/goal", json=body)
    assert response.status_code == 201, response.text
    return response.json()["goal"]


def test_claude_code_works_on_a_goal(hub):
    """11.7 (D44): Claude Code as a goal's worker - the hub starts its turn with the note on stdin, within
    --max-turns, and checks the goal when the turn ends, as any worker's; its tokens count, its money does not
    (the subscription pays, D12)."""
    (hub.repo / "conf.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    cid = chat_with_project(hub)
    assert hub.http.put(f"/v1/conversations/{cid}/context", json={"mode": "trusted"}).status_code == 200
    set_goal(hub, cid)
    done = goal_settled(hub, cid, "achieved")
    assert done["goal"]["turns_used"] == 1 and done["goal"]["tokens_used"] == 60  # 130 in - 100 from the cache + 30
    assert done["goal"]["cost_usd"] == 0 and [c["route"] for c in done["checks"]] == ["achieved"]
    (run,) = runs(hub)
    assert run["text"].startswith('[AFO hub] <goal_loop type="start">')
    assert "GOAL: edit every x in conf.py" in run["text"]
    assert run["argv"][run["argv"].index("--max-turns") + 1] == "30"
    assert (hub.repo / "conf.py").read_text(encoding="utf-8") == "x = 2\n"


def test_a_goals_constraints_and_its_investigation_hold_for_claude_code(hub):
    """11.7: Claude Code asks for its edits and commands through the hub's permission tool, and in a goal's turn the
    hub refuses there what a constraint forbids - in «Доверенном» too, before anything is asked; an investigation
    turn refuses every edit and command (D44: «the investigator is a denial of the permission request»)."""
    (hub.repo / "conf.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    cid = chat_with_project(hub)
    hub.http.put(f"/v1/conversations/{cid}/context", json={"mode": "trusted"})
    set_goal(hub, cid, constraints=["не трогать: conf.py"], limits={"max_turns": 1})
    parked = goal_settled(hub, cid, "budget")
    assert [c["route"] for c in parked["checks"]] == ["budget"]
    chat = hub.http.get(f"/v1/conversations/{cid}").json()
    refused = [b for m in chat["messages"] if m["role"] == "tool" for b in m["content"]]
    assert refused[0]["error"] is True and "«не трогать: conf.py» forbids writing conf.py" in refused[0]["output"]
    assert (hub.repo / "conf.py").read_text(encoding="utf-8") == "x = 1\n"
    assert hub.http.get("/v1/approvals").json()["approvals"] == []

    hub.app.state.chat.guards[cid] = guard.TurnGuard([], investigating=True)  # as a goal's investigation sets it
    send(hub, cid, "edit every x")
    chat = hub.wait_idle(cid)
    assert chat["messages"][-1]["content"][0]["text"].startswith("Не стал править: not done: this is an investigation")
    assert (hub.repo / "conf.py").read_text(encoding="utf-8") == "x = 1\n"
