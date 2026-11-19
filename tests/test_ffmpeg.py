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


def _solid(colour: tuple[int, int, int]):
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(*colour))
    return (image, 0, 0, False)


def _colour(path: Path) -> tuple[int, int, int]:
    from PySide6.QtGui import QImage

    image = QImage(str(path))
    assert (image.width(), image.height()) == (64, 64), path
    pixel = image.pixelColor(32, 32)
    return pixel.red(), pixel.green(), pixel.blue()


def _near(colour: tuple[int, int, int], other: tuple[int, int, int]) -> bool:
    return sum(abs(a - b) for a, b in zip(colour, other, strict=True)) < 60


def _frames_of(program: Path, video: Path, into: Path) -> list[Path]:
    into.mkdir()
    subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-i", str(video), "-fps_mode", "passthrough",
                    str(into / "%d.png")], check=True, timeout=60)  # fmt: skip
    return sorted(into.iterdir(), key=lambda f: int(f.stem))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_clips_last_frame_its_chosen_frames_and_scenes_joined(tmp_path):
    """50.3, for the director's film - a real ffmpeg, the hub's own arguments: a clip's last frame as a PNG (the next
    scene goes on from it) - of the engine's WebP and of an MP4; the frames with the numbers asked (for a look at a
    scene); scenes joined into one MP4 - cut to cut, every frame of each kept once, or with a cross-fade, shorter
    by the fades; nothing half-made is left when it fails."""
    program = Path(shutil.which("ffmpeg"))
    blues = [(20, 40, 100 + 15 * k) for k in range(9)]  # a scene of 9 frames: blue, lighter each frame
    oranges = [(230, 110 + 5 * k, 30) for k in range(16)]  # a scene of 17: orange, its last frame green
    one, two = tmp_path / "one.webp", tmp_path / "two.webp"
    one.write_bytes(animated_webp([_solid(c) for c in blues], 64, 64))
    two.write_bytes(animated_webp([*(_solid(c) for c in oranges), _solid(GREEN)], 64, 64))

    last = tmp_path / "last.png"
    ffmpeg.last_frame(program, two, last)
    assert _near(_colour(last), GREEN) and not (tmp_path / "last.png.part").exists()
    looks = tmp_path / "looks"
    looks.mkdir()
    chosen = ffmpeg.frames_at(program, two, looks, [0, 8, 16])
    assert [f.name for f in chosen] == ["1.png", "2.png", "3.png"]
    assert _near(_colour(chosen[0]), oranges[0]) and _near(_colour(chosen[1]), oranges[8])
    assert _near(_colour(chosen[2]), GREEN)

    film = tmp_path / "film.mp4"
    ffmpeg.join(program, [(one, 9 / 24), (two, 17 / 24)], film, fps=24)
    frames = _frames_of(program, film, tmp_path / "cut")
    assert len(frames) == 26  # each frame of each scene, once
    assert _near(_colour(frames[0]), blues[0]) and _near(_colour(frames[8]), blues[8])
    assert _near(_colour(frames[9]), oranges[0]) and _near(_colour(frames[25]), GREEN)
    end = tmp_path / "end.png"
    ffmpeg.last_frame(program, film, end)  # an MP4's too
    assert _near(_colour(end), GREEN)

    faded = tmp_path / "faded.mp4"
    ffmpeg.join(program, [(one, 9 / 24), (two, 17 / 24)], faded, fps=24, fade_s=0.25)
    frames = _frames_of(program, faded, tmp_path / "fade")
    assert len(frames) == 20  # 26 less the fade's 6
    assert _near(_colour(frames[0]), blues[0]) and _near(_colour(frames[19]), GREEN)
    middle = _colour(frames[5])  # inside the fade: neither scene's own colour
    assert not _near(middle, blues[5]) and not _near(middle, oranges[2])

    small = tmp_path / "small"  # 51.2, for a model's look at a scene: the same frames, small JPEGs
    small.mkdir()
    shown = ffmpeg.frames_at(program, two, small, [16, 0, 8], small=32)
    assert [f.name for f in shown] == ["1.jpg", "2.jpg", "3.jpg"] and shown[0].read_bytes()[:2] == b"\xff\xd8"
    sizes, colours = [], []
    for frame in shown:
        from PySide6.QtGui import QImage

        image = QImage(str(frame))
        sizes.append((image.width(), image.height()))
        pixel = image.pixelColor(16, 16)
        colours.append((pixel.red(), pixel.green(), pixel.blue()))
    assert sizes == [(32, 32)] * 3
    assert _near(colours[0], oranges[0]) and _near(colours[1], oranges[8]) and _near(colours[2], GREEN)
    (tmp_path / "few").mkdir()
    with pytest.raises(EngineError, match="меньше кадров"):
        ffmpeg.frames_at(program, one, tmp_path / "few", [0, 40], small=32)

    with pytest.raises(EngineError, match="нечего склеивать"):
        ffmpeg.join(program, [], tmp_path / "none.mp4", fps=24)
    with pytest.raises(EngineError, match="короче перехода"):
        ffmpeg.join(program, [(one, 9 / 24), (two, 17 / 24)], tmp_path / "short.mp4", fps=24, fade_s=0.5)
    with pytest.raises(EngineError, match="не взял последний кадр"):
        ffmpeg.last_frame(program, tmp_path / "missing.webp", tmp_path / "missing.png")
    assert not list(tmp_path.glob("*.part")) and not (tmp_path / "missing.png").exists()


