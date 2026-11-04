"""The stdio adapter to the hub's MCP (stage 4.2, D32): forwarding, errors, and the real process end to end."""

import asyncio
import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import LATEST_PROTOCOL_VERSION
from starlette.testclient import TestClient

from common.paths import DATA_DIR_ENV, PROJECT_ROOT, data_paths
from hub import locate
from hub.auth import create_client
from hub.locate import probe
from hub.mcp_stdio import HUB_ERROR, PARSE_ERROR, Bridge, client_token, pump, sse_messages
from hub.secrets import STORE_ENV
from hub.server import create_app
from hub.settings import LLAMA_SWAP_URL_ENV, PORT_ENV, HubSettings
from tests.test_mcp import TOOLS

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def hub(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "lib.py").write_text("class Shared:\n    pass\n", encoding="utf-8")
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = client.post("/v1/projects", json={"path": str(shared)}).json()["id"]
        client.patch(f"/v1/projects/{pid}", json={"cloud_ok": True})
        yield client


def through(hub, token, lines):
    """What the adapter answers to each of `lines`, forwarded to the hub app in its own event loop."""

    async def run():
        transport = httpx.ASGITransport(app=hub.app)
        async with httpx.AsyncClient(transport=transport) as http:
            bridge = Bridge(BASE, token, http)
            answers = [await bridge.forward(line.encode("utf-8")) for line in lines]
            return answers, bridge.protocol

    return hub.portal.call(run)


def request(number, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params or {}})


INITIALIZE = request(
    1,
    "initialize",
    {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
)
INITIALIZED = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})


def test_the_bridge_forwards_messages_as_they_are(hub):
    token = hub.app.state.db.call(lambda c: create_client(c, "stdio-client"))
    lines = [
        INITIALIZE,
        INITIALIZED,
        request(2, "tools/list"),
        request(3, "tools/call", {"name": "file_outline", "arguments": {"project": "shared", "path": "lib.py"}}),
        request(4, "tools/call", {"name": "read_file", "arguments": {"project": "nope", "path": "x"}}),
        "{not json",
    ]
    (init, initialized, listed, outline, wrong, garbage), protocol = through(hub, token, lines)
    assert init[0]["id"] == 1 and init[0]["result"]["serverInfo"]["name"] == "AFO hub"
    assert protocol == init[0]["result"]["protocolVersion"]  # sent with every later request
    assert initialized == []  # a notification: the hub accepts it (202) and nothing goes back
    assert {t["name"] for t in listed[0]["result"]["tools"]} == TOOLS | {"approval_prompt"}
    assert "class Shared" in outline[0]["result"]["content"][0]["text"]
    assert wrong[0]["result"]["isError"] is True  # a tool's error is the tool's result, as over HTTP
    assert garbage[0]["id"] is None and garbage[0]["error"]["code"] == PARSE_ERROR


def test_a_refused_token_is_explained_to_requests_only(hub):
    (refused, notification), _ = through(hub, "wrong", [INITIALIZE, INITIALIZED])
    assert refused[0]["id"] == 1 and refused[0]["error"]["code"] == HUB_ERROR
    assert "token create" in refused[0]["error"]["message"]
    assert notification == []


def test_a_hub_that_went_away_is_found_again():
    calls = []

    async def run():
        async with httpx.AsyncClient() as http:
            bridge = Bridge("http://127.0.0.1:9", "t", http, reconnect=lambda: calls.append(1) or "http://127.0.0.1:9")
            return await bridge.forward(INITIALIZE.encode("utf-8"))

    (answer,) = asyncio.run(run())
    assert calls == [1]  # tried to find (or start) the hub once, then gave up on this message
    assert answer["error"]["code"] == HUB_ERROR and "does not answer" in answer["error"]["message"]


