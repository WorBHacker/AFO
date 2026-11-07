"""ffmpeg fetched by the hub for a video's MP4 (29.8в, hub/ffmpeg.py): from a stand-in GitHub, the pinned Windows
build picked, its sha256 checked, ffmpeg.exe with its libraries and licence put in place; a WebP turned into MP4."""

import asyncio
import hashlib
import io
import json
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from hub import engines, ffmpeg
from hub.engines import EngineError
from hub.hf import HubError

TOP = ffmpeg.ASSET.removesuffix(".zip")


def build():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in ("bin/ffmpeg.exe", "bin/avcodec-63.dll", "bin/avformat-63.dll", "bin/ffplay.exe", "LICENSE.txt",
                     "doc/ffmpeg.html", "include/libavcodec/avcodec.h", "lib/avcodec.lib"):  # fmt: skip
            zf.writestr(f"{TOP}/{name}", b"MZ" + name.encode())
    return data.getvalue()


ARCHIVE = build()


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0
    digest = "github"  # "github": GitHub's own digest; "none"; "wrong"
    missing = False  # the pinned build not in the release

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
        if self.path == f"/repos/{ffmpeg.REPO}/releases/tags/{ffmpeg.TAG}":
            asset = {"name": ffmpeg.ASSET, "size": len(ARCHIVE), "browser_download_url": url + ffmpeg.ASSET}
            if self.digest != "none":
                sha = hashlib.sha256(ARCHIVE).hexdigest() if self.digest == "github" else "0" * 64
                asset["digest"] = "sha256:" + sha
            others = [{"name": "ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip", "size": 1, "browser_download_url": url},
                      {"name": "ffmpeg-n9.0-latest-linux64-gpl-shared-9.0.tar.xz", "size": 1,
                       "browser_download_url": url}]  # fmt: skip
            self._send({"tag_name": "latest", "assets": others if self.missing else [*others, asset]})
        elif self.path == f"/download/{ffmpeg.ASSET}":
            self._send(ARCHIVE, "application/zip")
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
    FakeGitHub.digest, FakeGitHub.missing = "github", False
    server.shutdown()


def latest():
    async def run():
        async with httpx.AsyncClient() as http:
            return await ffmpeg.latest(http)

    return asyncio.run(run())


def test_ffmpeg_fetched_and_put_in_place(github, tmp_path):
    found = latest()
    assert found["version"] == "9.0" and found["asset"]["sha256"] == hashlib.sha256(ARCHIVE).hexdigest()
    assert ffmpeg.find(tmp_path / "tools") is None
    program = ffmpeg.install(found, tmp_path / "tools", threading.Event())
    folder = tmp_path / "tools" / "ffmpeg"
    assert program == folder / "ffmpeg.exe" == ffmpeg.find(tmp_path / "tools")
    # the program, its libraries and its licence: not ffplay, the docs, the headers
    assert sorted(p.name for p in folder.iterdir()) == ["LICENSE.txt", "avcodec-63.dll", "avformat-63.dll",
                                                        "ffmpeg.exe"]  # fmt: skip
    assert not list((tmp_path / "tools" / "downloads").iterdir())


@pytest.mark.parametrize(("digest", "missing", "said"), [("none", False, "sha256"), ("github", True, "нет сборки")])
def test_nothing_unchecked_or_missing_is_put_in(github, tmp_path, digest, missing, said):
    github.digest, github.missing = digest, missing
    with pytest.raises(EngineError, match=said):
        latest()


def test_a_wrong_archive_is_refused(github, tmp_path):
    github.digest = "wrong"
    with pytest.raises(HubError, match="sha256"):
        ffmpeg.install(latest(), tmp_path / "tools", threading.Event())
    assert ffmpeg.find(tmp_path / "tools") is None


def test_a_webp_into_mp4(tmp_path, monkeypatch):
    """H.264 in yuv420p (any player, any messenger), the index at the front (it plays as it loads); written beside
    under another name, then put in place - a broken run leaves nothing."""
    ran = []

    def run(args, **kw):
        ran.append(args)
        if "fail" in str(args[args.index("-i") + 1]):
            return subprocess.CompletedProcess(args, 1, b"", b"Invalid data found when processing input\n")
        (tmp_path / args[-1]).write_bytes(b"MP4")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(ffmpeg.subprocess, "run", run)
    source, target = tmp_path / "v.webp", tmp_path / "v.mp4"
    source.write_bytes(b"RIFF")
    ffmpeg.to_mp4(tmp_path / "ffmpeg.exe", source, target)
    assert target.read_bytes() == b"MP4" and not (tmp_path / "v.mp4.part").exists()
    args = ran[0]
    assert args[0] == str(tmp_path / "ffmpeg.exe") and args[args.index("-i") + 1] == str(source)
    assert args[args.index("-c:v") + 1] == "libx264" and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert "+faststart" in args and args[args.index("-f") + 1] == "mp4"
    with pytest.raises(EngineError, match="Invalid data"):
        ffmpeg.to_mp4(tmp_path / "ffmpeg.exe", tmp_path / "fail.webp", tmp_path / "fail.mp4")
    assert not (tmp_path / "fail.mp4").exists()
