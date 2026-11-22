"""AFO's own Strata (stage 45.2а, hub/strata.py): the pinned release from a stand-in GitHub, each file's sha256
checked, the control program unpacked, the engine Strata names for this computer fetched and put in by Strata - a
stand-in Strata, a real process with a Python of its own, as the real one is. Every folder it is given is AFO's; the
person's own Strata on PATH keeps its strata.ps1 (45.0: a newer Strata renames it - the stand-in does the same)."""

import asyncio
import hashlib
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from hub import engines, strata

ENGINE = "strata-engine-0.1.64-windows-x86_64-cuda-cuda13.tar.gz"
ENGINE_BYTES = b"engine " * 1000

# the stand-in strata.py: what AFO asks of the real one (update --check / update), the heal of an older Strata's
# strata.ps1 on PATH (strata_cli/launcher_heal.py), and a record of the environment it was run in
STAND_IN = r"""
import hashlib, json, os, sys
from pathlib import Path

args = sys.argv[1:]
for folder in os.environ.get("PATH", "").split(os.pathsep):
    ps1 = Path(folder) / "strata.ps1"
    if folder and ps1.is_file() and (Path(folder) / "strata.cmd").is_file():
        ps1.replace(ps1.with_name("strata-direct.ps1"))
record = Path(os.environ["STRATA_DATA_DIR"]) / "seen.json"
runs = json.loads(record.read_text()) if record.exists() else []
names = ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "STRATA_DATA_DIR", "STRATA_COMPONENTS_DIR", "STRATA_SETTINGS_FILE",
         "PYTHONPATH", "VIRTUAL_ENV", "PATH")
runs.append({"args": args, "env": {k: os.environ.get(k, "") for k in names}})
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(runs))
feed = Path(args[args.index("--feed") + 1])
offered = json.loads((feed / "strata-feed.json").read_text())["engine"]
components = Path(os.environ["STRATA_COMPONENTS_DIR"]) / "engine"
current = components.is_dir() and any(components.iterdir())
print("  Reading the signed update feed")
if "--check" in args:
    print(json.dumps({"ok": True, "engine": {"status": "current" if current else "install", "offered": offered,
                                             "problem": None}}))
else:
    archive = feed / offered["url"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != offered["sha256"]:
        print(json.dumps({"ok": False, "error": "the package does not match the feed"}))
        sys.exit(3)
    folder = components / f"{offered['version']}-{offered['variant']}"
    folder.mkdir(parents=True)
    (folder / "strata.exe").write_bytes(archive.read_bytes()[:16])
    print(json.dumps({"ok": True, "results": [{"component": "engine", "status": "updated",
                                                "installed_after": {"version": offered["version"]}}]}))
"""


def control_zip() -> bytes:
    """The control package as the release has it - rooted at the bundle - with a Python of its own: a copy of this
    test's venv launcher, its pyvenv.cfg beside the folder (the real one carries Python 3.12)."""
    venv = Path(sys.prefix)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("strata.py", STAND_IN)
        zf.writestr("bin/strata.cmd", "@echo off\r\n")
        zf.writestr("BUNDLE.json", json.dumps({"component": "strata-control", "version": strata.CONTROL_VERSION}))
        zf.write(Path(sys.executable), "python/python.exe")
        zf.write(venv / "pyvenv.cfg", "pyvenv.cfg")
    return out.getvalue()


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0
    files: dict[str, bytes] = {}  # noqa: RUF012 - the release's files, set by the fixture
    digests: dict[str, str] = {}  # noqa: RUF012 - a name -> the sha256 GitHub gives ("" - none)

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
        if self.path == f"/repos/{strata.REPO}/releases/tags/{strata.TAG}":
            assets = []
            for name, data in self.files.items():
                asset = {"name": name, "size": len(data), "browser_download_url": url + name}
                sha = self.digests.get(name, hashlib.sha256(data).hexdigest())
                if sha:
                    asset["digest"] = "sha256:" + sha
                assets.append(asset)
            self._send({"tag_name": strata.TAG, "assets": assets})
        elif self.path.startswith("/download/") and self.path[10:] in self.files:
            self._send(self.files[self.path[10:]], "application/octet-stream")
        else:
            self.send_response(404)
            self.end_headers()


