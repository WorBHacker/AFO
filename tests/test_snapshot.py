"""A picture of a web page for a model that sees pictures (stage 61.7): drawn by the real Edge without a window, only
this computer's pages and a project's or a task's HTML files, nothing fetched from the internet; a coordinator that
sees pictures gets the tool and the picture."""

import base64
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from hub import agents, snapshot
from hub import chat as chat_module
from hub.gateway.base import Model
from tests.test_agents import ByTask, ended, outputs, plug, profile
from tests.test_tools import hub  # noqa: F401 - the hub fixture

pytestmark = pytest.mark.skipif(snapshot.edge() is None, reason="Microsoft Edge is not on this computer")

LATE = "#00ccff"  # the colour a page's script gives it after a second
PAGE = ("<!doctype html><html><body style='margin:0;background:#ffcc00'><h1>ждём</h1>"
        "<img src='http://example.org/far.png'>"
        "<script>setTimeout(() => { document.body.style.background = '" + LATE + "' }, 1000)</script>"
        "</body></html>")  # fmt: skip


def picture(data):
    image = QImage.fromData(data, "PNG")
    assert not image.isNull()
    return image


class Pages:
    """A server on this computer: the page to every GET (/slow - after 15 s), and every request it got - a proxy's
    too (the whole URL)."""

    def __init__(self):
        self.asked = []
        pages = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def do_GET(self) -> None:
                pages.asked.append(self.path)
                if self.path == "/slow":
                    time.sleep(15)
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True  # a /slow one still asleep does not hold the test's end
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def pages():
    found = Pages()
    yield found
    found.close()


def test_a_page_of_this_computer_is_drawn_and_nothing_goes_to_the_internet(pages, monkeypatch):
    """localhost, 127.0.0.1, [::1] - not AFO's own ports, not a site, not a file:// address; the page's scripts run
    before the picture; a request beyond this computer goes to the proxy that is not there - here a server of the test
    stands in for it and sees the request come; a page that does not come in time is given up, Edge stopped."""
    own = {8765, 8080}
    assert snapshot.page_url(f"http://localhost:{pages.port}/app", own) == f"http://localhost:{pages.port}/app"
    assert snapshot.page_url("http://[::1]:5173/", own) and snapshot.page_url("https://127.0.0.1:3000", own)
    for url, said in [("http://127.0.0.1:8765/v1/models", "AFO's own"), ("http://localhost:8080/", "AFO's own"),
                      ("https://example.org/", "not this computer"), ("http://192.168.1.5:80/", "not this computer"),
                      ("file:///C:/Windows/win.ini", "`url`"), ("http://localhost:99999/", "`url`"), ("", "`url`"),
                      ("ftp://localhost/", "`url`")]:  # fmt: skip
        with pytest.raises(snapshot.SnapshotError, match=said):
            snapshot.page_url(url, own)
    assert snapshot.size({}) == (snapshot.WIDTH, snapshot.HEIGHT) and snapshot.size({"width": 640}) == (640, 800)
    for wrong in ({"width": 100}, {"height": 5000}, {"width": True}, {"width": "640"}):
        with pytest.raises(snapshot.SnapshotError):
            snapshot.size(wrong)
    profiles = set(Path(tempfile.gettempdir()).glob("afo-snapshot-*"))
    stand_in = Pages()  # the proxy beyond which the internet would be
    monkeypatch.setattr(snapshot, "NOWHERE", f"http://127.0.0.1:{stand_in.port}")
    try:
        data = snapshot.take(f"http://localhost:{pages.port}/app", 640, 400)
    finally:
        stand_in.close()
    image = picture(data)
    assert (image.width(), image.height()) == (640, 400)
    assert image.pixelColor(320, 300).name() == LATE  # the page's script ran before the picture
    assert "/app" in pages.asked
    assert "http://example.org/far.png" in stand_in.asked  # the picture from the internet went to the proxy only
    began = time.monotonic()
    with pytest.raises(snapshot.SnapshotError, match="not drawn in 3 s"):
        snapshot.take(f"http://127.0.0.1:{pages.port}/slow", timeout_s=3)
    assert time.monotonic() - began < 10  # Edge was stopped, not waited for
    assert set(Path(tempfile.gettempdir()).glob("afo-snapshot-*")) == profiles  # no profile left behind


def test_a_projects_html_file_is_drawn_and_no_other(tmp_path):
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_bytes(PAGE.encode())
    (tmp_path / "app.py").write_bytes(b"print('hi')\n")
    (tmp_path / ".env.html").write_bytes(b"<p>TOKEN=x1y2z3</p>")
    address = snapshot.page_file(tmp_path, "site\\index.html")
    assert address.startswith("file:///") and address.endswith("/site/index.html")
    for path, said in [("app.py", "only an HTML file"), ("none.html", "no such file"), ("", "`path`"),
                       ("../outside.html", "leaves the project"), ("C:/Windows/x.html", "leaves the project"),
                       (".env.html", "a file of secrets")]:  # fmt: skip
        with pytest.raises(snapshot.SnapshotError, match=said):
            snapshot.page_file(tmp_path, path)
    image = picture(snapshot.take(address, 400, 300))
    assert (image.width(), image.height()) == (400, 300) and image.pixelColor(200, 250).name() == LATE


