"""ripgrep fetched by the hub when there is none (2026-10-28, hub/ripgrep.py): from a stand-in GitHub, the Windows build
picked, its sha256 checked (GitHub's digest or the release's .sha256 file), rg.exe and its licences put in place."""

import asyncio
import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hub import engines, ripgrep, search

TOP = "ripgrep-14.1.1-x86_64-pc-windows-msvc"


def build():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in ("rg.exe", "COPYING", "LICENSE-MIT", "UNLICENSE", "README.md", "doc/rg.1"):
            zf.writestr(f"{TOP}/{name}", b"MZ" + name.encode())
    return data.getvalue()


ARCHIVE = build()


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0
    digest = "github"  # "github": GitHub's own digest; "file": only the .sha256 asset; "none"; "wrong"

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = f"http://127.0.0.1:{self.port}/download/"
        sha = hashlib.sha256(ARCHIVE).hexdigest()
        if self.path == "/repos/BurntSushi/ripgrep/releases/latest":
            asset = {"name": f"{TOP}.zip", "size": len(ARCHIVE), "browser_download_url": url + f"{TOP}.zip"}
            if self.digest in ("github", "wrong"):
                asset["digest"] = "sha256:" + (sha if self.digest == "github" else "0" * 64)
            assets = [{"name": "ripgrep-14.1.1-aarch64-pc-windows-msvc.zip", "size": 1, "browser_download_url": url},
                      {"name": "ripgrep-14.1.1-x86_64-pc-windows-gnu.zip", "size": 1, "browser_download_url": url},
                      asset]  # fmt: skip
            if self.digest == "file":
                assets.append({"name": f"{TOP}.zip.sha256", "size": 99, "browser_download_url": url + "sums"})
            self._send({"tag_name": "14.1.1", "assets": assets})
        elif self.path == f"/download/{TOP}.zip":
            self._send(ARCHIVE, "application/zip")
        elif self.path == "/download/sums":
            self._send(f"{sha}  {TOP}.zip\n".encode(), "text/plain")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def github(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    FakeGitHub.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    yield FakeGitHub
    FakeGitHub.digest = "github"
    server.shutdown()


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
        self.events.append((event_type, payload))


@pytest.mark.parametrize("digest", ["github", "file"])
def test_ripgrep_fetched_and_put_in_place(github, tmp_path, digest):
    github.digest = digest
    bus = Bus()
    fetcher = ripgrep.Fetcher(bus, enabled=True, into=tmp_path / "tools")
    assert asyncio.run(fetcher.fetch()) is True and fetcher.error == ""
    folder = tmp_path / "tools" / "ripgrep"
    assert sorted(p.name for p in folder.iterdir()) == ["COPYING", "LICENSE-MIT", "UNLICENSE", "rg.exe"]
    assert (folder / "rg.exe").read_bytes() == b"MZrg.exe"
    assert not list((tmp_path / "tools" / "downloads").iterdir())  # the archive and the unpacked rest are gone
    assert bus.events == [("tools.fetched", {"tool": "ripgrep", "version": "14.1.1", "path": str(folder / "rg.exe")})]


@pytest.mark.parametrize(("digest", "said"), [("none", "нет sha256"), ("wrong", "sha256")])
def test_nothing_unchecked_is_put_in(github, tmp_path, digest, said):
    github.digest = digest
    bus = Bus()
    fetcher = ripgrep.Fetcher(bus, enabled=True, into=tmp_path / "tools")
    assert asyncio.run(fetcher.fetch()) is False and said in fetcher.error
    assert not (tmp_path / "tools" / "ripgrep").exists() and bus.events == []


def test_fetched_only_when_there_is_none(github, tmp_path, monkeypatch):
    async def started(enabled, found):
        monkeypatch.setattr(search, "ripgrep", lambda: found)
        fetcher = ripgrep.Fetcher(Bus(), enabled=enabled, into=tmp_path / "tools")
        fetcher.start()
        running = fetcher._task is not None
        if running:
            await asyncio.wait_for(fetcher._task, 10)  # fetched once: the loop ends
        await fetcher.stop()
        return running

    assert asyncio.run(started(True, None)) is True
    assert (tmp_path / "tools" / "ripgrep" / "rg.exe").is_file()
    assert asyncio.run(started(True, "C:/tools/rg.exe")) is False  # one is there already
    assert asyncio.run(started(False, None)) is False  # tests and a hub told not to


def test_the_feed_says_so():
    from app.ui.feed_rows import describe

    payload = {"tool": "ripgrep", "version": "14.1.1", "path": "C:/LocalChat/tools/ripgrep/rg.exe"}
    row = describe({"type": "tools.fetched", "payload": payload}, projects={}, chats={})
    assert (row["title"], row["detail"]) == ("Поставлен ripgrep 14.1.1 — поиск по проектам теперь быстрый",
                                             payload["path"])  # fmt: skip