def release_files(feed_sha: str | None = None) -> dict[str, bytes]:
    feed = {"engine": {"url": ENGINE, "sha256": feed_sha or hashlib.sha256(ENGINE_BYTES).hexdigest(),
                       "version": "0.1.64", "variant": "cuda13"}}  # fmt: skip
    return {strata.CONTROL: control_zip(), "strata-feed.json": json.dumps(feed).encode(),
            "strata-feed.json.sig": b"signed", ENGINE: ENGINE_BYTES,
            "strata-engine-0.1.64-windows-x86_64-cuda-cuda12.tar.gz": b"another card's"}  # fmt: skip


@pytest.fixture
def github(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    FakeGitHub.port = server.server_port
    FakeGitHub.files, FakeGitHub.digests = release_files(), {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    yield FakeGitHub
    server.shutdown()


@pytest.fixture
def persons_strata(tmp_path, monkeypatch):
    """The person's own Strata on PATH, as the user's computer has it (0.1.51: strata.cmd and strata.ps1)."""
    folder = tmp_path / "persons" / "Strata" / "terminal" / "current" / "bin"
    folder.mkdir(parents=True)
    (folder / "strata.cmd").write_text("@echo off\r\n")
    (folder / "strata.ps1").write_text("# Strata Terminal launcher for PowerShell\n")
    monkeypatch.setenv("PATH", os.pathsep.join([str(folder), os.environ["PATH"]]))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hubs-own"))  # the hub's: not Strata's
    return folder


def assets() -> dict:
    async def run():
        async with httpx.AsyncClient() as http:
            return await strata.release(http)

    return asyncio.run(run())


def test_strata_is_put_in_with_folders_of_its_own(github, persons_strata, tmp_path):
    base = tmp_path / "engines" / "strata"
    reported = []
    ready = strata.install(assets(), base, report=lambda n, total: reported.append((n, total)),
                           cancel=threading.Event())  # fmt: skip
    assert ready == {"tag": strata.TAG, "control": strata.CONTROL_VERSION, "engine": "0.1.64-cuda13"}
    assert strata.installed(base) == ready
    assert (strata.data(base) / "components" / "engine" / "0.1.64-cuda13" / "strata.exe").is_file()
    assert not (base / "downloads").exists()  # the archives go once it is in
    # the engine the feed names for this computer was fetched - not the other card's
    assert reported[-1][0] == reported[-1][1] == sum(len(github.files[n]) for n in (strata.CONTROL, *strata.FEED,
                                                                                     ENGINE))  # fmt: skip
    runs = json.loads((strata.data(base) / "seen.json").read_text())
    assert [r["args"][:2] for r in runs] == [["update", "--check"], ["update", "--no-control"]]
    for env in (r["env"] for r in runs):
        assert all(Path(env[k]).is_relative_to(base) for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE",
                                                                "STRATA_DATA_DIR", "STRATA_COMPONENTS_DIR",
                                                                "STRATA_SETTINGS_FILE"))  # fmt: skip
        assert str(persons_strata) not in env["PATH"] and not env["PYTHONPATH"] and not env["VIRTUAL_ENV"]
    assert (persons_strata / "strata.ps1").is_file() and not (persons_strata / "strata-direct.ps1").exists()
    # put in again: the engine is current - nothing of it fetched again
    again = strata.install(assets(), base, report=lambda n, total: None, cancel=threading.Event())
    assert again == ready and len(json.loads((strata.data(base) / "seen.json").read_text())) == 3


def test_an_engine_not_of_the_release_or_unchecked_is_not_put_in(github, tmp_path):
    github.files = release_files(feed_sha="0" * 64)  # the feed names another file than the release's
    base = tmp_path / "strata"
    with pytest.raises(strata.StrataError, match="не совпадает"):
        strata.install(assets(), base, report=lambda n, total: None, cancel=threading.Event())
    assert strata.installed(base) is None
    github.files, github.digests = release_files(), {strata.CONTROL: ""}
    with pytest.raises(strata.StrataError, match="контрольную сумму"):
        assets()
