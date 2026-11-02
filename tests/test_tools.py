"""Stage 3: the reading tools a chat's model gets, the tool loop through the hub, and tool turns on the wire."""

import asyncio
import json
import os

import httpx
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import changes, projects
from hub import chat as chat_module
from hub.chat import CHAT_TOOLS, NOT_RUN, _to_prompt
from hub.gateway.anthropic_provider import replay_blocks, to_wire
from hub.gateway.base import Model, StreamEvent
from hub.gateway.openai_compat import OpenAICompatProvider
from hub.gateway.openai_compat import to_wire as openai_wire
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings
from hub.store import MessageRow
from hub.tools import TOOLS, ToolContext, plain_terminal_text, run_tool
from tests.conftest import run_git

BASE = "http://127.0.0.1:8765"
WS_HOST = {"Host": "127.0.0.1:8765"}
SECRET = "sk-ant-api03-" + "q" * 40


def project(root, name="proj", pid="p1", cloud_ok=False):
    return Project(id=pid, name=name, path=str(root), description="", cloud_ok=cloud_ok, created_at="", updated_at="")


@pytest.fixture
def ctx(repo):
    (repo / ".env").write_text(f"ANTHROPIC_API_KEY={SECRET}\nprint = 1\n", encoding="utf-8")
    (repo / "config.py").write_text(f"KEY = '{SECRET}'\nTIMEOUT = 5\n", encoding="utf-8")
    (repo / "long.txt").write_text("".join(f"line {n}\n" for n in range(1, 701)), encoding="utf-8")
    return ToolContext([project(repo.resolve())])


def tool(name, args, ctx):
    return asyncio.run(run_tool(name, args, ctx))


# --- the tools ---


def test_list_and_read(ctx):
    listing, error = tool("list_files", {"project": "PROJ", "depth": 2}, ctx)  # names ignore case
    assert not error and "src/\n  app.py" in listing and ".env" in listing and "(secret: not readable)" in listing
    text, error = tool("read_file", {"project": "proj", "path": "README.md"}, ctx)
    assert not error and text == "README.md (lines 1-1 of 1)\n1| # Проект"
    first, _ = tool("read_file", {"project": "proj", "path": "long.txt"}, ctx)
    assert "lines 1-300 of 700" in first and "read on with start_line=301" in first
    page, _ = tool("read_file", {"project": "proj", "path": "long.txt", "start_line": 650, "line_count": 1000}, ctx)
    assert "lines 650-700 of 700" in page and "read on" not in page  # line_count is capped, the end is the end
    secret, error = tool("read_file", {"project": "proj", "path": ".env"}, ctx)
    assert error and "holds secrets" in secret
    masked, error = tool("read_file", {"project": "proj", "path": "config.py"}, ctx)
    assert not error and SECRET not in masked and "[REDACTED]" in masked and "TIMEOUT = 5" in masked
    escape, error = tool("read_file", {"project": "proj", "path": "../../etc/hosts"}, ctx)
    assert error and "leaves the project" in escape


def test_the_hubs_own_data_and_the_names_windows_opens_as_secret(ctx, repo, monkeypatch):
    """The audit (D46): a checkout of LocalChat holds the hub's data - its tokens, database, logs - and no path through
    a project reaches it (a sandbox's copy there is a project of its own); a secret file is known by the names Windows
    opens as it (.env., .env::$DATA)."""
    data = repo / "data"
    (data / "tokens").mkdir(parents=True)
    (data / "tokens" / "app.token").write_text("token-of-the-window\n", encoding="utf-8")
    monkeypatch.setattr(projects, "HUB_DATA", data.resolve())
    said, error = tool("read_file", {"project": "proj", "path": "data/tokens/app.token"}, ctx)
    assert error and "hub's own data" in said
    listing, _ = tool("list_files", {"project": "proj", "depth": 3}, ctx)
    assert "data/" not in listing and "app.token" not in listing
    found, _ = tool("search_code", {"project": "proj", "query": "token-of-the-window"}, ctx)
    assert "app.token" not in found
    with pytest.raises(changes.WriteError, match="hub's own data"):
        changes.plan("write_file", ctx.projects[0], {"path": "data/tokens/app.token", "content": "x"})
    copy = data / "worktrees" / "chat" / "p1"
    copy.mkdir(parents=True)
    (copy / "a.txt").write_text("копия\n", encoding="utf-8")
    text, error = tool("read_file", {"project": "copy", "path": "a.txt"}, ToolContext([project(copy, "copy", "p2")]))
    assert not error and "копия" in text

    for name in (".env.", ".env ", ".env::$DATA"):
        said, error = tool("read_file", {"project": "proj", "path": name}, ctx)
        assert error and "holds secrets" in said, name


