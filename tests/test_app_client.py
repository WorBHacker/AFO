"""The app's hub client: its calls against the real API, and finding or starting a real hub process."""

import http.server
import json
import os
import socket
import subprocess
import threading
import time

import pytest
from starlette.testclient import TestClient

from app.hub_client import HubClient, HubError, connect, hub_address, probe
from common.paths import data_paths
from hub.server import create_app
from hub.settings import LLAMA_SWAP_URL_ENV, PORT_ENV, HubSettings
from tests.test_chat import FakeProvider, default_script

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    with TestClient(app, base_url=BASE) as http:
        app.state.gateway.register(FakeProvider(default_script()))
        http.portal.call(app.state.gateway.sync_catalog)
        token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield HubClient(BASE, token, http=http)


def test_calls_through_the_api(client):
    assert "fake/echo" in {m["id"] for m in client.models()}
    cid = client.create_conversation()
    assert client.rename(cid, "Тест")["title"] == "Тест"
    assert client.send(cid, "привет", "fake/echo", "low")["user_message"]["role"] == "user"
    assert client.conversation(cid)["title"] == "Тест"
    assert client.conversations()["conversations"][0]["id"] == cid
    assert set(client.status()) >= {"gpu", "memory", "local", "busy"}
    assert "today_cost_usd" in client.usage()
    assert client.connections()["connections"][0]["set"] is False
    assert client.events_url == "ws://127.0.0.1:8765/v1/events"


def test_errors_carry_the_hubs_detail(client):
    cid = client.create_conversation()
    with pytest.raises(HubError, match="unknown model"):
        client.send(cid, "x", "nope/model", "low")
    with pytest.raises(HubError, match="no such conversation"):
        client.conversation("ghost")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_address_comes_from_hub_json_or_the_port_setting(isolated_data_dir, monkeypatch):
    paths = data_paths().ensure()
    monkeypatch.setenv(PORT_ENV, "9999")
    assert hub_address(paths) == "http://127.0.0.1:9999"
    paths.hub_info.write_text(json.dumps({"host": "127.0.0.1", "port": 8123}), encoding="utf-8")
    assert hub_address(paths) == "http://127.0.0.1:8123"
    # 43.8 (the live check): a hub that ended without tidying left its hub.json (2026-10-29) - its port is not the
    # hub's any more (another program held it), the configured one is
    import subprocess
    import sys

    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    paths.hub_info.write_text(json.dumps({"pid": int(gone.stdout.strip()), "host": "127.0.0.1", "port": 8765}),
                              encoding="utf-8")  # fmt: skip
    assert hub_address(paths) == "http://127.0.0.1:9999"
    paths.hub_info.write_text(json.dumps({"pid": os.getpid(), "host": "127.0.0.1", "port": 8124}), encoding="utf-8")
    assert hub_address(paths) == "http://127.0.0.1:8124"  # alive: its own word


def test_a_foreign_program_on_the_port_is_not_taken_for_the_hub(monkeypatch):
    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"service": "something-else"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert probe(f"http://127.0.0.1:{port}") == "foreign"
        monkeypatch.setenv(PORT_ENV, str(port))
        with pytest.raises(HubError, match="занят"):
            connect(data_paths().ensure(), start=False)
    finally:
        server.shutdown()
    assert probe(f"http://127.0.0.1:{free_port()}") == "absent"


def test_connect_starts_a_hub_that_outlives_the_client(monkeypatch):
    port = free_port()
    monkeypatch.setenv(PORT_ENV, str(port))
    monkeypatch.setenv(LLAMA_SWAP_URL_ENV, "http://127.0.0.1:9")
    paths = data_paths().ensure()
    with pytest.raises(HubError, match="не запущен"):
        connect(paths, start=False)
    hub = None
    try:
        hub = connect(paths, timeout=60)
        assert hub.base_url == f"http://127.0.0.1:{port}"
        assert hub.status()["local"]["reachable"] is False
        hub.close()
        again = connect(paths, start=False)  # the hub is still there for the next window
        again.shutdown()
        deadline = time.monotonic() + 30
        while probe(again.base_url) != "absent":
            assert time.monotonic() < deadline, "the hub did not stop"
            time.sleep(0.2)
    finally:
        if probe(f"http://127.0.0.1:{port}") == "ok":
            pid = json.loads(paths.hub_info.read_text(encoding="utf-8"))["pid"]
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