def _clip_with_sound(program: Path, target: Path, frames: int, *, moves: bool = True) -> None:
    """A clip as the engine gives one with sound (52.1 live): VP8 at 24 a second and PCM 48 kHz stereo in
    Matroska named .webm - a tone of 440 Hz, a little shorter than the frames."""
    picture = "testsrc=size=64x64:rate=24" if moves else "color=c=red:size=64x64:rate=24"
    subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", picture, "-f",
                    "lavfi", "-t", f"{(frames - 1) / 24:.4f}", "-i", "sine=frequency=440:sample_rate=48000",
                    "-frames:v", str(frames), "-ac", "2", "-c:v", "libvpx", "-g", "1", "-c:a", "pcm_s16le", "-f",
                    "matroska", str(target)], check=True, timeout=60)  # fmt: skip


def _said(program: Path, *args: str) -> str:
    done = subprocess.run([str(program), "-hide_banner", *args], capture_output=True, timeout=60, check=False)
    return done.stderr.decode("utf-8", "replace")


def _loud(program: Path, video: Path, start: float, length: float) -> float:
    """The mean volume of a piece of a file's sound, dB (silence - about -91)."""
    import re

    said = _said(program, "-ss", f"{start}", "-t", f"{length}", "-i", str(video), "-vn", "-af", "volumedetect", "-f",
                 "null", "-")  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", said).group(1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_clip_with_sound_keeps_it_in_its_mp4_and_in_the_joined_film(tmp_path):
    """52.2 - a real ffmpeg, the hub's own arguments: the engine's clip with sound (WebM: VP8 and PCM) is told from
    a silent one; its MP4 has the sound (AAC) and each frame; its poster is the first frame; a clip of one frame
    repeated is told from one that moves; scenes with sound and without are joined into one film - the silent ones'
    stretch is silence - cut to cut and with a cross-fade, the sound as long as the picture."""
    program = Path(shutil.which("ffmpeg"))
    loud, silent, still = tmp_path / "loud.webm", tmp_path / "silent.webp", tmp_path / "still.webm"
    _clip_with_sound(program, loud, 25)
    _clip_with_sound(program, still, 9, moves=False)
    silent.write_bytes(animated_webp([_solid((20, 40, 100 + 15 * k)) for k in range(9)], 64, 64))

    assert ffmpeg.heard(program, loud) and not ffmpeg.heard(program, silent)
    assert not ffmpeg.heard(program, tmp_path / "missing.webm")
    assert ffmpeg.still(program, still, 9) and not ffmpeg.still(program, loud, 25)

    mp4 = tmp_path / "loud.mp4"
    ffmpeg.to_mp4(program, loud, mp4)
    said = _said(program, "-i", str(mp4))
    assert "Video: h264" in said and "Audio: aac" in said and "48000 Hz, stereo" in said, said
    assert len(_frames_of(program, mp4, tmp_path / "mp4-frames")) == 25
    assert _loud(program, mp4, 0.2, 0.5) > -30
    quiet = tmp_path / "silent.mp4"
    ffmpeg.to_mp4(program, silent, quiet)  # a silent one's MP4: no sound track at all, as before
    assert "Audio:" not in _said(program, "-i", str(quiet))

    poster = tmp_path / "loud.jpg"
    ffmpeg.poster(program, loud, poster)
    assert poster.read_bytes()[:2] == b"\xff\xd8" and not (tmp_path / "loud.jpg.part").exists()
    from PySide6.QtGui import QImage

    assert (QImage(str(poster)).width(), QImage(str(poster)).height()) == (64, 64)

    film = tmp_path / "film.mp4"  # sound, silence, sound - cut to cut
    ffmpeg.join(program, [(loud, 25 / 24), (silent, 9 / 24), (loud, 25 / 24)], film, fps=24)
    said = _said(program, "-i", str(film))
    assert "Audio: aac" in said, said
    assert len(_frames_of(program, film, tmp_path / "film-frames")) == 59
    assert _loud(program, film, 0.2, 0.6) > -30 and _loud(program, film, 1.75, 0.6) > -30
    assert _loud(program, film, 1.12, 0.22) < -60  # the silent scene's stretch
    whole = _said(program, "-i", str(film), "-map", "0:a", "-f", "null", "-")
    import re

    seconds = re.findall(r"time=\d+:\d+:([\d.]+)", whole)[-1]
    assert abs(float(seconds) - 59 / 24) < 0.06, whole  # the sound as long as the picture

    faded = tmp_path / "faded.mp4"
    ffmpeg.join(program, [(loud, 25 / 24), (silent, 9 / 24), (loud, 25 / 24)], faded, fps=24, fade_s=0.125)
    assert len(_frames_of(program, faded, tmp_path / "faded-frames")) == 53  # 59 less two fades of 3
    assert "Audio: aac" in _said(program, "-i", str(faded))
    assert _loud(program, faded, 0.2, 0.5) > -30

    mute = tmp_path / "mute.mp4"  # silent scenes alone: a film without a sound track, as before
    ffmpeg.join(program, [(silent, 9 / 24), (silent, 9 / 24)], mute, fps=24)
    assert "Audio:" not in _said(program, "-i", str(mute))
    assert not list(tmp_path.glob("*.part"))


