"""A film's added sound (stage 53): audio.cpp fetched as a part, its models, a piece made by the program on the
graphics card. A real hub, stand-ins of GitHub and Hugging Face, and a stand-in of the engine's program - a real
process that writes a real WAV (tests/fake_audio_cli.py)."""

import asyncio
import hashlib
import io
import json
import math
import shutil
import struct
import sys
import threading
import time
import wave
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
    clips = tmp_path / "voices"  # the voices' clips: the stand-in's own voice, a tone of 220 Hz
    clips.mkdir()
    for voice in audio_cpp.VOICES:
        with wave.open(str(clips / f"{voice}.wav"), "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(24000)
            clip.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * n / 24000)))
                                      for n in range(24000)))  # fmt: skip
    monkeypatch.setattr(audio_cpp, "VOICE_CLIPS", clips)
    audio_cpp.voice_pitch.cache_clear()
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


def test_a_sound_part_comes_with_the_engine_and_a_piece_is_made_holding_the_card(stand, tmp_path, monkeypatch):
    """53.2: «Скачать» fetches the engine's build for this computer (the sha256 GitHub gives checked, its archives
    not kept) and the part's model (pinned by size and sha256); the window is told as it goes. A piece is made by
    the program - a real process - while the hub holds the card from local chats, the models llama-swap held
    unloaded first; the card is given after."""
    hub = stand  # noqa: F811
    service, card = hub.app.state.sound, hub.app.state.card
    was = hub.get("/v1/sound").json()
    assert was["engine"] == {"tag": audio_cpp.TAG, "build": BUILD, "ready": False}
    assert [(p["id"], p["state"], p["size"]) for p in was["parts"]] == [
        (name, "missing", len(MODELS[name])) for name in ("voice", "music", "effects", "listener")]  # fmt: skip
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
        async with service.working(cancel):
            held.append((card.holder, card.free.is_set()))
            return await service.speak(target, cancel, text="Привет, это диктор «АФО»", voice="serena")

    assert hub.portal.call(speak) == target and held == [("sound", False)]
    assert card.holder == "" and card.free.is_set()
    assert FakeDrawSwap.unloaded == ["unload"]  # every model llama-swap held, before the program
    asked = hub.asked()[-1]
    assert (asked["family"], asked["text"], asked["voice"], asked["language"], asked["backend"]) == (
        "qwen3_tts", "Привет, это диктор «АФО»", "serena", "Russian", "cuda")  # fmt: skip
    assert (asked["alone"], asked["seed"]) == ("true", "7")  # by the voice's clip, its voice alone; the first take
    assert Path(asked["model"]) == audio_cpp.model_file(service.models, "voice")
    assert Path(asked["cwd"]) == program.parent  # the program's own folder: its model_specs lie there
    assert sound.seconds_of(target) == pytest.approx(len("Привет, это диктор «АФО»") / 12, abs=0.01)
    assert [p.name for p in tmp_path.glob("line*")] == ["line.wav"]

    # (53.8) a reading in another voice is not the line: read again by the next seed, the take in the clip's
    # voice kept; three takes at most - then the nearest; one that went on to the end of its tokens is not kept
    def said(env, value, text):
        monkeypatch.setenv(env, str(len([a for a in hub.asked() if a["family"] == "qwen3_tts"]) + value))
        before = len(hub.asked())
        path = tmp_path / f"{env}-{value}.wav"

        async def line():
            cancel = asyncio.Event()
            async with service.working(cancel):
                return await service.speak(path, cancel, text=text, voice="ryan")

        hub.portal.call(line)
        monkeypatch.delenv(env)
        return path, [a["seed"] for a in hub.asked()[before:]]

    path, seeds = said("FAKE_AUDIO_HIGH", 2, "Утро в горах.")
    assert seeds == ["7", "8", "9"] and audio_cpp.pitch_of(path) == pytest.approx(220, rel=0.05)
    path, seeds = said("FAKE_AUDIO_HIGH", 5, "Утро в горах.")
    assert seeds == ["7", "8", "9"] and audio_cpp.pitch_of(path) == pytest.approx(330, rel=0.08)  # none held
    path, seeds = said("FAKE_AUDIO_LONG", 1, "Утро в горах.")
    assert seeds == ["7", "8"] and sound.seconds_of(path) == pytest.approx(len("Утро в горах.") / 12, abs=0.01)
    assert not list(tmp_path.glob("*.take*")) and card.holder == ""

    async def music():
        cancel = asyncio.Event()
        async with service.working(cancel):
            return await service.music(tmp_path / "music.wav", cancel, words="calm piano", seconds=7)

    hub.portal.call(music)
    asked = hub.asked()[-1]
    assert (asked["family"], asked["seconds"], asked["lyrics"]) == ("ace_step", 7.0, "[instrumental]")
    assert "calm piano" in asked["text"] and "no vocals" in asked["text"]
    assert sound.seconds_of(tmp_path / "music.wav") == pytest.approx(7.0, abs=0.01)


def test_a_sound_part_whose_licence_forbids_commercial_use_is_fetched_only_with_consent(stand, monkeypatch):
    """62 (the user's word 2026-11-21): a part of sound whose licence forbids commercial use (CC-BY-NC here) - its
    «Скачать» refused before the engine or the model is fetched, 409 with the code the window asks by; with
    «Понимаю, скачать» (`licence_ok`) - fetched as any other."""
    hub = stand  # noqa: F811
    monkeypatch.setitem(audio_cpp.PARTS, "listener", audio_cpp.PARTS["listener"] | {"licence": "CC-BY-NC-4.0"})
    refused = hub.post("/v1/sound/parts/listener")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert (detail["code"], detail["name"], detail["licence"]) == ("licence", "Слушатель", "CC-BY-NC-4.0")
    assert not FakeSources.asked and part(hub.get("/v1/sound").json(), "listener")["state"] == "missing"
    assert hub.post("/v1/sound/parts/listener", json={"licence_ok": True}).status_code == 200
    assert wait_part(hub, "listener", "ready", "missing")["state"] == "ready"
    install(hub, "voice")  # Apache-2.0: not asked


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
        async with service.working(cancel):
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

    async def behind_a_video():  # the Studio films: the wait for the card ends with «Стоп», nothing is started
        cancel = asyncio.Event()
        await card.take("studio", chats=True)
        asyncio.get_running_loop().call_later(0.3, cancel.set)
        try:
            async with service.working(cancel):
                raise AssertionError("the card was the Studio's")
        finally:
            card.give("studio")

    with pytest.raises(sound.SoundError):
        hub.portal.call(behind_a_video)
    assert card.holder == "" and len(hub.asked()) == 2