def test_a_link_to_a_secret_file_is_one(ctx, repo, monkeypatch):
    """A link named notes.txt that leads to .env is .env (D46). Where Windows lets no test make a symbolic link
    (Developer Mode off), the path resolves as the link would."""
    try:
        os.symlink(repo / ".env", repo / "notes.txt")
    except OSError:
        resolve = projects.resolve_inside
        monkeypatch.setattr(projects, "resolve_inside",
                            lambda root, rel: resolve(root, ".env" if rel == "notes.txt" else rel))  # fmt: skip
    said, error = tool("read_file", {"project": "proj", "path": "notes.txt"}, ctx)
    assert error and "holds secrets" in said


def test_search_git_and_bad_input(ctx):
    found, error = tool("search_code", {"project": "proj", "query": "print"}, ctx)
    assert not error and "src/app.py:1: print('hi')" in found
    assert ".env" not in found  # .env has "print" too: never shown (ripgrep skips it, the walk filters it)
    status, _ = tool("git_status", {"project": "proj"}, ctx)
    assert "branch main" in status and "M src/app.py" in status and "first (Test," in status
    diff, _ = tool("git_diff", {"project": "proj", "path": "src/app.py"}, ctx)
    assert "+print('new line')" in diff
    assert tool("git_diff", {"project": "proj", "path": ".env"}, ctx)[1] is True

    for name, args, says in [
        ("read_file", {"project": "other", "path": "x"}, "among those you may read: proj"),
        ("read_file", {"project": "proj", "path": 5}, "`path` must be a string"),
        ("read_file", {"project": "proj"}, "`path` must be a string"),
        ("read_file", {"project": "proj", "path": "README.md", "start_line": "2"}, "whole number"),
        ("search_code", {"project": "proj", "query": "x", "regex": "yes"}, "true or false"),
        ("search_code", {"project": "proj", "query": "(", "regex": True}, "the search failed"),
        ("rm_rf", {}, "no tool"),
        ("list_files", ["proj"], "JSON object"),
        ("list_files", {"project": "proj", "__invalid_json__": "{"}, ""),  # unknown keys are ignored
    ]:
        output, error = tool(name, args, ctx)
        if says:
            assert error and says in output, (name, args, output)


def test_terminals_and_processes(ctx):
    class Terminals:
        async def list(self, project_id, limit):
            return [{"id": "t1", "title": "npm run dev", "status": "running", "exit_code": None,
                     "created_at": "2026-10-27T01:00:00.000Z"}]  # fmt: skip

        async def output(self, terminal_id, max_chars):
            return {"data": "\x1b[32mready\x1b[0m on :3000\r\nprogress 10%\rprogress 100%\r\n" + f"token {SECRET}\n"}

    node = {"name": "node.exe", "pid": 7, "ports": [3000], "cpu": 1.5, "memory_mb": 80, "depth": 0}

    class Processes:
        def __init__(self):
            self.latest = {"p1": [{**node, "args": f"server.js --key={SECRET}"}]}

    ctx.terminals, ctx.processes = Terminals(), Processes()
    listing, _ = tool("terminal_output", {"project": "proj"}, ctx)
    assert "t1  npm run dev  [running]" in listing
    tail, _ = tool("terminal_output", {"project": "proj", "terminal": "npm run dev"}, ctx)
    assert "ready on :3000\nprogress 100%" in tail and SECRET not in tail  # colours out, \r rewrites, secret masked
    procs, _ = tool("list_processes", {"project": "proj"}, ctx)
    assert "node.exe (pid 7) ports 3000" in procs and SECRET not in procs
    assert plain_terminal_text("a\rb\r\nc") == "b\nc"


