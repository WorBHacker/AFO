"""The web for the hub's models (stage 5.4, D19): a page as text, never this computer, asked unless the site is on
the list, and a chat that read the web asks for every write and command."""

import asyncio
import http.server
import threading

import pytest

from hub import web
from hub.tools import ToolError
from tests.test_changes import edit, finished, result_of, say, setup_chat, wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture

PAGE = (
    "<!doctype html><html><head><title>Справка — demo</title><style>body{color:red}</style></head><body>"
    "<nav><a href='#top'>наверх</a></nav><h1>Установка</h1><p>Поставьте пакет <a href='/pkg'>demo</a>.</p>"
    "<ul><li>шаг один</li><li>шаг два</li></ul><script>steal()</script><p>Ключ: sk-ant-api03-" + "k" * 40 + "</p>"
    "</body></html>"
)


@pytest.fixture
def site(monkeypatch):
    """A small web site on this computer - which the tool refuses, so the test lets this one host through."""

    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/old":
                self.send_response(302)
                self.send_header("Location", "/doc")
                self.end_headers()
                return
            body = (PAGE if self.path == "/doc" else "x" * 30_000).encode("utf-8")
            self.send_response(200 if self.path in ("/doc", "/long") else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/doc" else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real = web._is_local
    monkeypatch.setattr(web, "_is_local", lambda host: False if host == "127.0.0.1" else real(host))
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_page_becomes_text(site):
    text = asyncio.run(web.fetch({"url": f"{site}/old"}))  # a redirect is followed
    assert text.startswith(f"{site}/doc - Справка — demo (characters 0-")
    assert "# Установка" in text and f"demo ({site}/pkg)" in text and "- шаг один\n- шаг два" in text
    assert "steal" not in text and "color:red" not in text and "[REDACTED]" in text  # scripts out, secrets masked
    first = asyncio.run(web.fetch({"url": f"{site}/long"}))
    assert first.endswith(f"[18000 more characters: read on with start_char={web.PART_CHARS}]")
    second = asyncio.run(web.fetch({"url": f"{site}/long", "start_char": web.PART_CHARS}))
    assert f"(characters {web.PART_CHARS}-{2 * web.PART_CHARS} of 30000)" in second
    with pytest.raises(ToolError, match="answered 404"):
        asyncio.run(web.fetch({"url": f"{site}/missing"}))


@pytest.mark.parametrize(
    ("url", "error"),
    [
        ("ftp://example.com/x", "only http and https"),
        ("file:///C:/Windows/win.ini", "only http and https"),
        ("http://127.0.0.1:8765/v1/info", "this computer or its local network"),
        ("http://localhost:8080/", "this computer or its local network"),
        ("http://192.168.0.10/", "this computer or its local network"),
    ],
)
def test_what_the_web_tool_refuses(url, error):
    with pytest.raises(ToolError, match=error):
        asyncio.run(web.fetch({"url": url}))


def fetch_call(url):
    return {"id": "f1", "name": "web_fetch", "input": {"url": url}}


def test_reading_the_web_is_asked_and_taints_the_chat(hub, repo, site):  # noqa: F811
    steps = [[fetch_call(f"{site}/doc")], [{"id": "w1", "name": "edit_file", "input": edit()}]]
    _pid, conv, _provider = setup_chat(hub, repo, steps, mode="trusted")
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "web_fetch" and asked["kind"] == "network" and asked["preview"]["text"] == f"{site}/doc"
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True})
    # the page is read; then the edit - in a trusted chat - is asked all the same: the chat has read the web (D19)
    write = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == "edit_file"])[0]
    hub.post(f"/v1/approvals/{write['id']}", json={"allow": True})
    messages = finished(hub, conv)
    results = [b for m in messages if m["role"] == "tool" for b in m["content"]]
    assert "# Установка" in results[0]["output"] and results[1]["error"] is False


def test_listed_sites_need_no_question(hub, repo, site, monkeypatch):  # noqa: F811
    monkeypatch.setattr(web, "ALLOWED_SITES", frozenset({"127.0.0.1"}))
    _pid, conv, _provider = setup_chat(hub, repo, [[fetch_call(f"{site}/doc")]])
    say(hub, conv)
    result = result_of(finished(hub, conv))
    assert result["error"] is False and "# Установка" in result["output"]
    assert hub.get("/v1/approvals").json()["approvals"] == []
