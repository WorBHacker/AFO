"""The window's pool for the hub's requests (app/tasks.py) with the real hub client against a real HTTP server: a long
request gives its place among the window's four threads to the others while it waits (0.1.16 step 9б)."""

import http.server
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication

from app.hub_client import HubClient
from app.ui.controller import Controller
from app.ui.theme import Theme
from common.paths import data_paths

SLOW_S = 3.0


class _Hub(http.server.BaseHTTPRequestHandler):
    """`/v1/slow` answers in 3 s, `/v1/quick` at once, `/v1/drop` breaks the connection without an answer; anything
    else - 404, as the hub says to a path it does not know."""

    def do_GET(self):
        if self.path.startswith("/v1/slow"):
            time.sleep(SLOW_S)
        elif self.path.startswith("/v1/drop"):
            time.sleep(0.5)
            self.close_connection = True
            return
        found = self.path.startswith(("/v1/slow", "/v1/quick"))
        body = json.dumps({"path": self.path} if found else {"detail": "not here"}).encode()
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PUT = do_DELETE = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    """The window's controller attached to the real hub client of a real HTTP server, as `python -m app` does."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    controller = Controller(data_paths().ensure(), QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat),
                            Theme("dark"))  # fmt: skip
    controller.attach(HubClient(f"http://127.0.0.1:{server.server_address[1]}", "token"), start_events=False)
    wait_until(lambda: not controller.bg.busy, 15.0)  # the window's first questions: 404s here
    try:
        yield controller
    finally:
        controller.shutdown()
        server.shutdown()
        server.server_close()


def wait_until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met"
        QGuiApplication.processEvents()
        time.sleep(0.005)
    QGuiApplication.processEvents()


def test_a_long_request_does_not_hold_the_quick_ones(window):
    """Four long requests (a model's launch check: up to 330 s) took all four of the window's threads: every other
    button waited for them. Now a request the client gives more than the usual 30 s lets its place go while it waits
    and takes it back after; the pool's count comes back even, after an answer and after a broken connection. One
    of 30 s keeps its place: the boundary stays where it was."""
    bg, client = window.bg, window.client
    answered = {}

    def ask(name, path, timeout=None):
        started = time.monotonic()
        bg.run(lambda: client._call("GET", path, timeout=timeout),
               lambda _r: answered.__setitem__(name, time.monotonic() - started),
               lambda e: answered.__setitem__(name, e))  # fmt: skip

    for i in range(4):
        ask(f"slow{i}", "/v1/slow", timeout=60)
    ask("quick", "/v1/quick")
    wait_until(lambda: "quick" in answered, 10.0)
    assert answered["quick"] < 1.0, f"the quick one waited for the long ones: {answered['quick']:.1f} s"
    wait_until(lambda: len(answered) == 5 and not bg.busy, 10.0)
    assert all(isinstance(answered[f"slow{i}"], float) for i in range(4)), answered
    assert (bg._pool.maxThreadCount(), bg._pool.activeThreadCount()) == (4, 0)

    answered.clear()  # a long one that fails: its place comes back too
    for i in range(4):
        ask(f"drop{i}", "/v1/drop", timeout=60)
    wait_until(lambda: len(answered) == 4 and not bg.busy, 10.0)
    assert all(isinstance(e, Exception) for e in answered.values()), answered
    assert (bg._pool.maxThreadCount(), bg._pool.activeThreadCount()) == (4, 0)

    answered.clear()  # 30 s - an ordinary request: it keeps its place
    for i in range(4):
        ask(f"slow{i}", "/v1/slow", timeout=30)
    ask("quick", "/v1/quick")
    wait_until(lambda: len(answered) == 5 and not bg.busy, 15.0)
    assert answered["quick"] > SLOW_S - 0.5, answered
    assert (bg._pool.maxThreadCount(), bg._pool.activeThreadCount()) == (4, 0)