def test_requests_run_side_by_side_and_all_are_answered():
    class Slow:
        async def forward(self, line):
            message = json.loads(line)
            await asyncio.sleep(message["delay"])
            return [{"id": message["id"]}]

    lines = [json.dumps({"id": 1, "delay": 0.3}).encode() + b"\n", b"\n", json.dumps({"id": 2, "delay": 0}).encode()]
    written = []

    async def read_line():
        return lines.pop(0) if lines else b""

    asyncio.run(pump(Slow(), read_line, written.append))
    assert [json.loads(w)["id"] for w in written] == [2, 1]  # the quick one did not wait for the slow one
    assert all(w.endswith(b"\n") and w.count(b"\n") == 1 for w in written)  # one message per line


def test_an_event_stream_answer_is_read_too():
    stream = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\ndata: {"jsonrpc":"2.0",\ndata: "id":2}\n\n'
    assert [m["id"] for m in sse_messages(stream)] == [1, 2]


def test_the_token_comes_from_the_environment_or_the_saved_file(monkeypatch):
    paths = data_paths().ensure()
    assert client_token(paths, "cline") is None
    (paths.tokens / "cline.token").write_text("from-file\n", encoding="utf-8")
    assert client_token(paths, "cline") == "from-file"
    monkeypatch.setenv("LOCALCHAT_TOKEN", "from-env")
    assert client_token(paths, "cline") == "from-env"


# --- the real processes ---


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(isolated_data_dir):
    return {
        DATA_DIR_ENV: str(isolated_data_dir),
        PORT_ENV: str(free_port()),
        LLAMA_SWAP_URL_ENV: "http://127.0.0.1:9",
        STORE_ENV: "memory",
    }


def hub_cli(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "hub", *args],
        cwd=PROJECT_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_the_hub_leaves_the_starters_job_when_it_may(monkeypatch):
    started = []

    def popen(args, creationflags=0, **kwargs):
        started.append(creationflags)
        if creationflags & locate.BREAKAWAY:
            raise PermissionError("the job forbids breakaway")

    monkeypatch.setattr(locate.subprocess, "Popen", popen)
    locate.start_hub()
    if os.name == "nt":
        assert started[0] & locate.BREAKAWAY and not started[1] & locate.BREAKAWAY  # tried to leave, then stayed
    else:
        assert len(started) == 1


def test_without_a_token_the_adapter_says_how_to_make_one(env):
    result = hub_cli(env, "mcp", "--client", "nobody")
    assert result.returncode == 1
    assert "python -m hub token create nobody --save" in result.stderr
    assert result.stdout == ""  # stdout belongs to the protocol


def test_a_stdio_client_works_through_the_adapter_which_starts_the_hub(env, isolated_data_dir):
    saved = hub_cli(env, "token", "create", "stdio-test", "--save", "--local")
    assert saved.returncode == 0, saved.stderr
    assert "python -m hub mcp --client stdio-test" in saved.stdout
    token_file = isolated_data_dir / "tokens" / "stdio-test.token"
    assert token_file.read_text(encoding="utf-8") not in saved.stdout  # saved, not printed
    base = f"http://127.0.0.1:{env[PORT_ENV]}"
    assert probe(base) == "absent"

    server = StdioServerParameters(
        command=sys.executable, args=["-m", "hub", "mcp", "--client", "stdio-test"], env=env, cwd=str(PROJECT_ROOT)
    )

    async def session():
        async with Client(server) as client:
            tools = await client.list_tools()
            mapped = await client.call_tool("get_ecosystem_map", {})
            wrong = await client.call_tool("git_status", {"project": "nope"})
            started = await asyncio.to_thread(probe, base)  # the adapter started the hub
            return {t.name for t in tools.tools}, mapped.content[0].text, wrong, started

    try:
        names, mapped, wrong, started = asyncio.run(session())
        assert names == TOOLS | {"approval_prompt"}
        assert "projects" in mapped.lower()
        assert wrong.is_error and "no project 'nope'" in wrong.content[0].text
        assert started == "ok"
        assert "forwarding stdio to" in (isolated_data_dir / "logs" / "hub-mcp.log").read_text(encoding="utf-8")
    finally:
        hub_cli(env, "stop")
        deadline = time.monotonic() + 30
        while probe(base) != "absent" and time.monotonic() < deadline:
            time.sleep(0.2)
    revoked = hub_cli(env, "token", "revoke", "stdio-test")
    assert revoked.returncode == 0 and not token_file.exists()