def tone(path, pitch, seconds=1.0, rate=48000):
    """A WAV of one tone, as the engine writes a sound."""
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * pitch * n / rate)))
                                  for n in range(int(seconds * rate))))  # fmt: skip
    return path


def test_the_listener_says_what_is_heard_in_a_sound_without_the_card(stand, tmp_path, monkeypatch):
    """53.11: the listener is the fourth part of the engine of sound - fetched as the others are, pinned by size
    and sha256. Asked what is heard in a WAV, the program - a real process - answers with its labels, the surest
    first; it works on the processor, so the card is not taken and nothing is unloaded: it answers while the Studio
    holds the card. Not here - said so; «Стоп» kills it at once; a program that fails says why in words."""
    hub = stand  # noqa: F811
    service, card = hub.app.state.sound, hub.app.state.card
    rain, dog = tone(tmp_path / "rain.wav", 1760), tone(tmp_path / "dog.wav", 440)

    async def listen(path, stop_after=None):
        cancel = asyncio.Event()
        if stop_after is not None:
            asyncio.get_running_loop().call_later(stop_after, cancel.set)
        return await service.listen(path, cancel)

    install(hub, "voice")  # the engine is here, the listener is not
    with pytest.raises(sound.SoundError, match="Слушатель") as missing:
        hub.portal.call(lambda: listen(rain))
    assert missing.value.status == 422 and hub.asked() == []

    found = part(hub.get("/v1/sound").json(), "listener")
    assert (found["state"], found["size"], found["licence"]) == ("missing", len(MODELS["listener"]), "Apache-2.0")
    install(hub, "listener")
    assert audio_cpp.model_file(service.models, "listener").read_bytes() == MODELS["listener"]

    async def behind_the_studio():  # the card is the Studio's: the listener does not wait for it
        await card.take("studio", chats=True)
        try:
            return await listen(rain), card.holder
        finally:
            card.give("studio")

    heard, holder = hub.portal.call(behind_the_studio)
    assert heard == [("Rain", 0.64), ("Rain on surface", 0.5)] and holder == "studio"
    assert hub.portal.call(lambda: listen(dog))[0] == ("Dog", 0.82)
    assert FakeDrawSwap.unloaded == []  # the models llama-swap holds stay where they are
    asked = hub.asked()[-1]
    assert (asked["family"], asked["task"], asked["backend"], asked["top"]) == ("ced", "cls", "cpu", "8")
    assert Path(asked["audio"]) == dog and Path(asked["model"]) == audio_cpp.model_file(service.models, "listener")

    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "30")
    began = time.monotonic()
    with pytest.raises(sound.SoundError):
        hub.portal.call(lambda: listen(rain, 0.5))
    assert time.monotonic() - began < 10
    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "0")
    monkeypatch.setenv("FAKE_AUDIO_FAIL", "ced")
    with pytest.raises(sound.SoundError, match="failed to read the audio file") as failed:
        hub.portal.call(lambda: listen(rain))
    assert failed.value.status == 502 and card.holder == ""


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


BOARD = {"scenes": [
    {"mode": "text", "seconds": 3, "words": "Дождь", "prompt": "rain falls on a street", "sound": "rain on a roof",
     "say": " Дождь не  страшен. "},
    {"mode": "text", "seconds": 3, "words": "Прохожий", "prompt": "a man walks by", "sound": "", "say": ""},
    {"mode": "text", "seconds": 3, "words": "Гром", "prompt": "lightning over the roofs", "sound": "thunder",
     "say": "Наш дождевик не промокает."}],
    "music": " calm piano,  slow "}  # fmt: skip