# --- the history as prompt ---


def test_tool_turns_in_the_prompt_and_an_unanswered_call():
    call = {"type": "tool_call", "id": "c1", "name": "read_file", "input": {"project": "p", "path": "a"}}
    history = [
        MessageRow(1, 1, "user", None, [{"type": "text", "text": "почитай"}], ""),
        MessageRow(2, 2, "assistant", "fake/x", [{"type": "text", "text": "Смотрю"}, call], ""),
        MessageRow(3, 3, "tool", None, [{"type": "tool_result", "id": "c1", "name": "read_file", "output": "ok"}], ""),
        MessageRow(4, 4, "assistant", "fake/x", [{**call, "id": "c2", "name": "git_status"}], ""),
    ]
    prompt = _to_prompt(history)
    assert [m["role"] for m in prompt] == ["user", "assistant", "tool", "assistant", "tool"]
    assert prompt[1]["tool_calls"] == [{"id": "c1", "name": "read_file", "input": {"project": "p", "path": "a"}}]
    assert prompt[2] == {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "ok", "is_error": False}
    assert prompt[4]["tool_call_id"] == "c2" and prompt[4]["content"] == NOT_RUN  # the run crashed before it


def test_tool_turns_on_the_wire():
    call = {"id": "c1", "name": "read_file", "input": {"path": "a"}}
    messages = [
        {"role": "system", "content": "проекты"},
        {"role": "user", "content": "почитай"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "текст", "is_error": False},
    ]
    system, turns = to_wire(messages, "anthropic")
    assert system == "проекты"
    assert turns[1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "read_file",
                                                          "input": {"path": "a"}}]}  # fmt: skip
    assert turns[2]["role"] == "user" and turns[2]["content"][0]["type"] == "tool_result"
    use = {"type": "tool_use", "id": "toolu_1", "name": "git_status", "input": {"project": "p"}}
    raw = {"content": [{"type": "thinking", "thinking": "t", "signature": "s"}, use]}
    assert [b["type"] for b in replay_blocks(raw["content"])] == ["thinking", "tool_use"]
    wire = openai_wire(messages[2])
    assert wire["content"] is None and json.loads(wire["tool_calls"][0]["function"]["arguments"]) == {"path": "a"}
    assert openai_wire(messages[3]) == {"role": "tool", "tool_call_id": "c1", "content": "текст"}


def test_openai_tool_calls_come_in_pieces():
    def piece(index, name, args, call_id=None):
        part = {"index": index, "function": {"name": name, "arguments": args}}
        return {"choices": [{"delta": {"tool_calls": [part | ({"id": call_id} if call_id else {})]}}]}

    chunks = [
        {"choices": [{"delta": {"content": "Смотрю"}}]},
        piece(0, "read_", '{"pro', "call_a"),
        piece(0, "file", 'ject": "p"}'),
        piece(1, "git_status", "{bad"),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=body)

    provider = OpenAICompatProvider("llama-swap", "http://x", transport=httpx.MockTransport(handler))
    tools = [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in TOOLS]

    async def collect():
        return [e async for e in provider.stream_chat("coder", [{"role": "user", "content": "x"}], "low",
                                                        asyncio.Event(), tools=tools)]  # fmt: skip

    events = asyncio.run(collect())
    end = events[-1]
    assert [t["function"]["name"] for t in seen["tools"]] == [t["name"] for t in TOOLS]
    assert end.tool_calls == [
        {"id": "call_a", "name": "read_file", "input": {"project": "p"}},
        {"id": "call_1", "name": "git_status", "input": {"__invalid_json__": "{bad"}},
    ]
    assert end.stop_reason == "tool_calls"


