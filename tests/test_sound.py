"""A film's added sound (stage 53): audio.cpp fetched as a part, its models, a piece made by the program on the
graphics card. A real hub, stand-ins of GitHub and Hugging Face, and a stand-in of the engine's program - a real
process that writes a real WAV (tests/fake_audio_cli.py)."""

import asyncio
import hashlib
import io
import json
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from hub import audio_cpp, engines, hf, sound
from tests.test_visual import FakeDrawSwap, hub  # noqa: F401 - the Studio's stand

CLI = Path(__file__).with_name("fake_audio_cli.py")
BUILD = "cuda-13"


def archive(names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in names:
            zf.writestr(name, b"MZ" + name.encode())
    return data.getvalue()


ARCHIVES = {
    f"audio-{audio_cpp.TAG}-{audio_cpp.ARCHIVES[BUILD][0]}.zip": archive(
        [audio_cpp.PROGRAM, "ggml-cuda.dll", "model_specs/qwen3_tts.json", "LICENSE"]),
    f"audio-{audio_cpp.TAG}-{audio_cpp.ARCHIVES[BUILD][1]}.zip": archive(["cudart64_13.dll", "cublas64_13.dll"]),
}  # fmt: skip
MODELS = {part: (part.encode() + b"-gguf-") * 4000 for part in audio_cpp.PARTS}  # a few tens of kilobytes each
MODELS["effects"] *= 64  # three megabytes: comes in several of the hub's chunks, to be stopped on the way


class FakeSources(BaseHTTPRequestHandler):
    """GitHub's release of audio.cpp with its archives, and the models' files on Hugging Face (by Range)."""

    port = 0
    slow = 0.0  # seconds a chunk of a model's file
    corrupt: ClassVar[set] = set()  # the parts whose file comes with a wrong byte
    asked: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json", status=200, first=0, whole=0):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        if status == 206:
            self.send_header("content-range", f"bytes {first}-{first + len(data) - 1}/{whole}")
        self.end_headers()
        try:
            for at in range(0, len(data), 1 << 18):
                self.wfile.write(data[at : at + (1 << 18)])
                if self.slow and kind == "application/octet-stream":
                    time.sleep(self.slow)
        except OSError:
            pass

    def do_GET(self):
        type(self).asked.append(self.path)
        url = f"http://127.0.0.1:{self.port}/download/"
        if self.path == f"/repos/{audio_cpp.REPO}/releases/tags/{audio_cpp.TAG}":
            assets = [{"name": name, "size": len(data), "browser_download_url": url + name,
                       "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                      for name, data in ARCHIVES.items()]  # fmt: skip
            assets.append({"name": f"audio-{audio_cpp.TAG}-bin-windows-x64-vulkan.zip", "size": 1,
                           "browser_download_url": url + "vulkan", "digest": "sha256:" + "0" * 64})  # fmt: skip
            return self._send({"tag_name": audio_cpp.TAG, "assets": assets})
        if self.path.startswith("/download/") and (name := self.path.removeprefix("/download/")) in ARCHIVES:
            return self._send(ARCHIVES[name], "application/zip")
        for part, spec in audio_cpp.PARTS.items():
            if self.path == f"/{audio_cpp.MODELS}/resolve/{audio_cpp.REVISION}/{spec['path']}":
                data = MODELS[part]
                if part in self.corrupt:
                    data = b"X" + data[1:]
                first = int((self.headers.get("Range") or "bytes=0-").removeprefix("bytes=").split("-")[0])
                return self._send(data[first:], "application/octet-stream", 206 if first else 200, first, len(data))
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def stand(hub, monkeypatch, tmp_path):  # noqa: F811
    """The hub with the stand-ins: GitHub and Hugging Face, the engine's program, the models' pins - the stand's
    files'; what the program was asked - `stand.asked()`."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSources)
    FakeSources.port, FakeSources.slow, FakeSources.corrupt, FakeSources.asked = server.server_port, 0.0, set(), []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv(hf.BASE_ENV, f"http://127.0.0.1:{server.server_port}")
    for part, data in MODELS.items():
        monkeypatch.setitem(audio_cpp.PARTS, part, {**audio_cpp.PARTS[part], "size": len(data),
                                                    "sha256": hashlib.sha256(data).hexdigest()})  # fmt: skip
    monkeypatch.setattr(audio_cpp, "command", lambda _program: [sys.executable, str(CLI)])
    log = tmp_path / "engine.jsonl"
    monkeypatch.setenv("FAKE_AUDIO_LOG", str(log))
    monkeypatch.setattr(sound, "TICK_S", 0.05)

    async def build():
        return BUILD

    hub.app.state.sound.machine = build
    hub.asked = lambda: [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() \
        else []  # fmt: skip
    yield hub
    server.shutdown()


def part(state, name):
    return next(p for p in state["parts"] if p["id"] == name)


def wait_part(hub, name, *states, timeout=20.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = part(hub.get("/v1/sound").json(), name)
        if found["state"] in states:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{name} is still {found}")


def install(hub, *names):  # noqa: F811
    for name in names:
        assert hub.post(f"/v1/sound/parts/{name}").status_code == 200
        assert wait_part(hub, name, "ready", "missing")["state"] == "ready", name


def test_a_sound_part_comes_with_the_engine_and_a_piece_is_made_holding_the_card(stand, tmp_path):
    """53.2: «Скачать» fetches the engine's build for this computer (the sha256 GitHub gives checked, its archives
    not kept) and the part's model (pinned by size and sha256); the window is told as it goes. A piece is made by
    the program - a real process - while the hub holds the card from local chats, the models llama-swap held
    unloaded first; the card is given after."""
    hub = stand  # noqa: F811
    service, card = hub.app.state.sound, hub.app.state.card
    was = hub.get("/v1/sound").json()
    assert was["engine"] == {"tag": audio_cpp.TAG, "build": BUILD, "ready": False}
    assert [(p["id"], p["state"], p["size"]) for p in was["parts"]] == [
        (name, "missing", len(MODELS[name])) for name in ("voice", "music", "effects")]  # fmt: skip
    assert [v["id"] for v in was["voices"]][:2] == ["ryan", "aiden"] and len(was["voices"]) == 9
    assert hub.post("/v1/sound/parts/drums").status_code == 404

    install(hub, "voice")
    now = hub.get("/v1/sound").json()
    assert now["engine"]["ready"] and part(now, "music")["state"] == "missing"
    program = audio_cpp.find(service.tools, BUILD)
    assert program is not None and (program.parent / "cudart64_13.dll").is_file()
    assert (program.parent / "model_specs" / "qwen3_tts.json").is_file()
    assert not (service.tools / "downloads" / "audio.cpp").exists()
    assert audio_cpp.model_file(service.models, "voice").read_bytes() == MODELS["voice"]
    told = [payload for kind, payload in hub.seen if kind == "sound.changed"]
    assert any(part(p, "voice")["state"] == "loading" for p in told) and part(told[-1], "voice")["state"] == "ready"

    FakeSources.asked.clear()
    install(hub, "music")  # the engine is here: the model alone
    assert not any("releases" in path or "download" in path for path in FakeSources.asked)

    target, held = tmp_path / "line.wav", []

    async def speak():
        cancel = asyncio.Event()
        async with service.working(cancel.set):
            held.append((card.holder, card.free.is_set()))
            return await service.speak(target, cancel, text="Привет, это диктор «АФО»", voice="serena")

    assert hub.portal.call(speak) == target and held == [("sound", False)]
    assert card.holder == "" and card.free.is_set()
    assert FakeDrawSwap.unloaded == ["unload"]  # every model llama-swap held, before the program
    asked = hub.asked()[-1]
    assert (asked["family"], asked["text"], asked["voice"], asked["language"], asked["backend"]) == (
        "qwen3_tts", "Привет, это диктор «АФО»", "serena", "Russian", "cuda")  # fmt: skip
    assert Path(asked["model"]) == audio_cpp.model_file(service.models, "voice")
    assert Path(asked["cwd"]) == program.parent  # the program's own folder: its model_specs lie there
    assert sound.seconds_of(target) == pytest.approx(len("Привет, это диктор «АФО»") / 12, abs=0.01)
    assert [p.name for p in tmp_path.glob("line*")] == ["line.wav"]

    async def music():
        cancel = asyncio.Event()
        async with service.working(cancel.set):
            return await service.music(tmp_path / "music.wav", cancel, words="calm piano", seconds=7)

    hub.portal.call(music)
    asked = hub.asked()[-1]
    assert (asked["family"], asked["seconds"], asked["lyrics"]) == ("ace_step", 7.0, "[instrumental]")
    assert "calm piano" in asked["text"] and "no vocals" in asked["text"]
    assert sound.seconds_of(tmp_path / "music.wav") == pytest.approx(7.0, abs=0.01)


def test_a_piece_stopped_or_failed_leaves_nothing_and_gives_the_card(stand, tmp_path, monkeypatch):
    """53.2: «Стоп» kills the program at once - no file, no half of one, the card given; a program that fails says
    why in words; a part that is not here is said so before anything is started."""
    hub = stand  # noqa: F811
    service, card = hub.app.state.sound, hub.app.state.card
    install(hub, "voice")

    async def piece(kind, stop_after=None, **what):
        cancel = asyncio.Event()
        if stop_after is not None:
            asyncio.get_running_loop().call_later(stop_after, cancel.set)
        async with service.working(cancel.set):
            return await getattr(service, kind)(tmp_path / f"{kind}.wav", cancel, **what)

    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "30")
    began = time.monotonic()
    with pytest.raises(sound.SoundError):
        hub.portal.call(lambda: piece("speak", 0.5, text="Долгая речь", voice="ryan"))
    assert time.monotonic() - began < 10 and not list(tmp_path.glob("speak*"))
    assert card.holder == "" and card.free.is_set()

    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "0")
    monkeypatch.setenv("FAKE_AUDIO_FAIL", "qwen3_tts")
    with pytest.raises(sound.SoundError, match="out of memory") as failed:
        hub.portal.call(lambda: piece("speak", text="Речь", voice="ryan"))
    assert failed.value.status == 502 and not list(tmp_path.glob("speak*")) and card.holder == ""

    with pytest.raises(sound.SoundError, match="Звуки сцен") as missing:  # the engine is here, the part is not
        hub.portal.call(lambda: piece("effect", words="rain", seconds=3))
    assert missing.value.status == 422 and len(hub.asked()) == 2


def test_a_part_broken_on_the_way_stopped_and_removed(stand):
    """53.2: a model that comes with another sha256 is not kept and says so; a download stopped keeps what came and
    goes on from there; «Удалить» takes the model's file away, the engine stays."""
    hub = stand  # noqa: F811
    service = hub.app.state.sound
    FakeSources.corrupt = {"effects"}
    hub.post("/v1/sound/parts/effects")
    broken = wait_part(hub, "effects", "missing")
    assert "sha256" in broken["error"] and not audio_cpp.model_file(service.models, "effects").exists()
    assert hub.get("/v1/sound").json()["engine"]["ready"]  # the engine came whole

    FakeSources.corrupt, FakeSources.slow = set(), 0.2
    hub.post("/v1/sound/parts/effects")
    deadline = time.monotonic() + 10
    while part(hub.get("/v1/sound").json(), "effects")["got"] == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    stopped = part(hub.post("/v1/sound/parts/effects/stop").json(), "effects")
    left = audio_cpp.model_file(service.models, "effects")
    assert stopped["state"] == "missing" and stopped["error"] == ""
    assert 0 < left.with_name(left.name + ".part").stat().st_size < len(MODELS["effects"])

    FakeSources.slow, FakeSources.asked = 0.0, []
    install(hub, "effects")
    assert left.read_bytes() == MODELS["effects"]
    removed = hub.delete("/v1/sound/parts/effects").json()
    assert part(removed, "effects")["state"] == "missing" and removed["engine"]["ready"] and not left.exists()