SILENT = {"engine": "sd", "set": "film", "kind": "video", "tier": "video", "size": 768, "modes": ["text"],
          "flags": ["--cfg-scale", "1", "--steps", "8"],
          "clip": {"fps": 24, "step": 8, "grid": 32, "sizes": {"wide": [768, 512], "tall": [512, 768],
                                                               "square": [640, 640]}}}  # fmt: skip


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_film_gets_its_narrator_music_and_silent_scenes_sounds_when_it_is_joined(stand, monkeypatch, tmp_path):
    """53.3-53.5: a film with a narrator and made music - the director is asked each scene's line and what music,
    and its answer kept; joined, the film gets the lines read by the film's voice (each at its scene), the music,
    and the sounds of the scenes filmed without sound - made by the engine (a real process), the card held, and
    mixed by a real ffmpeg. A piece made is not made twice; another line - that line alone is made anew, the clip
    stays; the user's own music takes the made one's place; «Стоп» while the pieces are made stops at once; a part
    that is not here is said before anything begins."""
    from app.hub_client import HubClient, HubError
    from hub import director, ffmpeg
    from hub.gateway.openai_compat import OpenAICompatProvider
    from tests.test_autonomous import use
    from tests.test_director import _until
    from tests.test_ffmpeg import _band, _said, _solid, _tone, animated_webp
    from tests.test_judge import SeeingCloud, roles
    from tests.test_visual import _install

    hub = stand  # noqa: F811
    asks = {"sound": True, "say": True, "music": True}
    system, _prompt, form = director.question("Дождевик", 9, "wide", ["text"], False, **asks)
    assert '"say": "..."' in form and '"music": "..."' in form and "narrator's line" in system
    assert "33 characters" in system and '"music": English' in system
    system, _prompt, form = director.question("Дождевик", 9, "wide", ["text"], False)
    assert '"say"' not in form and '"music"' not in form and "narrator" not in system
    clip = {"fps": 24, "step": 8}
    read = director.reader(clip=clip, modes=["text"], photo=False, seconds=9, **asks)(json.dumps(BOARD))
    assert [s["say"] for s in read["scenes"]] == ["Дождь не страшен.", "", "Наш дождевик не промокает."]
    assert read["music_words"] == "calm piano, slow"
    plain = director.reader(clip=clip, modes=["text"], photo=False, seconds=9)(json.dumps(BOARD))
    assert [s["say"] for s in plain["scenes"]] == ["", "", ""] and plain["music_words"] == ""

    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    model = tmp_path / "film.gguf"
    model.write_bytes(b"x")
    _install(hub, "film", SILENT, [{"role": "diffusion-model", "repo": "test/Film-GGUF", "path": model.name,
                                    "size": 1, "at": str(model)}])  # a video model that films no sound  # fmt: skip
    # the engine's clip of a scene: a silent WebP of 73 frames - 3 s
    FakeDrawSwap.filmed = animated_webp([_solid((20, 40, 30 + 3 * k)) for k in range(73)], 64, 64)
    install(hub, "voice", "effects")
    cloud = SeeingCloud([json.dumps(BOARD, ensure_ascii=False)] * 2)
    try:
        use(hub, OpenAICompatProvider("studio-cloud", cloud.url, locality="cloud", api_key="k"))
        roles(hub, director="studio-cloud/eye")
        client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
        media, card = hub.app.state.visual.media, hub.app.state.card
        with pytest.raises(HubError, match="нет такого голоса"):
            client.new_film({"idea": "Дождевик", "seconds": 9, "voice": "caruso"})
        made = client.new_film({"idea": "Дождевик", "seconds": 9, "checks": False, "voice": "serena",
                                "music": "made"})["film"]  # fmt: skip
        film_id = made["id"]
        assert (made["voice"], made["music"], made["music_words"]) == ("serena", "made", "")
        client.write_film(film_id)
        written = _until(hub, film_id, lambda f: f["status"] == "ready")
        assert [s["say"] for s in written["scenes"]] == ["Дождь не страшен.", "", "Наш дождевик не промокает."]
        assert [s["sound_words"] for s in written["scenes"]] == ["rain on a roof", "", "thunder"]  # the engine's part
        assert written["music_words"] == "calm piano, slow"
        asked = json.dumps(cloud.asked[0]["messages"][0])
        assert "narrator" in asked and "music" in asked

        client.shoot_film(film_id)
        _until(hub, film_id, lambda f: f["status"] == "ready" and all(s["status"] == "done" for s in f["scenes"]))
        with pytest.raises(HubError, match="Музыка"):  # the music's part is not here: said before anything begins
            client.join_film(film_id)
        assert hub.asked() == [] and client.film(film_id)["film"]["status"] == "ready"
        install(hub, "music")

        client.join_film(film_id)
        joined = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"])
        assert joined["error"] == "" and card.holder == "" and card.free.is_set()
        made = hub.asked()
        assert sorted((a["family"], a["text"]) for a in made if a["family"] != "ace_step") == [
            ("moss_soundeffect", "rain on a roof"), ("moss_soundeffect", "thunder"),
            ("qwen3_tts", "Дождь не страшен."), ("qwen3_tts", "Наш дождевик не промокает.")]  # fmt: skip
        assert {a["voice"] for a in made if a["family"] == "qwen3_tts"} == {"serena"}
        tune = next(a for a in made if a["family"] == "ace_step")
        assert tune["text"].startswith("calm piano, slow") and tune["seconds"] == pytest.approx(9.1, abs=0.1)
        assert [a["seconds"] for a in made if a["family"] == "moss_soundeffect"] == [pytest.approx(3.0, abs=0.1)] * 2
        film = media / joined["file"]
        assert "Audio: aac" in _said(program, "-i", str(film))
        # the voice's pitch where its two lines are: at the first scene's beginning and the third's (6.08 s)
        assert _band(program, film, 220, 0.3, 1.0) > -30 and _band(program, film, 220, 6.5, 1.5) > -30
        assert _band(program, film, 220, 3.5, 2.0) < -42
        alone = _band(program, film, 660, 3.5, 2.0)  # the music, alone in the second scene - and lower under a line
        assert alone > -40 and _band(program, film, 660, 6.5, 1.5) < alone - 5
        # a silent scene's own sound: the first scene's, none in the second - it has no «what is heard»
        assert _band(program, film, 1760, 2.0, 0.9) > -45 and _band(program, film, 1760, 3.5, 2.0) < -50
        pieces = sorted(p.name.split("-")[2] for p in media.glob(f"*/film-{film_id}-*.wav"))
        assert pieces == ["fx", "fx", "music", "say", "say"] and not list(media.glob("*/*.cut.mp4"))

        client.join_film(film_id)  # joined again: no piece is made twice
        _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"] and f["file"] != joined["file"])
        assert len(hub.asked()) == len(made) and not (media / joined["file"]).exists()

        long = "Наш дождевик не промокает даже в самую сильную грозу, проверено годами."
        edited = client.update_scene(film_id, 3, {"say": long})["film"]
        assert edited["file"] == "" and [s["status"] for s in edited["scenes"]] == ["done"] * 3  # the clip stays
        client.join_film(film_id)
        late = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"])
        assert [(a["family"], a["text"]) for a in hub.asked()[len(made) :]] == [("qwen3_tts", long)]
        assert "диктор не уместился" in late["error"]  # 6 s of speech in a scene of 3, the film's last
        assert len(list(media.glob(f"*/film-{film_id}-say-*.wav"))) == 2  # the line before is gone

        own = _tone(program, tmp_path / "own.wav", 330, 4.0)
        with pytest.raises(HubError):
            client.update_film(film_id, {"music_path": str(tmp_path / "missing.mp3")})
        with pytest.raises(HubError):
            client.update_film(film_id, {"music_path": str(model)})  # not a sound file
        mine = client.update_film(film_id, {"music_path": str(own)})["film"]
        assert mine["music"] == "file" and (media / mine["music_file"]).read_bytes() == own.read_bytes()
        assert mine["file"] == ""
        before = len(hub.asked())
        client.join_film(film_id)
        with_own = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"])
        assert len(hub.asked()) == before  # nothing to make: the lines are here, the music is the user's
        assert _band(program, media / with_own["file"], 330, 3.5, 2.0) > -40
        assert not list(media.glob(f"*/film-{film_id}-music-*.wav"))  # the made music is not the film's any more

        monkeypatch.setenv("FAKE_AUDIO_SLEEP", "30")
        client.update_scene(film_id, 1, {"say": "Совсем другая строка."})
        began = client.join_film(film_id)["film"]
        assert began["status"] == "joining"
        deadline = time.monotonic() + 10
        while len(hub.asked()) == before and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        stopped = client.stop_film(film_id)["film"]
        assert time.monotonic() - started < 10
        assert (stopped["status"], stopped["error"], stopped["file"]) == ("ready", "", "")
        assert card.holder == "" and not list(media.glob("*/*.cut.mp4")) and not list(media.glob("*/*.part.wav"))

        monkeypatch.setenv("FAKE_AUDIO_SLEEP", "0")
        off = client.update_film(film_id, {"voice": "", "music": "", "sound": False})["film"]
        assert (off["voice"], off["music"]) == ("", "")
        before = len(hub.asked())
        client.join_film(film_id)  # nothing added: the film as its scenes are - silent, as before the stage
        bare = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"])
        assert len(hub.asked()) == before and "Audio:" not in _said(program, "-i", str(media / bare["file"]))

        # (53.6) the music's description the user wrote stays: the director is asked for it only while there is none
        client.update_film(film_id, {"music": "made", "music_words": "slow jazz"})
        client.write_film(film_id)
        again = _until(hub, film_id, lambda f: f["status"] == "ready" and not f["file"])
        assert again["music_words"] == "slow jazz" and len(cloud.asked) == 2
        assert "one sentence: the genre" in asked and "one sentence: the genre" not in json.dumps(cloud.asked[1])
    finally:
        cloud.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_scenes_made_sound_is_heard_and_made_again_until_it_is_right(stand, monkeypatch, tmp_path):
    """53.13: with the listener installed a scene's made sound is heard before it goes into the film - the listener
    (a real process) names what sounds, a real ffmpeg measures where its energy lies, the director's model says
    whether that is what the scene asks for. A sound that is not right is made again by the next seed, three takes
    at most, the best kept; what was heard is kept with the scene. A sound made before the listener came is heard
    as the first take; one heard is not heard twice; when the model does not answer the measure alone judges;
    «Стоп» leaves no takes."""
    from app.hub_client import HubClient
    from hub import director, ffmpeg
    from hub.gateway.openai_compat import OpenAICompatProvider
    from tests.test_autonomous import use
    from tests.test_director import _until
    from tests.test_ffmpeg import _band, _solid, animated_webp
    from tests.test_judge import SeeingCloud, roles
    from tests.test_visual import _install

    hub = stand  # noqa: F811
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    model = tmp_path / "film.gguf"
    model.write_bytes(b"x")
    _install(hub, "film", SILENT, [{"role": "diffusion-model", "repo": "test/Film-GGUF", "path": model.name,
                                    "size": 1, "at": str(model)}])  # fmt: skip
    FakeDrawSwap.filmed = animated_webp([_solid((20, 40, 30 + 3 * k)) for k in range(73)], 64, 64)
    board = {"scenes": [{"mode": "text", "seconds": 3, "words": "Дождь", "prompt": "rain falls", "sound": "rain"},
                        {"mode": "text", "seconds": 3, "words": "Крыша", "prompt": "a roof",
                         "sound": "rain on a roof"}]}  # fmt: skip
    no = {"fits": False, "heard": "музыка", "why": "просили дождь, а слышна музыка"}
    yes = {"fits": True, "heard": "шум дождя", "why": ""}
    cloud = SeeingCloud([json.dumps(a, ensure_ascii=False) for a in (board, no, yes, yes, yes, yes, yes)])

    def effects(since=0):
        return [(a["text"], a["seed"]) for a in hub.asked()[since:] if a["family"] == "moss_soundeffect"]

    def heard(film, n):
        return film["scenes"][n - 1]["heard"].get("made")

    try:
        use(hub, OpenAICompatProvider("studio-cloud", cloud.url, locality="cloud", api_key="k"))
        roles(hub, director="studio-cloud/eye")
        client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
        media, card = hub.app.state.visual.media, hub.app.state.card
        film_id = client.new_film({"idea": "Дождь", "seconds": 6, "checks": False})["film"]["id"]

        def pieces():
            return {p.name for p in media.glob(f"*/film-{film_id}-fx-*.wav")}

        def joined(before=""):
            client.join_film(film_id)
            return _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"] and f["file"] != before)

        def shot():
            client.shoot_film(film_id)
            _until(hub, film_id, lambda f: f["status"] == "ready" and all(s["status"] == "done" for s in f["scenes"]))

        install(hub, "effects")
        client.write_film(film_id)
        _until(hub, film_id, lambda f: f["status"] == "ready")
        shot()
        monkeypatch.setenv("FAKE_AUDIO_WRONG", "1")  # the first scene's sound comes as music
        first = joined()
        assert effects() == [("rain", "7"), ("rain on a roof", "7")] and len(cloud.asked) == 1
        assert [s["heard"] for s in first["scenes"]] == [{}, {}]  # no listener: nothing is heard, as before
        assert _band(program, media / first["file"], 660, 0.5, 2.0) > -45  # music where the rain was asked for

        # the listener comes: the sounds made before are heard as their first takes - the wrong one is made again
        install(hub, "listener")
        before, spent = len(hub.asked()), first["tokens"]
        second = joined(first["file"])
        asked = hub.asked()[before:]
        assert [(a["family"], a.get("seed", "")) for a in asked] == [
            ("ced", ""), ("ced", ""), ("moss_soundeffect", "8"), ("ced", "")]  # fmt: skip
        assert heard(second, 1)["heard"] == "шум дождя" and heard(second, 1)["bad"] == ""
        assert heard(second, 1)["takes"] == 2 and heard(second, 2)["takes"] == 1
        assert {heard(second, n)["file"] for n in (1, 2)} == pieces() and len(pieces()) == 2
        assert not list(media.glob("*/*.take*")) and card.holder == ""
        said = json.dumps(cloud.asked[1]["messages"], ensure_ascii=False)
        assert "Asked to sound like: rain" in said and "- Music: 0.80" in said and "Дождь" in said
        assert "two to five words, in Russian" in said  # the window's language, by its name
        assert len(cloud.asked) == 4 and second["tokens"] > spent  # the director's words are counted to the film
        film = media / second["file"]
        assert _band(program, film, 1760, 0.5, 2.0) > -45 and _band(program, film, 660, 0.5, 2.0) < -50

        before = len(hub.asked())
        third = joined(second["file"])  # joined again: what was heard is not heard twice
        assert len(hub.asked()) == before and len(cloud.asked) == 4 and heard(third, 1) == heard(second, 1)

        # a rain as from an old loudspeaker - no top: three takes, none right, the first stays and says why
        edited = client.update_scene(film_id, 2, {"sound_words": "heavy rain"})["film"]
        assert edited["scenes"][1]["heard"] == {} and edited["scenes"][0]["heard"] != {}
        shot()
        monkeypatch.setenv("FAKE_AUDIO_DULL", str(len(effects()) + 3))
        before = len(hub.asked())
        dull = joined()
        assert effects(before) == [("heavy rain", "7"), ("heavy rain", "8"), ("heavy rain", "9")]
        assert heard(dull, 2)["takes"] == 3 and "глухо" in heard(dull, 2)["bad"] and len(cloud.asked) == 7
        assert heard(dull, 1) == heard(second, 1) and not list(media.glob("*/*.take*"))
        assert len(pieces()) == 2  # the sound before is not the film's any more

        # the director's model does not answer (the cloud has no more to say): the listener's own words, the
        # measure alone - and the joining does not wait for it twice
        monkeypatch.setattr(director, "HEAR_TIMEOUT_S", 0.5)
        client.update_scene(film_id, 1, {"sound_words": "rain on leaves"})
        shot()
        before = len(hub.asked())
        plain = joined()
        assert effects(before) == [("rain on leaves", "7")] and len(cloud.asked) == 8
        assert heard(plain, 1) == {"file": heard(plain, 1)["file"], "heard": "Rain, Rain on surface", "bad": "",
                                   "mark": 3, "takes": 1}  # fmt: skip

        monkeypatch.setenv("FAKE_AUDIO_DULL", "1000")
        monkeypatch.setenv("FAKE_AUDIO_SLEEP", "2")
        client.update_scene(film_id, 1, {"sound_words": "rain on glass"})
        shot()
        before = len(hub.asked())
        client.join_film(film_id)
        deadline = time.monotonic() + 30
        while len(effects(before)) < 2 and time.monotonic() < deadline:  # its second take is being made
            time.sleep(0.05)
        stopped = client.stop_film(film_id)["film"]
        assert (stopped["status"], stopped["error"], stopped["file"]) == ("ready", "", "")
        assert card.holder == "" and not list(media.glob("*/*.take*")) and not list(media.glob("*/*.part.wav"))
        assert len(pieces()) == 3  # a take made is not lost
    finally:
        cloud.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_scenes_own_sound_is_heard_and_gives_way_to_a_made_one_when_it_is_not_right(stand, monkeypatch, tmp_path):
    """53.14, the user's ear («дождь как из старой советской динамики») and word («заменять самому»): the sound a
    video model films with a scene is heard once the scene is filmed - its card says what sounds in it - and not
    twice for one clip. Joined, a film whose scenes all sound right is the film as it was; a scene whose own sound
    is not what was asked for, or is a rain without its top, gets the engine's - made, heard, and put in the
    film in place of its own, which is silent there. A made sound that came out no better is left out: the scene
    keeps its own. A real hub, a real ffmpeg, the engine's stand-in."""
    import subprocess

    from app.hub_client import HubClient
    from hub import ffmpeg
    from hub.gateway.openai_compat import OpenAICompatProvider
    from tests.test_autonomous import use
    from tests.test_director import LTX, _until
    from tests.test_ffmpeg import _band, _said
    from tests.test_judge import SeeingCloud, roles
    from tests.test_visual import _install

    hub = stand  # noqa: F811
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    files = [{"role": role, "repo": "test/Film-GGUF", "path": name, "size": 1, "at": str(tmp_path / name)}
             for role, name in (("diffusion-model", "film.gguf"),
                                ("audio-vae", "film_audio_vae.safetensors"))]  # fmt: skip
    for f in files:
        Path(f["at"]).write_bytes(b"x")
    _install(hub, "film", LTX, files)

    def filmed(name, sound):
        """A clip as the engine films one with sound: 73 frames, its sound - this lavfi graph."""
        subprocess.run([str(program), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=64x64:rate=24", "-f", "lavfi", "-t", "3.0", "-i", sound, "-frames:v", "73",
                        "-ac", "2", "-c:v", "libvpx", "-g", "1", "-c:a", "pcm_s16le", "-f", "matroska",
                        str(tmp_path / name)], check=True, timeout=60)  # fmt: skip
        return (tmp_path / name).read_bytes()

    tone = "sine=frequency={hz}:sample_rate=48000"
    rain = filmed("rain.webm", tone.format(hz=1760) + "[a];anoisesrc=color=white:sample_rate=48000:amplitude=0.08"
                  "[b];[a][b]amix=inputs=2:normalize=0")  # fmt: skip
    music, dull = filmed("music.webm", tone.format(hz=660)), filmed("dull.webm", tone.format(hz=1760))
    board = {"scenes": [{"mode": "text", "seconds": 3, "words": "Дождь", "prompt": "rain falls", "sound": "rain"},
                        {"mode": "text", "seconds": 3, "words": "Шаги", "prompt": "a man walks",
                         "sound": "steps in the rain"},
                        {"mode": "text", "seconds": 3, "words": "Крыша", "prompt": "a roof",
                         "sound": "rain on a roof"}]}  # fmt: skip
    no = {"fits": False, "heard": "музыка", "why": "просили шаги под дождём, а слышна музыка"}
    yes = {"fits": True, "heard": "шум дождя", "why": ""}
    cloud = SeeingCloud([json.dumps(a, ensure_ascii=False) for a in (board, yes, yes, yes, no, yes, yes, yes, yes,
                                                                     yes, yes, yes)])  # fmt: skip

    def asked(since=0):
        return [(a["family"], a.get("text", ""), a.get("seed", "")) for a in hub.asked()[since:]]

    def own(film, n):
        return film["scenes"][n - 1]["heard"].get("own")

    try:
        use(hub, OpenAICompatProvider("studio-cloud", cloud.url, locality="cloud", api_key="k"))
        roles(hub, director="studio-cloud/eye")
        client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
        media, card = hub.app.state.visual.media, hub.app.state.card
        install(hub, "effects", "listener")
        film_id = client.new_film({"idea": "Дождь", "seconds": 9, "checks": False})["film"]["id"]

        def shot(only=None):
            client.shoot_film(film_id, only)
            return _until(hub, film_id, lambda f: f["status"] == "ready" and all(s["status"] == "done"
                                                                                  for s in f["scenes"]))  # fmt: skip

        def joined():
            client.join_film(film_id)
            return _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"])

        client.write_film(film_id)
        _until(hub, film_id, lambda f: f["status"] == "ready")
        FakeDrawSwap.filmed = rain
        first = shot()  # each scene is heard as it is filmed: the card says what sounds in it
        assert asked() == [("ced", "", "")] * 3 and len(cloud.asked) == 4
        assert [own(first, n)["heard"] for n in (1, 2, 3)] == ["шум дождя"] * 3
        assert [(own(first, n)["bad"], own(first, n)["mark"]) for n in (1, 2, 3)] == [("", 5)] * 3
        assert own(first, 2)["job"] == first["scenes"][1]["job_id"]
        said = json.dumps(cloud.asked[2]["messages"], ensure_ascii=False)
        assert "Asked to sound like: steps in the rain" in said and "- Rain: 0.64" in said

        whole = joined()  # every scene sounds right: nothing is made, the film is as its scenes are
        assert len(hub.asked()) == 3 and len(cloud.asked) == 4 and whole["error"] == ""
        assert "Audio: aac" in _said(program, "-i", str(media / whole["file"]))
        assert not list(media.glob("*/*.cut.mp4")) and "replaced" not in own(whole, 1)

        FakeDrawSwap.filmed = music  # the second scene filmed anew: music where steps in the rain were asked for
        again = shot(2)
        assert again["file"] == "" and (own(again, 2)["bad"], own(again, 2)["mark"]) == (no["why"], 0)
        assert own(again, 2)["job"] != own(first, 2)["job"] and own(again, 1) == own(first, 1)
        FakeDrawSwap.filmed = dull  # the third: a rain, but as from an old loudspeaker
        again = shot(3)
        assert "глухо" in own(again, 3)["bad"] and own(again, 3)["mark"] == 4 and len(cloud.asked) == 6

        before = len(hub.asked())
        mixed = joined()
        assert asked(before) == [
            ("moss_soundeffect", "steps in the rain", "7"),
            ("moss_soundeffect", "rain on a roof", "7"),
            ("ced", "", ""),
            ("ced", "", ""),
        ]
        assert [own(mixed, n).get("replaced") for n in (1, 2, 3)] == [None, True, True] and card.holder == ""
        assert [mixed["scenes"][n]["heard"]["made"]["mark"] for n in (1, 2)] == [5, 5] and len(cloud.asked) == 8
        film = media / mixed["file"]
        # the first scene keeps its own rain; in the second the music is gone and the made rain is there
        assert _band(program, film, 1760, 0.5, 2.0) > -45 and _band(program, film, 660, 0.5, 2.0) < -50
        assert _band(program, film, 660, 3.5, 2.2) < -50 and _band(program, film, 1760, 3.5, 2.2) > -45
        assert ffmpeg.bands(program, film, at=6.5, seconds=2.2)["air"] > 0.03  # the third has its top now

        # a made sound that is no better - three takes without a top - is left out: the scene keeps its own
        client.update_scene(film_id, 3, {"sound_words": "rain on a tin roof"})
        again = shot()
        assert "глухо" in own(again, 3)["bad"] and "replaced" not in own(again, 3)
        monkeypatch.setenv("FAKE_AUDIO_DULL", "1000")
        before = len(hub.asked())
        kept = joined()
        assert [a for a in asked(before) if a[0] != "ced"] == [
            ("moss_soundeffect", "rain on a tin roof", seed) for seed in ("7", "8", "9")]  # fmt: skip
        made = kept["scenes"][2]["heard"]["made"]
        assert (made["takes"], made["mark"]) == (3, 4) and "глухо" in made["bad"] and len(cloud.asked) == 12
        assert [own(kept, n).get("replaced") for n in (2, 3)] == [True, False]
        film = media / kept["file"]
        assert ffmpeg.bands(program, film, at=6.5, seconds=2.2)["air"] < 0.01
        assert _band(program, film, 1760, 6.5, 2.2) > -45 and _band(program, film, 660, 3.5, 2.2) < -50
        assert not list(media.glob("*/*.take*")) and not list(media.glob("*/*.cut.mp4"))
    finally:
        cloud.close()