def _tone(program: Path, target: Path, hz: int, seconds: float, *, rate: int = 48000, channels: int = 2,
          volume: float = 0.3) -> Path:  # fmt: skip
    subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={hz}:sample_rate={rate}:duration={seconds}", "-af", f"volume={volume}", "-ac",
                    str(channels), str(target)], check=True, timeout=60)  # fmt: skip
    return target


def _band(program: Path, video: Path, hz: int, start: float, length: float) -> float:
    """The mean volume of one pitch in a piece of a file's sound, dB."""
    import re

    said = _said(program, "-ss", f"{start}", "-t", f"{length}", "-i", str(video), "-vn", "-af",
                 f"bandpass=f={hz}:width_type=h:w=40,volumedetect", "-f", "null", "-")  # fmt: skip
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", said).group(1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_films_added_sound_is_mixed_the_voice_over_the_rest(tmp_path, monkeypatch):
    """53.5 - a real ffmpeg, the hub's own arguments: the joined film gets one sound track as long as itself - the
    narrator's line at its second, music under the whole film (a short one goes round again, a loud one is brought
    down to its level), a scene's made sound at its place and no longer than asked; while the narrator speaks the
    music and the film's own sound go down; the picture is copied, frame for frame."""
    import re

    program = Path(shutil.which("ffmpeg"))
    silent, loud = tmp_path / "silent.mp4", tmp_path / "loud.mp4"
    picture = ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=6"]
    video = ["-c:v", "libx264", "-g", "1", "-pix_fmt", "yuv420p"]
    quiet = [str(program), "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run([*quiet, *picture, *video, str(silent)], check=True, timeout=60)
    subprocess.run([*quiet, *picture, "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000:duration=6", "-af",
                    "volume=2", *video, "-c:a", "aac", str(loud)], check=True, timeout=60)  # fmt: skip
    voice = _tone(program, tmp_path / "voice.wav", 220, 1.5, rate=24000, channels=1)
    music = _tone(program, tmp_path / "music.wav", 660, 2.5, volume=6.0)  # short and loud
    effect = _tone(program, tmp_path / "effect.wav", 1760, 2.0, channels=1)
    assert ffmpeg.loudness(program, music) > -12 and ffmpeg.loudness(program, silent) is None

    film = tmp_path / "film.mp4"
    ffmpeg.dub(program, silent, film, seconds=6.0, lines=[(voice, 2.0)], music=music, effects=[(effect, 4.0, 1.0)])
    said = _said(program, "-i", str(film))
    assert "Video: h264" in said and "Audio: aac" in said and "48000 Hz, stereo" in said, said
    assert len(_frames_of(program, film, tmp_path / "frames")) == 144  # the picture as it was
    whole = _said(program, "-i", str(film), "-map", "0:a", "-f", "null", "-")
    assert abs(float(re.findall(r"time=\d+:\d+:([\d.]+)", whole)[-1]) - 6.0) < 0.06, whole

    alone = _band(program, film, 660, 0.8, 0.8)  # music before the narrator
    assert -33 < alone < -22, alone  # the loud file at music's level
    assert alone - 8 < _band(program, film, 660, 2.4, 0.9) < alone - 4  # under the narrator: down, by 6 dB
    assert _band(program, film, 660, 4.05, 0.4) > alone - 4  # up again - and round again: the file is 2.5 s long
    assert _band(program, film, 660, 5.8, 0.2) < alone - 6  # fades out at the end
    # the line, at 2 s (where it is not, its pitch is only what the filter lets through of the music)
    assert _band(program, film, 220, 2.3, 1.0) > -26 and _band(program, film, 220, 0.8, 0.8) < -42
    assert _band(program, film, 220, 4.0, 1.5) < -42
    assert _band(program, film, 1760, 4.3, 0.5) > -40  # the scene's sound, in its scene
    assert _band(program, film, 1760, 1.0, 1.0) < -50 and _band(program, film, 1760, 5.3, 0.6) < -50  # 1 s, not 2

    own = tmp_path / "own.mp4"  # the film's own sound stays, and goes down under the narrator too
    ffmpeg.dub(program, loud, own, seconds=6.0, lines=[(voice, 2.0)])
    before = _band(program, own, 110, 0.5, 1.0)
    assert before > -30 and before - 8 < _band(program, own, 110, 2.4, 0.9) < before - 4
    assert _band(program, own, 220, 2.3, 1.0) > -26

    spoken = tmp_path / "spoken.mp4"  # the voice alone, on a film that had no sound
    ffmpeg.dub(program, silent, spoken, seconds=6.0, lines=[(voice, 0.0), (voice, 3.0)])
    assert _band(program, spoken, 220, 0.2, 1.0) > -26 and _band(program, spoken, 220, 3.2, 1.0) > -26
    assert _band(program, spoken, 220, 1.8, 1.0) < -60

    # a line longer than what is left of the film is cut at its end - and ffmpeg ends: cut inside the graph, it
    # never ended every second time (a time limit here, so that a hang is a failure, not a test that never ends)
    monkeypatch.setattr(ffmpeg, "JOIN_TIMEOUT_S", 30.0)
    for n in range(4):
        late = tmp_path / f"late-{n}.mp4"
        ffmpeg.dub(program, silent, late, seconds=6.0, lines=[(voice, 0.5), (voice, 5.2)], music=music,
                   effects=[(effect, 5.0, 2.0)])  # fmt: skip
        whole = _said(program, "-i", str(late), "-map", "0:a", "-f", "null", "-")
        assert abs(float(re.findall(r"time=\d+:\d+:([\d.]+)", whole)[-1]) - 6.0) < 0.06, whole
    assert _band(program, late, 220, 5.4, 0.5) > -30

    with pytest.raises(EngineError):
        ffmpeg.dub(program, silent, tmp_path / "nothing.mp4", seconds=6.0)
    with pytest.raises(EngineError):
        ffmpeg.dub(program, silent, tmp_path / "broken.mp4", seconds=6.0, music=tmp_path / "missing.wav")
    assert not (tmp_path / "broken.mp4").exists() and not list(tmp_path.glob("*.part"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_made_piece_is_levelled_by_what_is_heard_of_it_not_by_its_rumble(tmp_path):
    """53.7, the live film - a real ffmpeg: the engine of sound gives a scene's «wind» with nine tenths of its energy
    below 60 Hz, and music with a loud bottom no loudspeaker of a notebook gets. Levelled as they came, what is heard
    of them sank 20-30 dB under its place. A made piece is cut below its kind's frequency before it is measured and
    mixed: what is heard comes to the kind's level, the rumble is gone; the user's own music is left as it is."""
    program = Path(shutil.which("ffmpeg"))
    quiet = [str(program), "-hide_banner", "-loglevel", "error", "-y"]
    silent = tmp_path / "silent.mp4"
    subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=6", "-c:v", "libx264", "-g",
                    "1", "-pix_fmt", "yuv420p", str(silent)], check=True, timeout=60)  # fmt: skip

    def rumbling(name: str, hz: int, seconds: float) -> Path:
        """A loud rumble of 30 Hz and, 30 dB under it, the sound itself."""
        subprocess.run([*quiet, "-f", "lavfi", "-i", f"sine=frequency=30:sample_rate=48000:duration={seconds}", "-f",
                        "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration={seconds}",
                        "-filter_complex", "[0:a]volume=4[low];[1:a]volume=0.12[high];[low][high]amix=inputs=2:"
                        "normalize=0", "-ac", "1", str(tmp_path / name)], check=True, timeout=60)  # fmt: skip
        return tmp_path / name

    effect, music = rumbling("wind.wav", 1760, 2.0), rumbling("music.wav", 660, 6.0)
    assert ffmpeg.loudness(program, effect) - ffmpeg.loudness(program, effect, ffmpeg.EFFECT_CUT) > 20

    film = tmp_path / "film.mp4"
    ffmpeg.dub(program, silent, film, seconds=6.0, music=music, effects=[(effect, 3.0, 2.0)])
    assert -30 < _band(program, film, 1760, 3.4, 1.2) < -19  # the scene's sound at its level
    assert -34 < _band(program, film, 660, 0.8, 1.6) < -23  # the music at its own
    # the rumble, 30 dB over the sound as it came, is under it now
    assert _band(program, film, 30, 0.8, 1.6) < -32 and _band(program, film, 30, 3.4, 1.2) < -28

    own = tmp_path / "own.mp4"  # the user's own music: its bottom is its own
    ffmpeg.dub(program, silent, own, seconds=6.0, music=music, own_music=True)
    assert _band(program, own, 30, 0.8, 1.6) > -32 and _band(program, own, 660, 0.8, 1.6) < -50


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_the_bed_is_down_once_for_a_stretch_of_speech_not_at_every_word(tmp_path):
    """53.8, the user's ear («дождь как через шумоподавление») - a real ffmpeg: the narrator speaks with pauses
    between the words, as a person does. The bed - the film's own sound, the music - is down by the same 6 dB
    under a word and in the pause after it, and comes back slowly only when a stretch of speech is over; two lines
    a short pause apart are one stretch. A compressor keyed by the voice took the bed 10-20 dB down at each word and
    let it up in each pause."""
    program = Path(shutil.which("ffmpeg"))
    quiet = [str(program), "-hide_banner", "-loglevel", "error", "-y"]
    rain = tmp_path / "rain.mp4"  # a film with a steady sound of its own
    subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=10", "-f", "lavfi", "-i",
                    "sine=frequency=1760:sample_rate=48000:duration=10", "-c:v", "libx264", "-g", "1", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(rain)], check=True, timeout=60)  # fmt: skip
    line = tmp_path / "line.wav"  # two words of half a second, 0.6 s of silence between them
    subprocess.run([*quiet, "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=24000:duration=1.6", "-af",
                    "volume='if(between(t,0.5,1.1),0,2)':eval=frame", "-ac", "1", str(line)], check=True,
                   timeout=60)  # fmt: skip
    assert ffmpeg._stretches([(2.0, 3.6), (4.2, 5.8)], 10.0) == [(1.6, 6.2)]  # 0.6 s apart: one stretch
    assert ffmpeg._stretches([(2.0, 3.6), (8.0, 9.9)], 10.0) == [(1.6, 4.0), (7.6, 10.0)]

    film = tmp_path / "film.mp4"
    ffmpeg.dub(program, rain, film, seconds=10.0, lines=[(line, 2.0), (line, 4.2)])
    alone = _band(program, film, 1760, 0.2, 1.0)  # before the narrator: as it was filmed
    word, pause = _band(program, film, 1760, 2.1, 0.3), _band(program, film, 1760, 2.65, 0.3)
    assert alone - 7.5 < word < alone - 4.5 and abs(word - pause) < 1.0, (alone, word, pause)
    between = _band(program, film, 1760, 3.75, 0.3)  # between the two lines: the same stretch
    assert abs(between - word) < 1.0, (word, between)
    assert abs(_band(program, film, 1760, 5.0, 0.3) - word) < 1.0
    assert _band(program, film, 1760, 7.0, 1.5) > alone - 1.0  # the speech over: up again


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_where_a_sounds_energy_lies_is_measured_by_bands(tmp_path):
    """53.12 - a real ffmpeg: the listener says what sounds, not that it sounds as from an old loudspeaker (53.10:
    the video model's rain had next to nothing above 4 kHz, its thunder nothing above 800 Hz - and both passed for
    rain). `bands` says where a sound's energy lies: the rumble below 100 Hz, and of the rest - how much is above
    1 kHz, above 4 and above 8 kHz; of a piece of a film too; silence and a file without sound - None. A clip's
    sound alone is taken as a WAV for the listener."""
    program = Path(shutil.which("ffmpeg"))
    quiet = [str(program), "-hide_banner", "-loglevel", "error", "-y"]

    def made(name: str, source: str, chain: str = "anull") -> Path:
        subprocess.run([*quiet, "-f", "lavfi", "-i", source, "-af", chain, "-ac", "1", str(tmp_path / name)],
                       check=True, timeout=60)  # fmt: skip
        return tmp_path / name

    noise = "anoisesrc=color=white:sample_rate=48000:duration=3:amplitude=0.3:seed=5"
    rain = ffmpeg.bands(program, made("rain.wav", noise))
    assert rain["top"] > 0.7 and rain["mid"] > 0.9 and rain["low"] < 0.05 and -30 < rain["level"] < -5, rain
    assert 0.5 < rain["air"] < rain["top"]
    # the same through an old loudspeaker: nothing above 3 kHz
    old = ffmpeg.bands(program, made("old.wav", noise, "lowpass=f=3000,lowpass=f=3000,lowpass=f=3000"))
    assert old["top"] < 0.08 and old["air"] < 0.01 and old["mid"] > 0.4, old
    hum = ffmpeg.bands(program, made("hum.wav", noise, "lowpass=f=500,lowpass=f=500,lowpass=f=500"))
    assert hum["top"] < 0.01 and hum["mid"] < 0.1, hum
    rumble = ffmpeg.bands(program, made("rumble.wav", "sine=frequency=40:sample_rate=48000:duration=3"))
    assert rumble["low"] > 0.9, rumble
    assert ffmpeg.bands(program, made("none.wav", "anullsrc=sample_rate=48000:duration=2")) is None

    film = tmp_path / "film.mp4"  # a hum for two seconds, then the rain: each scene's stretch by itself
    subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=4", "-i",
                    str(tmp_path / "hum.wav"), "-i", str(tmp_path / "rain.wav"), "-filter_complex",
                    "[1:a]atrim=duration=2[a];[2:a]atrim=duration=2[b];[a][b]concat=n=2:v=0:a=1[s]", "-map", "0:v",
                    "-map", "[s]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    str(film)], check=True, timeout=60)  # fmt: skip
    first, second = ffmpeg.bands(program, film, at=0.2, seconds=1.6), ffmpeg.bands(program, film, at=2.2, seconds=1.6)
    assert first["mid"] < 0.1 and second["top"] > 0.6, (first, second)
    silent = tmp_path / "silent.mp4"
    subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(silent)], check=True, timeout=60)  # fmt: skip
    assert ffmpeg.bands(program, silent) is None and ffmpeg.bands(program, tmp_path / "missing.wav") is None

    wav = tmp_path / "heard.wav"
    ffmpeg.sound_of(program, film, wav)
    said = _said(program, "-i", str(wav))
    assert "pcm_s16le" in said and "48000 Hz" in said and "mono" in said and not list(tmp_path.glob("*.part"))
    with pytest.raises(EngineError, match="не взял звук клипа"):
        ffmpeg.sound_of(program, silent, tmp_path / "no.wav")
    assert not (tmp_path / "no.wav").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_the_films_own_sound_is_silent_where_a_scene_got_a_made_one(tmp_path):
    """53.14 - a real ffmpeg: a scene whose sound, as the video model filmed it, was not right gets a made one. In
    the film's sound track the scene's own sound is taken away for its stretch - down and up in a moment, as the
    made one comes in and goes out - and is as it was before and after it."""
    program = Path(shutil.which("ffmpeg"))
    quiet = [str(program), "-hide_banner", "-loglevel", "error", "-y"]
    source = tmp_path / "source.mp4"
    subprocess.run([*quiet, "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=6", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=6", "-c:v", "libx264", "-g", "1", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-b:a", "192k", str(source)], check=True, timeout=60)  # fmt: skip
    effect = _tone(program, tmp_path / "rain.wav", 1760, 2.0, channels=1)

    film = tmp_path / "film.mp4"
    ffmpeg.dub(program, source, film, seconds=6.0, effects=[(effect, 2.0, 2.0)], mute=[(2.0, 2.0)])
    before, inside, after = (_band(program, film, 440, at, 1.2) for at in (0.4, 2.4, 4.4))
    # (what is left there is the made tone's skirt in the measure's band, not the scene's own sound)
    assert before > -30 and after > -30 and inside < before - 30, (before, inside, after)
    assert _band(program, film, 1760, 2.4, 1.2) > -30 and _band(program, film, 1760, 0.4, 1.2) < -50

    both = tmp_path / "both.mp4"  # nothing muted: the scene's own sound under the made one, as before the step
    ffmpeg.dub(program, source, both, seconds=6.0, effects=[(effect, 2.0, 2.0)])
    assert abs(_band(program, both, 440, 2.4, 1.2) - before) < 1.0