# --- the tool loop through the hub ---


class ToolProvider:
    """Asks for tools as scripted (one list of calls per step), then answers with text."""

    name = "fake"

    def __init__(self, steps, locality="local"):
        self.steps = list(steps)
        self.calls = []  # (messages, tools) per request
        self.models = [Model("fake/echo", "fake", "echo", "Echo", locality, {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.calls.append((messages, tools))
        if tools and self.steps:
            calls = self.steps.pop(0)
            yield StreamEvent("content", "Смотрю. ")
            yield StreamEvent("end", usage={"input_tokens": 10, "output_tokens": 2}, stop_reason="tool_use",
                              tool_calls=calls)  # fmt: skip
            return
        yield StreamEvent("content", "Готово")
        yield StreamEvent("end", usage={"input_tokens": 20, "output_tokens": 3}, stop_reason="end_turn")


@pytest.fixture
def hub():
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def run_chat(hub, provider, attach=None, text="что в README?"):
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    if attach:
        assert hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": attach}).json()["projects"] == attach
    with hub.websocket_connect("/v1/events", headers={**dict(hub.headers), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": text, "model_id": "fake/echo"})
        events = []
        while not events or events[-1]["type"] not in ("chat.done", "chat.error"):
            events.append(ws.receive_json())
    return conv, events


def test_the_tool_loop(hub, repo):
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    read = {"id": "c1", "name": "read_file", "input": {"project": "proj", "path": "README.md"}}
    status = {"id": "c2", "name": "git_status", "input": {"project": "proj"}}
    provider = ToolProvider([[read, status]])
    conv, events = run_chat(hub, provider, attach=[pid])

    done = events[-1]["payload"]
    assert events[-1]["type"] == "chat.done" and done["steps"] == 2
    assert done["usage"]["input_tokens"] == 30 and done["usage"]["output_tokens"] == 5  # both steps
    assert [e["payload"]["name"] for e in events if e["type"] == "chat.tool"] == ["read_file", "git_status"]
    first, second = provider.calls
    assert [t["name"] for t in first[1]] == [t["name"] for t in CHAT_TOOLS]  # reading, then writing (stage 5)
    user, ctx_message = first[0][:2]  # the context comes right after the user's message, stored (D14)
    assert user["role"] == "user" and ctx_message["role"] == "system"
    assert "- proj [local-only]:" in ctx_message["content"] and '<project name="proj">' in ctx_message["content"]
    assert '<live_state project="proj">' in ctx_message["content"] and "M src/app.py" in ctx_message["content"]
    assert second[0][:2] == first[0][:2]  # the second step sees the very same start
    results = [m for m in second[0] if m["role"] == "tool"]
    assert "1| # Проект" in results[0]["content"] and "branch main" in results[1]["content"]

    saved = hub.get(f"/v1/conversations/{conv}").json()
    assert saved["projects"] == [pid]
    assert [m["role"] for m in saved["messages"]] == ["user", "system", "assistant", "tool", "assistant"]
    assert [b["type"] for b in saved["messages"][2]["content"]] == ["text", "tool_call", "tool_call"]
    assert saved["messages"][3]["content"][0]["output"].startswith("README.md")
    assert saved["messages"][2]["usage"]["output_tokens"] == 2  # each step keeps its own usage

    # the next message: the map and the project did not change, so only the live state comes again
    provider.steps = []
    hub.post(f"/v1/conversations/{conv}/messages", json={"content": "а теперь?", "model_id": "fake/echo"})
    for _ in range(200):
        if len(hub.get(f"/v1/conversations/{conv}").json()["messages"]) == 8:
            break
        hub.portal.call(asyncio.sleep, 0.02)
    second_context = hub.get(f"/v1/conversations/{conv}").json()["messages"][6]
    keys = [b["key"] for b in second_context["content"]]
    assert second_context["role"] == "system" and keys == ["header", f"live:{pid}", "tools"]


def test_a_chat_without_projects_has_tools_and_cloud_models_see_only_cloud_projects(hub, repo):
    provider = ToolProvider([])
    run_chat(hub, provider)
    messages, tools = provider.calls[0]
    assert [m["role"] for m in messages] == ["user"]  # an ordinary chat: no context
    assert {"web_search", "web_fetch"} <= {t["name"] for t in tools}  # but the web tools, local model or not (D19)

    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local only by default
    cloud = ToolProvider([], locality="cloud")
    hub.app.state.gateway._providers.pop("fake", None)
    _conv, _events = run_chat(hub, cloud, attach=[pid])
    messages, _tools = cloud.calls[0]
    shown = messages[1]["content"]
    assert "proj [local-only: you may not see more of it]" in shown
    assert "[proj: local-only, details hidden from cloud models]" in shown and "Проект" not in shown
    assert "src/app.py" not in shown  # neither its files nor its live state


def test_a_cloud_model_may_not_take_over_a_chat_with_local_only_data(hub, repo):
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local only
    local = ToolProvider([[{"id": "c1", "name": "read_file", "input": {"project": "proj", "path": "README.md"}}]])
    conv, _events = run_chat(hub, local, attach=[pid])
    hub.app.state.gateway._providers.pop("fake", None)
    cloud = ToolProvider([], locality="cloud")
    hub.app.state.gateway.register(cloud)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    refused = hub.post(f"/v1/conversations/{conv}/messages", json={"content": "дальше", "model_id": "fake/echo"})
    assert refused.status_code >= 400 and "only local models may see" in refused.text and cloud.calls == []
    hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": True})  # the user allows the cloud: it may go on
    allowed = hub.post(f"/v1/conversations/{conv}/messages", json={"content": "дальше", "model_id": "fake/echo"})
    assert allowed.status_code == 202


def test_the_step_limit(hub, repo, monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_TOOL_STEPS", 3)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]

    def asks(count):
        return [[{"id": f"c{n}", "name": "git_status", "input": {"project": "proj"}}] for n in range(count)]

    # three steps run their tools; the fourth call is answered "answer now", and the model then answers
    provider = ToolProvider(asks(4))
    conv, events = run_chat(hub, provider, attach=[pid])
    assert events[-1]["payload"]["steps"] == 5
    assert all(tools is not None for _m, tools in provider.calls)  # the tool list never changes in a chat
    results = [m for m in hub.get(f"/v1/conversations/{conv}").json()["messages"] if m["role"] == "tool"]
    assert results[-1]["content"][0]["output"] == chat_module.STEP_LIMIT and len(results) == 4

    # a model that keeps asking even then: the run ends after one more refusal
    stubborn = ToolProvider(asks(10))
    hub.app.state.gateway._providers.pop("fake", None)
    _conv, events = run_chat(hub, stubborn, attach=[pid])
    assert events[-1]["type"] == "chat.done" and events[-1]["payload"]["steps"] == 5 and len(stubborn.calls) == 5


def test_context_settings_shape_the_next_context(hub, repo):
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    hub.app.state.gateway.register(ToolProvider([]))
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})
    pin = {"project_id": pid, "path": "README.md"}
    settings = hub.put(f"/v1/conversations/{conv}/context", json={"map": False, "pinned": [pin, pin]}).json()
    assert settings == {"map": False, "live": True, "pinned": [pin], "mode": "ask", "instructions": "", "preset": "",
                        "refusal_vector": ""}  # fmt: skip

    def ask(text, count):
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": text, "model_id": "fake/echo"})
        for _ in range(300):
            messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
            if len(messages) == count:
                return [m for m in messages if m["role"] == "system"][-1]["content"]
            hub.portal.call(asyncio.sleep, 0.02)
        raise AssertionError("no answer")

    first = ask("раз", 3)
    assert [b["key"] for b in first] == ["header", f"project:{pid}", f"file:{pid}:README.md", f"live:{pid}", "tools"]
    assert "1| # Проект" in next(b["text"] for b in first if b["key"].startswith("file:"))
    hub.put(f"/v1/conversations/{conv}/context", json={"live": False})
    second = ask("два", 6)
    assert [b["key"] for b in second] == ["header", "tools"]  # nothing changed, the live state is off
    assert hub.get(f"/v1/conversations/{conv}").json()["context"]["live"] is False


def test_a_projects_session_is_made_as_such(hub, repo):
    """2026-10-28: a chat begun in «Проекты» is that project's session - its project attached from the start, and
    the list says whose it is; an unknown project - 404."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    cid = hub.post("/v1/conversations", json={"home_project": pid}).json()["id"]
    plain = hub.post("/v1/conversations", json={}).json()["id"]
    assert hub.post("/v1/conversations", json={"home_project": "nope"}).status_code == 404
    listed = {c["id"]: c for c in hub.get("/v1/conversations").json()["conversations"]}
    assert listed[cid]["home_project"] == pid and listed[plain]["home_project"] is None
    assert [p["id"] if isinstance(p, dict) else p for p in hub.get(f"/v1/conversations/{cid}").json()["projects"]] == [
        pid
    ]


def test_git_diff_staged_a_branch_and_the_log(ctx, repo):
    """The audit (D46): a model sees what is staged and what its branch changed since main (a review), reads the log
    in a chat as through MCP; a secret file is left out of a diff even with a space in its name."""
    (repo / "my secrets.env").write_text(f"ANTHROPIC_API_KEY={SECRET}\n", encoding="utf-8")
    run_git(repo, "add", "src/app.py", "my secrets.env")
    staged, error = tool("git_diff", {"project": "proj", "staged": True}, ctx)
    assert not error and "+print('new line')" in staged and "my secrets.env" not in staged and SECRET not in staged
    run_git(repo, "checkout", "-q", "-b", "feature")
    run_git(repo, "commit", "-q", "-m", "feature work")
    since, error = tool("git_diff", {"project": "proj", "base": "main"}, ctx)
    assert not error and "+print('new line')" in since and "my secrets.env" not in since
    assert tool("git_diff", {"project": "proj", "base": "--output=x"}, ctx)[1] is True
    nope, error = tool("git_diff", {"project": "proj", "base": "nope"}, ctx)
    assert error and "no branch or commit 'nope'" in nope
    log, error = tool("git_log", {"project": "proj", "count": 1}, ctx)
    assert not error and "feature work" in log and "first" not in log


def test_encodings_line_ends_and_invisible_characters(ctx, repo):
    """The audit (D46): a byte order mark is not text, UTF-16 is not binary, a model is told of CRLF - and of bidi
    or zero-width characters that can make code read differently from what it does."""
    (repo / "bom.txt").write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    (repo / "wide.txt").write_bytes("привет\n".encode("utf-16"))
    data = projects.read_file(repo, "bom.txt")
    assert data["text"] == "one\r\ntwo\r\n" and data["encoding"] == "utf-8 BOM" and data["eol"] == "CRLF"
    wide = projects.read_file(repo, "wide.txt")
    assert not wide["binary"] and wide["text"] == "привет\n" and wide["encoding"] == "utf-16" and wide["eol"] == "LF"
    shown, error = tool("read_file", {"project": "proj", "path": "bom.txt"}, ctx)
    assert not error and "CRLF line ends, utf-8 BOM)" in shown.splitlines()[0] and "1| one" in shown
    (repo / "trojan.py").write_text("ok = True\nif user == 'admin\u202e \u2066':  # admin\n    grant()\n",
                                    encoding="utf-8")  # fmt: skip
    shown, error = tool("read_file", {"project": "proj", "path": "trojan.py"}, ctx)
    assert not error and "invisible characters (bidi or zero-width) on lines 2:" in shown
