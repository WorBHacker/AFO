"""Stage 3: the reading tools a chat's model gets, the tool loop through the hub, and tool turns on the wire."""

import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import chat as chat_module
from hub.chat import NOT_RUN, _to_prompt
from hub.gateway.anthropic_provider import replay_blocks, to_wire
from hub.gateway.base import Model, StreamEvent
from hub.gateway.openai_compat import OpenAICompatProvider
from hub.gateway.openai_compat import to_wire as openai_wire
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings
from hub.store import MessageRow
from hub.tools import TOOLS, ToolContext, plain_terminal_text, run_tool

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
        ("read_file", {"project": "other", "path": "x"}, "the projects you can use: proj"),
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
    assert [t["name"] for t in first[1]] == [t["name"] for t in TOOLS]
    assert first[0][0]["role"] == "system" and "- proj:" in first[0][0]["content"]
    results = [m for m in second[0] if m["role"] == "tool"]
    assert "1| # Проект" in results[0]["content"] and "branch main" in results[1]["content"]

    saved = hub.get(f"/v1/conversations/{conv}").json()
    assert saved["projects"] == [pid]
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert [b["type"] for b in saved["messages"][1]["content"]] == ["text", "tool_call", "tool_call"]
    assert saved["messages"][2]["content"][0]["output"].startswith("README.md")
    assert saved["messages"][1]["usage"]["output_tokens"] == 2  # each step keeps its own usage


def test_no_projects_no_tools_and_cloud_models_see_only_cloud_projects(hub, repo):
    provider = ToolProvider([])
    run_chat(hub, provider)
    assert provider.calls[0][1] is None and provider.calls[0][0][0]["role"] == "user"  # an ordinary chat

    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local only by default
    cloud = ToolProvider([], locality="cloud")
    hub.app.state.gateway._providers.pop("fake", None)
    run_chat(hub, cloud, attach=[pid])
    messages, tools = cloud.calls[0]
    assert (
        tools is None
        and "only local models may see them, so you get nothing more than their names: proj" in (messages[0]["content"])
    )


def test_the_step_limit(hub, repo, monkeypatch):
    monkeypatch.setattr(chat_module, "MAX_TOOL_STEPS", 3)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    again = [[{"id": f"c{n}", "name": "git_status", "input": {"project": "proj"}}] for n in range(10)]
    provider = ToolProvider(again)
    _conv, events = run_chat(hub, provider, attach=[pid])
    assert events[-1]["payload"]["steps"] == 4  # three with tools, then one without them to finish
    assert [tools is not None for _m, tools in provider.calls] == [True, True, True, False]
