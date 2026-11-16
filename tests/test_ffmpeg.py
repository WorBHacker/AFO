"""ffmpeg fetched by the hub for a video's MP4 (29.8в, hub/ffmpeg.py): from a stand-in GitHub, the pinned Windows
build picked, its sha256 checked, ffmpeg.exe with its libraries and licence put in place; a WebP turned into MP4."""

import asyncio
import hashlib
import io
import json
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    assert args[args.index("-g") + 1] == "1"  # each frame a key frame (48.12.8): the player goes to it alone
    assert "+faststart" in args and args[args.index("-f") + 1] == "mp4"
    with pytest.raises(EngineError, match="Invalid data"):
        ffmpeg.to_mp4(tmp_path / "ffmpeg.exe", tmp_path / "fail.webp", tmp_path / "fail.mp4")
    assert not (tmp_path / "fail.mp4").exists()


BLUE, ORANGE, GREEN = (20, 40, 120), (230, 140, 30), (40, 200, 60)


def animated_webp(frames: list[tuple[object, int, int, bool]], width: int, height: int) -> bytes:
    """An animated WebP as the engine's are (libwebp's: sd-server): frames smaller than the canvas, with see-through
    parts (ALPH), laid over the one before - each (QImage, x, y, laid over), a tenth of a second; their pictures are
    what Qt's WebP writes (lossy: VP8 and ALPH)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    def chunk(kind: bytes, body: bytes) -> bytes:
        return kind + len(body).to_bytes(4, "little") + body + b"\0" * (len(body) & 1)

    def picture(image) -> bytes:  # its chunks but VP8X
        array = QByteArray()
        buffer = QBuffer(array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "WEBP", 90)
        data, at, out = bytes(array.data()), 12, b""
        while at < len(data):
            size = int.from_bytes(data[at + 4 : at + 8], "little")
            if data[at : at + 4] != b"VP8X":
                out += data[at : at + 8 + size + (size & 1)]
            at += 8 + size + (size & 1)
        return out

    def u24(value: int) -> bytes:
        return value.to_bytes(3, "little")

    body = b"WEBP" + chunk(b"VP8X", bytes([0x12, 0, 0, 0]) + u24(width - 1) + u24(height - 1))  # alpha, animation
    body += chunk(b"ANIM", bytes(4) + bytes(2))  # its background, round for ever
    for image, x, y, over in frames:
        flags = 0 if over else 0x02  # «do not blend» for the first; none is disposed of
        body += chunk(b"ANMF", u24(x // 2) + u24(y // 2) + u24(image.width() - 1) + u24(image.height() - 1)
                      + u24(100) + bytes([flags]) + picture(image))  # fmt: skip
    return b"RIFF" + len(body).to_bytes(4, "little") + body


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_videos_mp4_has_each_frame_as_the_webp_lays_it(tmp_path):
    """48.12.7 (the live check: the player showed the clip's first scene, then white): the MP4 the hub makes from the
    engine's WebP - by a real ffmpeg, the hub's own arguments - has each frame as the WebP lays it: a frame smaller
    than the canvas, see-through around, over the one before. The clip's middle frame is not of one colour and is not
    the first: the first frame's halves are still there and every square laid since. Each frame a key frame
    (48.12.8)."""
    from PySide6.QtGui import QColor, QImage

    first = QImage(64, 64, QImage.Format.Format_RGB32)
    first.fill(QColor(*BLUE))
    for x in range(32, 64):
        for y in range(64):
            first.setPixelColor(x, y, QColor(*ORANGE))
    frames = [(first, 0, 0, False)]
    for k in range(1, 9):  # a square moving right, each over what was
        tile = QImage(16, 16, QImage.Format.Format_ARGB32)
        tile.fill(QColor(0, 0, 0, 0))
        for x in range(4, 12):
            for y in range(4, 12):
                tile.setPixelColor(x, y, QColor(*GREEN))
        frames.append((tile, 2 + 5 * k, 24, True))  # within the canvas: the last at 42-58
    source, target = tmp_path / "v.webp", tmp_path / "v.mp4"
    source.write_bytes(animated_webp(frames, 64, 64))
    program = Path(shutil.which("ffmpeg"))
    ffmpeg.to_mp4(program, source, target)

    def frame(n: int) -> QImage:
        png = tmp_path / f"{n}.png"
        subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-i", str(target), "-vf",
                        f"select=eq(n\\,{n})", "-fps_mode", "passthrough", "-frames:v", "1", str(png)],
                       check=True, timeout=60)  # fmt: skip
        return QImage(str(png))

    def near(image, x: int, y: int, colour: tuple[int, int, int]) -> bool:
        pixel = image.pixelColor(x, y)
        return sum(abs(a - b) for a, b in zip((pixel.red(), pixel.green(), pixel.blue()), colour, strict=True)) < 60

    start, middle = frame(0), frame(4)
    assert (middle.width(), middle.height()) == (64, 64)
    assert near(start, 16, 4, BLUE) and near(start, 48, 60, ORANGE) and near(start, 16, 32, BLUE)
    # the first frame's halves around the squares, not the background a frame alone would have
    assert near(middle, 4, 4, BLUE) and near(middle, 60, 60, ORANGE) and near(middle, 40, 8, ORANGE)
    # the squares of frames 1 to 4 (each 8 wide at 11, 16, 21, 26): all of them, not only the last
    assert all(near(middle, x, 32, GREEN) for x in (13, 18, 23, 32)) and not near(middle, 44, 32, GREEN)
    assert not near(start, 16, 32, GREEN)  # not the first
    last = frame(8)
    assert near(last, 50, 32, GREEN) and near(last, 13, 32, GREEN)  # the square at 46, the first one kept
    # (48.12.8) each frame a key frame: the window's player goes to a frame by decoding it alone
    keys = tmp_path / "keys"
    keys.mkdir()
    subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey", "-i", str(target),
                    "-fps_mode", "passthrough", str(keys / "%d.png")], check=True, timeout=60)  # fmt: skip
    assert len(list(keys.iterdir())) == len(frames) == 9
