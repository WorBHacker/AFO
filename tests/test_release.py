"""AFO's own updates (step 10.4d): the newest release on GitHub (a tag vX.Y.Z with AFO-Setup-X.Y.Z.exe and its
.sha256), the installer downloaded and kept only whole and with its sha256, and run silently over the installed
program at the person's word - never over a checkout of the code. GitHub is a stand-in on this computer here."""

import asyncio
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from common import __version__
from hub import engines, release
from hub.auth import create_client
from installer import build
from tests.test_tools import hub  # noqa: F401 - the hub fixture

SETUP = b"MZ" + b"afo" * 4000
NAME = "AFO-Setup-0.2.0.exe"


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0
    release: dict | None = None
    files: ClassVar[dict[str, bytes]] = {}

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
        if self.path == f"/repos/{release.REPO}/releases/latest" and self.release is not None:
            self._send(self.release)
        elif self.path.startswith("/download/") and self.path.removeprefix("/download/") in self.files:
            self._send(self.files[self.path.removeprefix("/download/")], "application/octet-stream")
        else:
            self.send_response(404)
            self.end_headers()


def published(tag="v0.2.0", setup=SETUP, *, digest=True, sums=True, name=NAME):
    """A release as GitHub's API gives it, its files served by the stand-in."""
    url = f"http://127.0.0.1:{FakeGitHub.port}/download/"
    sha = hashlib.sha256(setup).hexdigest()
    FakeGitHub.files = {name: setup, f"{name}.sha256": f"{sha}  {name}\n".encode()}
    assets = [{"name": name, "size": len(setup), "browser_download_url": url + name,
               "digest": f"sha256:{sha}" if digest else None}]  # fmt: skip
    if sums:
        assets.append({"name": f"{name}.sha256", "size": 86, "browser_download_url": url + f"{name}.sha256",
                       "digest": None})  # fmt: skip
    FakeGitHub.release = {
        "tag_name": tag,
        "html_url": f"https://github.com/{release.REPO}/releases/tag/{tag}",
        "body": "Что нового: всё.",
        "published_at": "2026-11-05T10:00:00Z",
        "assets": assets,
    }
    return sha