def _eyes(hub, name, locality, scripts, **extra):  # noqa: F811
    """A ByTask whose model sees pictures."""
    provider = ByTask(name, locality, scripts, pause=0.02, **extra)
    provider.models = [Model(f"{name}/echo", name, "echo", "Echo", locality, {"vision": True}, {})]
    return plug(hub, provider)


def _write_page(path="site/page.html"):
    return {"Сделай страницу": [{"name": "write_file", "input": {"project": "proj", "path": path, "content": PAGE}}]}


def test_a_coordinator_that_sees_pictures_looks_at_the_page_its_task_made(hub, repo):  # noqa: F811
    """61.7: a coordinator whose model sees pictures gets page_snapshot with its batches; the page a task wrote in
    its copy is drawn, kept with the chat (the window shows it under the call) and comes to the model with its next
    request; AFO's own pages are not drawn. A coordinator whose model does not see pictures does not get the tool."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    own = hub.app.state.settings.port
    look = [{"id": "s1", "name": "page_snapshot", "input": {"task_id": "a", "path": "site/page.html", "width": 640,
                                                            "height": 400}},
            {"id": "s2", "name": "page_snapshot", "input": {"url": f"http://127.0.0.1:{own}/v1/runs"}}]  # fmt: skip
    provider = _eyes(hub, "eyes", "local", {"Сделай": [[{"id": "b1", "name": "delegate_tasks", "input": {"tasks": [
        {"task_id": "a", "agent_profile": "Верстальщик", "prompt": "Сделай страницу"}]}}], look]},
        work=_write_page())  # fmt: skip
    profile(hub, name="Верстальщик", model_id="eyes/echo", tools=["write_file"], project_ids=[pid])
    boss = profile(hub, name="Координатор", model_id="eyes/echo", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    assert ended(hub, run_id, timeout=60)["status"] == "done"
    assert "page_snapshot" in [t["name"] for t in provider.calls[0][2]]  # given with delegate_tasks to one that sees
    _board, taken, refused = outputs(hub, run_id)
    assert taken["error"] is False and taken["media_type"] == "image/png" and taken["blob"]  # kept: shown under it
    assert taken["output"].startswith("The picture of site/page.html of task a («proj»), 640x400 pixels")
    assert refused["error"] is True and "AFO's own" in refused["output"]
    after = next(m for _ref, messages, _tools in provider.calls for m in messages if m.get("images"))
    assert after["role"] == "user" and after["content"] == chat_module.SNAPSHOTS_SHOWN
    (shown,) = after["images"]
    image = picture(base64.b64decode(shown["data"]))
    assert shown["media_type"] == "image/png" and (image.width(), image.height()) == (640, 400)
    assert image.pixelColor(320, 300).name() == LATE  # the page as the task left it in its copy
    assert not (repo / "site").exists()  # it is in the task's copy only
    plug(hub, ByTask("fake", "local", {}))
    blind = profile(hub, name="Слепой", model_id="fake/echo", tools=["delegate_tasks"])

    def tools_of(conn):
        run = agents.create_run(conn, agents.get_profile(conn, blind["id"]), "Сделай", [])
        return [t["name"] for t in json.loads(conn.execute("SELECT tools FROM conversations WHERE id = ?",
                                                           (run,)).fetchone()["tools"])]  # fmt: skip

    assert "task_diff" in (names := hub.app.state.db.call(tools_of)) and "page_snapshot" not in names


def test_a_cloud_coordinator_is_not_shown_a_local_only_projects_page(hub, repo):  # noqa: F811
    """D10: a local agent made the page in a local-only project - a cloud coordinator that sees pictures is not shown
    it."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local-only by default (D10)
    look = [{"id": "s1", "name": "page_snapshot", "input": {"task_id": "a", "path": "site/page.html"}}]
    _eyes(hub, "cloudy", "cloud", {"Сделай": [[{"id": "b1", "name": "delegate_tasks", "input": {"tasks": [
        {"task_id": "a", "agent_profile": "Верстальщик", "prompt": "Сделай страницу"}]}}], look]})  # fmt: skip
    plug(hub, ByTask("fake", "local", {}, pause=0.02, work=_write_page()))
    profile(hub, name="Верстальщик", tools=["write_file"], project_ids=[pid])
    boss = profile(hub, name="Координатор", model_id="cloudy/echo", tools=["delegate_tasks"])
    run_id = hub.post(f"/v1/agents/{boss['id']}/runs", json={"task": "Сделай"}).json()["id"]
    assert ended(hub, run_id, timeout=60)["status"] == "done"
    _board, refused = outputs(hub, run_id)
    assert refused["error"] is True and refused["output"] == agents.LOCAL_PAGES.format(name="proj")
    assert not refused.get("blob")