def loudness(path, first, seconds):
    """The mean square of a WAV's samples between `first` and `first + seconds`: 0 - silence."""
    with wave.open(str(path), "rb") as sound:
        rate, channels = sound.getframerate(), sound.getnchannels()
        sound.setpos(round(first * rate))
        raw = sound.readframes(round(seconds * rate))
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)[::channels]
    return sum(v * v for v in samples) / max(1, len(samples))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_scene_that_speaks_has_its_narrators_line_said_by_the_person_in_its_frame(stand, monkeypatch, tmp_path):
    """57.4: a scene «говорит в кадре» - when the film is joined, its clip goes through «Губы под голос» first: a
    job of the Studio's queue, the engine (a real process) given the scene's frames and its line - the narrator's
    voice - where the film says it, silence before it, as long as the scene. The clip so made goes into the film in
    the scene's place, its sound its own: the line is mixed in once, with the others. A scene that does not speak
    is not given to it. Joined again - the lips are not made twice; another line - made anew; «Стоп» kills the
    engine. No narrator, no line, the part not here - said before anything begins."""
    import psutil

    from app.hub_client import HubClient, HubError
    from hub import director, ffmpeg
    from tests.test_director import _film_of, _until
    from tests.test_enhance import described
    from tests.test_ffmpeg import _band, _solid, animated_webp
    from tests.test_torch_engines import put_stand
    from tests.test_visual import _install

    hub = stand  # noqa: F811
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    model = tmp_path / "film.gguf"
    model.write_bytes(b"x")
    _install(hub, "film", SILENT, [{"role": "diffusion-model", "repo": "test/Film-GGUF", "path": model.name,
                                    "size": 1, "at": str(model)}])  # a video model that films no sound  # fmt: skip
    FakeDrawSwap.filmed = animated_webp([_solid((20, 40, 30 + 3 * k)) for k in range(73)], 64, 64)  # 73 frames
    install(hub, "voice")
    scenes = [{"mode": "text", "words": w, "prompt": p, "frames": 73, "say": s} for w, p, s in (
        ("Дождь", "rain falls on a street", "Дождь не страшен."), ("Прохожий", "a man walks by", ""),
        ("Продавец", "a seller in a raincoat speaks", "Наш дождевик не промокает."))]  # fmt: skip
    film_id = _film_of(hub, scenes, voice="serena")
    client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
    media, card = hub.app.state.visual.media, hub.app.state.card
    client.shoot_film(film_id)
    shot = _until(hub, film_id, lambda f: f["status"] == "ready" and all(s["status"] == "done" for s in f["scenes"]))
    edited = client.update_scene(film_id, 3, {"speaks": True})["film"]
    assert [s["speaks"] for s in edited["scenes"]] == [False, False, True] and edited["file"] == ""
    assert [s["status"] for s in edited["scenes"]] == ["done"] * 3  # the clips stay
    with pytest.raises(HubError, match="«Губы под голос» не установлено"):
        client.join_film(film_id)
    assert hub.asked() == [] and client.film(film_id)["film"]["status"] == "ready"

    torch = tmp_path / "torch"
    server = put_stand(hub, monkeypatch, torch)  # the PyTorch engines' stand-ins: MuseTalk's part put in
    try:
        assert hub.post("/v1/enhance/parts/lips", json={"licence_ok": True}).status_code == 200
        deadline = time.monotonic() + 60
        while next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "lips")["state"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        joins, join = [], ffmpeg.join

        def joined_from(program, clips, target, **how):
            joins.append([clip for clip, _seconds in clips])
            return join(program, clips, target, **how)

        monkeypatch.setattr(ffmpeg, "join", joined_from)

        def lipped():
            return [r for r in hub.engine() if "--voice" in r["args"]]

        client.join_film(film_id)
        joined = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"], timeout=60)
        assert joined["error"] == "" and card.holder == "" and card.free.is_set()
        assert len(lipped()) == 1  # the third scene alone
        heard = torch / "voice-0.wav"  # what the engine heard: the line 0.15 s into the scene, 16 kHz, as long
        with wave.open(str(heard), "rb") as w:
            assert (w.getframerate(), w.getnchannels()) == (16000, 1)
            assert abs(w.getnframes() / 16000 - 73 / 24) < 0.01
        assert loudness(heard, 0.0, 0.12) < 10 and loudness(heard, 0.3, 1.5) > 1e5
        lips = joined["scenes"][2]["lips"]
        made = hub.get(f"/v1/visual/jobs/{lips['job']}").json()["job"]
        assert made["status"] == "done" and made["enhance"]["job"] == shot["scenes"][2]["job_id"]
        assert made["enhance"]["lips"]["voice"] == "file" and made["enhance"]["lips"]["own"] is True
        assert not described(media / made["file"])["sound"]  # its own sound - none: the line is not in it
        assert joins[-1][2] == media / made["file"] and joins[-1][0] != joins[-1][2]  # in the scene's place
        assert not list(media.rglob("voice-*"))  # the line's file copied for the job - gone after it
        assert lips["job"] in hub.app.state.db.call(director.kept)  # the cleanup leaves it while the film is there
        film = media / joined["file"]
        assert _band(program, film, 220, 6.4, 1.5) > -30 and _band(program, film, 220, 3.5, 2.0) < -42  # said once
        asked = len(hub.asked())

        client.join_film(film_id)  # joined again: the lips are not made twice
        again = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"] and f["file"] != joined["file"])
        assert len(lipped()) == 1 and again["scenes"][2]["lips"] == lips and len(hub.asked()) == asked
        assert joins[-1][2] == media / made["file"]

        client.update_scene(film_id, 3, {"say": "Этот дождевик держит любой ливень."})
        client.join_film(film_id)
        other = _until(hub, film_id, lambda f: f["status"] == "ready" and f["file"], timeout=60)
        assert len(lipped()) == 2 and other["scenes"][2]["lips"]["job"] != lips["job"] and other["error"] == ""

        child = tmp_path / "child.pid"  # «Стоп» while the lips are made: the engine and its child killed
        monkeypatch.setenv("FAKE_ENGINE_SLOW", "60")
        monkeypatch.setenv("FAKE_ENGINE_CHILD", str(child))
        client.update_scene(film_id, 3, {"say": "Совсем другая строка."})
        assert client.join_film(film_id)["film"]["status"] == "joining"
        deadline = time.monotonic() + 30
        while not child.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        stopped = client.stop_film(film_id)["film"]
        assert time.monotonic() - started < 15
        assert (stopped["status"], stopped["error"], stopped["file"]) == ("ready", "", "")
        assert not psutil.pid_exists(int(child.read_text())) and not list(media.glob("*/*.cut.mp4"))
        deadline = time.monotonic() + 10
        while card.holder and time.monotonic() < deadline:
            time.sleep(0.05)
        assert card.holder == ""
        monkeypatch.delenv("FAKE_ENGINE_SLOW")

        client.update_scene(film_id, 2, {"speaks": True})  # a scene that speaks and has no line
        with pytest.raises(HubError, match="сцена 2 «говорит в кадре», а реплики у неё нет"):
            client.join_film(film_id)
        client.update_scene(film_id, 2, {"speaks": False})
        client.update_film(film_id, {"voice": ""})  # no narrator
        with pytest.raises(HubError, match="диктора у фильма нет"):
            client.join_film(film_id)
    finally:
        server.shutdown()


def _piece(hub, piece_id, *statuses, timeout=20.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = next((p for p in hub.get("/v1/sound/pieces").json()["pieces"] if p["id"] == piece_id), None)
        if found is not None and found["status"] in statuses:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{piece_id} is still {found}")


def test_the_studios_pieces_of_sound_are_made_listed_stopped_and_removed(stand, monkeypatch):
    """«Стабилизация», portion 5 (the user's complaint 15 of 2026-11-27, «Нет в Студии "Аудио"»): the Studio asks
    the hub for a voice reading a text, music, a sound - each a WAV of its own, made by the engine's program (a
    real process) in the queue, the card held meanwhile and given after; listed newest first, its file served; the
    same words again - another seed; ✕ on one going on stops its program, on one waiting takes it out; a piece
    removed - its file too; pieces a stopped hub left going are failed when it starts. A part not here - said with
    «Скачать». Before: no such thing - sound only in a film."""
    hub = stand  # noqa: F811
    card = hub.app.state.card
    refused = hub.post("/v1/sound/pieces", json={"kind": "music", "words": "calm piano"})
    assert refused.status_code == 422 and "Скачать" in refused.json()["detail"]
    install(hub, "voice", "music", "effects")
    for wrong in ({"kind": "voice", "words": "  "}, {"kind": "voice", "words": "Привет", "voice": "nobody"},
                  {"kind": "music", "words": "piano", "seconds": 2}, {"kind": "effect", "words": "rain", "seconds": 31},
                  {"kind": "song", "words": "la"}):  # fmt: skip
        assert hub.post("/v1/sound/pieces", json=wrong).status_code == 422, wrong

    voice = hub.post("/v1/sound/pieces", json={"kind": "voice", "words": "Привет, это проверка.",
                                               "voice": "serena"}).json()["piece"]  # fmt: skip
    music = hub.post("/v1/sound/pieces", json={"kind": "music", "words": "calm piano", "seconds": 6}).json()["piece"]
    rain = hub.post("/v1/sound/pieces", json={"kind": "effect", "words": "rain on a roof", "seconds": 2}).json()
    assert voice["status"] == "queued" and music["seconds"] == 6.0
    for made in (voice, music, rain["piece"]):
        assert _piece(hub, made["id"], "done", "failed")["status"] == "done", made
    assert card.holder == "" and card.free.is_set()  # given after
    listed = hub.get("/v1/sound/pieces").json()["pieces"]
    assert [p["id"] for p in listed] == [rain["piece"]["id"], music["id"], voice["id"]]  # the newest first
    by_id = {p["id"]: p for p in listed}
    assert by_id[voice["id"]]["seconds"] == pytest.approx(len("Привет, это проверка.") / 12, abs=0.01)
    assert by_id[music["id"]]["seconds"] == pytest.approx(6.0, abs=0.01) and by_id[music["id"]]["took"] >= 0
    wav = hub.get(f"/v1/sound/pieces/{music['id']}/file")
    assert wav.status_code == 200 and wav.headers["content-type"] == "audio/wav" and wav.content[:4] == b"RIFF"
    asked = hub.asked()[-3:]
    assert [(a["family"], a["seed"]) for a in asked] == [("qwen3_tts", "7"), ("ace_step", "7"),
                                                        ("moss_soundeffect", "7")]  # fmt: skip
    assert asked[0]["voice"] == "serena" and asked[1]["seconds"] == 6.0 and asked[2]["seconds"] == 2.0
    told = [p["piece"]["status"] for kind, p in hub.seen if kind == "sound.piece" and p["piece"]["id"] == music["id"]]
    assert told[0] == "queued" and told[-2:] == ["running", "done"]

    again = hub.post("/v1/sound/pieces", json={"kind": "music", "words": "calm piano", "seconds": 6}).json()["piece"]
    assert _piece(hub, again["id"], "done", "failed")["status"] == "done"
    assert (hub.asked()[-1]["family"], hub.asked()[-1]["seed"]) == ("ace_step", "8")  # another piece, not the same

    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "30")
    long = hub.post("/v1/sound/pieces", json={"kind": "voice", "words": "Долгая речь.", "voice": "ryan"}).json()
    behind = hub.post("/v1/sound/pieces", json={"kind": "effect", "words": "wind", "seconds": 1}).json()["piece"]
    _piece(hub, long["piece"]["id"], "running")
    assert hub.post(f"/v1/sound/pieces/{behind['id']}/cancel").json()["piece"]["status"] == "cancelled"
    began = time.monotonic()
    hub.post(f"/v1/sound/pieces/{long['piece']['id']}/cancel")
    stopped = _piece(hub, long["piece"]["id"], "cancelled", "failed", "done")
    assert stopped["status"] == "cancelled" and stopped["file"] == "" and time.monotonic() - began < 10
    assert not [a for a in hub.asked() if a["text"] == "wind"] and card.holder == ""  # never made
    monkeypatch.setenv("FAKE_AUDIO_SLEEP", "0")

    path = hub.app.state.pieces.media / by_id[voice["id"]]["file"]
    assert path.is_file()
    assert hub.delete(f"/v1/sound/pieces/{voice['id']}").status_code == 200
    assert not path.exists() and hub.get(f"/v1/sound/pieces/{voice['id']}/file").status_code == 404
    assert voice["id"] not in [p["id"] for p in hub.get("/v1/sound/pieces").json()["pieces"]]
    assert hub.delete(f"/v1/sound/pieces/{voice['id']}").status_code == 404

    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO sound_pieces (id, kind, words, status) VALUES ('left', 'music', 'x', 'running')"))  # fmt: skip
    assert hub.portal.call(hub.app.state.pieces.start) == 1
    assert _piece(hub, "left", "failed")["error"] == "хаб перезапускался — звук не сделан"