@pytest.fixture
def github(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    FakeGitHub.port = server.server_port
    FakeGitHub.release = None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    yield server
    server.shutdown()


async def newest():
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as http:
        return await release.latest(http)


def test_versions_compare_as_numbers():
    assert release.version("v0.10.0") == (0, 10, 0) and release.version("0.1.0") == (0, 1, 0)
    assert release.version("b5000") is None and release.version("v1.2") is None
    assert release.newer("0.10.0", "0.9.1") and release.newer("1.0.0", "0.99.99")
    assert not release.newer("0.1.0", "0.1.0") and not release.newer("0.0.9", "0.1.0")
    assert not release.newer("nightly", "0.1.0")


def test_the_newest_release_and_its_installer(github):
    sha = published()
    found = asyncio.run(newest())
    assert (found["version"], found["sha256"], found["size"], found["name"]) == ("0.2.0", sha, len(SETUP), NAME)
    assert found["url"].endswith("/releases/tag/v0.2.0") and found["notes"] == "Что нового: всё."
    published(digest=False)  # the .sha256 beside it is enough
    assert asyncio.run(newest())["sha256"] == sha
    published(digest=False, sums=False)  # no checksum at all: a changed file would be run
    with pytest.raises(release.ReleaseError, match="контрольн"):
        asyncio.run(newest())
    published()
    FakeGitHub.files[f"{NAME}.sha256"] = b"0" * 64 + b"  " + NAME.encode()  # the two disagree
    with pytest.raises(release.ReleaseError, match="не совпада"):
        asyncio.run(newest())
    published(name="AFO-Setup-0.3.0.exe")  # the tag says 0.2.0, the installer another version
    with pytest.raises(release.ReleaseError, match="установщик"):
        asyncio.run(newest())
    published(tag="nightly")
    assert asyncio.run(newest()) is None  # not a release of this kind
    published()
    FakeGitHub.release["assets"][0]["browser_download_url"] = "http://example.org/AFO-Setup-0.2.0.exe"
    with pytest.raises(release.ReleaseError, match="адрес"):
        asyncio.run(newest())  # a download from anywhere but GitHub (or this computer, in tests) is refused
    FakeGitHub.release = None
    assert asyncio.run(newest()) is None  # no release yet


def wait_for(hub, status, timeout=15.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while True:
        found = hub.get("/v1/release").json()
        if found["job"]["status"] == status:
            return found
        assert time.monotonic() < deadline, found
        time.sleep(0.05)


def test_the_hub_checks_and_downloads_only_a_whole_file(hub, github):  # noqa: F811
    published()
    first = hub.get("/v1/release").json()
    assert first["current"] == __version__ and first["auto"] is True and first["latest"] == "" and not first["newer"]
    assert first["installed"] is False  # this checkout: the installer is not for it
    found = hub.post("/v1/release/check").json()
    assert (found["latest"], found["newer"], found["size"], found["error"]) == ("0.2.0", True, len(SETUP), "")
    assert hub.post("/v1/release/download").json()["job"]["status"] == "downloading"
    ready = wait_for(hub, "ready")
    kept = Path(ready["job"]["file"])
    assert kept.name == NAME and kept.read_bytes() == SETUP and kept.parent.name == "updates"
    # a file that is not the published one is thrown away
    kept.unlink()
    FakeGitHub.files[NAME] = SETUP[:-1] + b"!"
    hub.post("/v1/release/download")
    failed = wait_for(hub, "failed")
    assert "sha256" in failed["job"]["error"] and not any(kept.parent.iterdir())
    # the person may turn the checks off; other clients may not touch any of it
    assert hub.put("/v1/release", json={"auto": False}).json()["auto"] is False
    assert hub.get("/v1/release").json()["auto"] is False
    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.post("/v1/release/check", headers={"Authorization": f"Bearer {other}"}).status_code == 403


def test_the_installer_runs_over_the_installed_program_only(hub, github, monkeypatch, tmp_path):  # noqa: F811
    published()
    hub.post("/v1/release/check")
    hub.post("/v1/release/download")
    ready = wait_for(hub, "ready")
    refused = hub.post("/v1/release/install")
    assert refused.status_code == 409 and "git" in refused.json()["detail"]  # a checkout updates with git
    program = tmp_path / "Programs" / "AFO"
    started = []

    class Started:
        def __init__(self, command, **options):
            started.append((command, options))

    monkeypatch.setattr(release, "program_folder", lambda: program)
    monkeypatch.setattr(release.subprocess, "Popen", Started)
    assert hub.get("/v1/release").json()["installed"] is True
    found = hub.post("/v1/release/install").json()
    assert found["job"]["status"] == "installing"
    ((command, options),) = started
    setup = Path(ready["job"]["file"])
    assert command[0] == str(setup) and {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1",
                                        "/LANG=russian"} <= set(command)  # fmt: skip
    log = Path(next(c for c in command if c.startswith("/LOG=")).removeprefix("/LOG="))
    assert log.parent == setup.parent and options.get("creationflags")  # on its own: the hub is stopped by it
    pending = json.loads((setup.parent / release.PENDING).read_text(encoding="utf-8"))
    assert pending == {"from": __version__, "to": "0.2.0", "log": str(log)}


def test_after_an_update_the_hub_says_how_it_went(tmp_path):
    """The installer stops the hub; the hub that starts after it reads the note left before and tells the window."""
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / "AFO-Setup-0.0.9.exe").write_bytes(b"old")
    (folder / release.PENDING).write_text(json.dumps({"from": "0.0.9", "to": __version__, "log": ""}), "utf-8")
    told = []

    class Bus:
        async def publish(self, kind, payload, persist=True):
            told.append((kind, payload))

    keeper = release.Releases(None, Bus(), folder)
    asyncio.run(keeper.after_update())
    assert keeper.job == {"status": "updated", "version": __version__, "from": "0.0.9"}
    assert [k for k, _ in told] == ["release.updated"] and not any(folder.iterdir())  # the old installer goes too
    log = folder / "install.log"
    log.write_text("…\n2026-11-05 Error: the file is in use: hub.dll\n", "utf-8")
    (folder / release.PENDING).write_text(json.dumps({"from": __version__, "to": "9.9.9", "log": str(log)}), "utf-8")
    keeper = release.Releases(None, Bus(), folder)
    asyncio.run(keeper.after_update())
    assert keeper.job["status"] == "failed" and "9.9.9" in keeper.job["error"] and "in use" in keeper.job["error"]
    assert not (folder / release.PENDING).exists()


def test_a_version_newer_than_the_update_just_made_can_be_taken(hub, github):  # noqa: F811
    """Live on 2026-11-05: AFO updated itself to 0.1.2, and 0.1.3 came out the same day - the hub found it, but its
    «updated» kept the window from offering it until the hub restarted. A version newer than the one just put in
    makes that update the past; one that is not newer leaves its word."""
    keeper = hub.app.state.releases
    keeper.job = {"status": "updated", "version": __version__, "from": "0.0.9"}
    published(tag=f"v{__version__}", name=f"AFO-Setup-{__version__}.exe")  # the version just put in: nothing newer
    found = hub.post("/v1/release/check").json()
    assert not found["newer"] and found["job"]["status"] == "updated"
    published()
    found = hub.post("/v1/release/check").json()
    assert found["newer"] and found["job"] == {"status": "idle"}
    assert hub.post("/v1/release/download").json()["job"]["status"] == "downloading"
    assert wait_for(hub, "ready")["job"]["version"] == "0.2.0"


def test_the_wizard_opens_the_window_again_after_a_silent_update():
    """A silent install shows nothing and starts nothing - except an update AFO started (`/UPDATE=1`): then the
    window opens again on the new version (and starts the hub)."""
    iss = build.ISS.read_text(encoding="utf-8")
    assert "ExpandConstant('{param:update|0}') = '1'" in iss
    assert 'Parameters: "-m app"; WorkingDir: "{app}"; Flags: nowait; Check: IsUpdate' in iss
