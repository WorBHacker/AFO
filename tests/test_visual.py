"""Pictures and videos (D55, stage 29): a picture model's trial picture through llama-swap, and a blank picture told
from a real one - stable-diffusion.cpp on Vulkan can answer «success» with a flat grey one (#1220)."""

import asyncio
import base64
import json
import random
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import visual
from hub.server import create_app
from hub.settings import HubSettings


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _filtered(row: bytes, prev: bytes, kind: int, bpp: int) -> bytes:
    """A row as PNG's filter `kind` writes it (the decoder must undo each)."""
    out = bytearray(len(row))
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if kind == 0:
            pred = 0
        elif kind == 1:
            pred = a
        elif kind == 2:
            pred = b
        elif kind == 3:
            pred = (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
        out[i] = (x - pred) & 255
    return bytes([kind]) + bytes(out)


def png(width: int, height: int, pixel, *, kinds=(0,)) -> bytes:
    """An 8-bit RGB PNG; row y written with filter kinds[y % len(kinds)]."""
    rows = [bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height)]
    prev = bytes(width * 3)
    raw = b""
    for y, row in enumerate(rows):
        raw += _filtered(row, prev, kinds[y % len(kinds)], 3)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def gradient(x, y):
    return (x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256)


def test_a_pngs_pixels_through_each_filter():
    for kinds in ((0,), (1,), (2,), (3,), (4,), (0, 1, 2, 3, 4)):
        width, height, channels, pixels = visual.pixels(png(24, 10, gradient, kinds=kinds))
        assert (width, height, channels) == (24, 10, 3)
        assert bytes(pixels) == b"".join(bytes(c for x in range(24) for c in gradient(x, y)) for y in range(10)), kinds


def test_a_blank_picture_is_told_from_a_real_one():
    rnd = random.Random(42)
    noise = png(64, 64, lambda x, y: (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    assert not visual.blank(noise)  # a real picture keeps much of its size: not even decoded
    assert visual.blank(png(64, 64, lambda x, y: (128, 128, 128), kinds=(4,)))  # the grey «success»
    assert visual.blank(png(64, 64, lambda x, y: (0, 0, 0)))  # NaN: black
    assert not visual.blank(png(64, 64, gradient, kinds=(1, 2)))  # compresses well, but it has things in it
    assert visual.blank(b"not a png")


def _chunks(data: bytes) -> list[bytes]:
    """A PNG's chunk kinds, each one's CRC checked."""
    pos, kinds = 8, []
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        assert struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0] == zlib.crc32(kind + body), kind
        kinds.append(kind)
        pos += 12 + length
    return kinds


def test_the_prompt_and_seed_go_into_the_picture():
    """29.4б: a picture keeps what it was drawn from - in «parameters», as stable-diffusion.cpp and A1111 write it."""
    assert visual.parameters(PICTURE) == ""
    text = "рыжая лиса в снегу\nSteps: 4, Seed: 7"
    described = visual.with_parameters(PICTURE, text)
    assert visual.parameters(described) == text and visual.pixels(described) == visual.pixels(PICTURE)
    assert _chunks(described) == [b"IHDR", b"iTXt", b"IDAT", b"IEND"]  # not Latin-1: UTF-8, as PNG has it
    plain = visual.with_parameters(described, "a fox\nSeed: 7")
    assert visual.parameters(plain) == "a fox\nSeed: 7" and _chunks(plain) == [b"IHDR", b"tEXt", b"IDAT", b"IEND"]
    # stable-diffusion.cpp puts its UTF-8 into a tEXt (Latin-1 by PNG's rules): read as UTF-8, written back right
    engine = PICTURE[:33] + _chunk(b"tEXt", b"parameters\x00" + text.encode()) + PICTURE[33:]
    assert visual.parameters(engine) == text
    assert _chunks(visual.with_parameters(engine, text)) == [b"IHDR", b"iTXt", b"IDAT", b"IEND"]
    assert visual.with_parameters(b"not a png", text) == b"not a png"


def _swap(answer):
    def handler(request):
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert body["model"] == "pictures" and body["size"] == "512x512" and body["prompt"] == visual.TRIAL_PROMPT
        return answer(body)

    return httpx.MockTransport(handler)


def test_the_trial_picture():
    rnd = random.Random(1)
    real = png(32, 32, lambda x, y: (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    grey = png(32, 32, lambda x, y: (128, 128, 128))

    def run(answer):
        return asyncio.run(visual.trial("http://swap", "pictures", transport=_swap(answer)))

    good = run(lambda b: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(real).decode()}]}))
    assert good["ok"] and good["size"] == 512 and good["seconds"] >= 0
    flat = run(lambda b: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(grey).decode()}]}))
    assert not flat["ok"] and "пустой" in flat["error"]
    crashed = run(lambda b: httpx.Response(502, text="upstream command exited prematurely"))
    assert not crashed["ok"] and "exited prematurely" in crashed["error"]
    empty = run(lambda b: httpx.Response(200, json={"data": []}))
    assert not empty["ok"] and "без картинки" in empty["error"]


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_the_trial_picture_without_an_answer(error):
    def handler(request):
        raise error

    result = asyncio.run(visual.trial("http://swap", "pictures", transport=httpx.MockTransport(handler)))
    assert not result["ok"] and type(error).__name__ in result["error"]


# --- the ladder (29.4б): a smaller size, then a lighter model; the rung that came out remembered ---

FLUX = {"model_id": "flux", "name": "FLUX", "tier": "main", "size": 1024}
SDXS = {"model_id": "sdxs", "name": "SDXS", "tier": "light", "size": 512}
ZIMG = {"model_id": "zimg", "name": "Z", "tier": "quality", "size": 1024}


def _rungs(*args):
    return [(r["model_id"], r["width"], r["height"], r["rung"]) for r in visual.rungs(*args)]


def test_a_size_in_a_sources_shape():
    """29.6б: drawn from a picture, the size follows its shape - as many pixels as the model's own side squared."""
    from common.picture_sizes import shaped

    assert shaped(1024, 1024, 1024) == (1024, 1024) and shaped(32, 32, 512) == (512, 512)
    assert shaped(1920, 1080, 1024) == (1344, 768) and shaped(1080, 1920, 1024) == (768, 1344)
    assert shaped(4000, 1000, 512) == (1024, 256)  # very wide: no side under 256


def test_a_videos_size_and_frames():
    """29.8: Wan2.2-TI2V-5B's sizes - 832×480 on most cards, its own 1280×704 with «Качественнее» (29.1); frames
    4k+1 at 24 a second."""
    from common.picture_sizes import frames_for, video_size

    assert video_size("wide", False) == (832, 480) and video_size("tall", False) == (480, 832)
    assert video_size("wide", True) == (1280, 704) and video_size("square", False) == (640, 640)
    assert [frames_for(s) for s in (1, 2, 3)] == [25, 49, 73] and frames_for(0.5) == 13


def test_the_ladder_goes_smaller_then_lighter():
    models = [SDXS, FLUX, ZIMG]
    assert _rungs(FLUX, 1024, 1024, models, {}) == [
        ("flux", 1024, 1024, "asked"), ("flux", 512, 512, "smaller"),
        ("sdxs", 512, 512, "lighter"), ("sdxs", 256, 256, "smaller")]  # fmt: skip
    # a wide one keeps its shape; a side under 256 is no rung
    assert _rungs(FLUX, 1344, 768, models, {}) == [("flux", 1344, 768, "asked"), ("flux", 672, 384, "smaller"),
                                                   ("sdxs", 672, 384, "lighter")]  # fmt: skip
    # at most MAX_TRIES
    assert _rungs(ZIMG, 1024, 1024, models, {}) == [("zimg", 1024, 1024, "asked"), ("zimg", 512, 512, "smaller"),
                                                    ("flux", 1024, 1024, "lighter"), ("flux", 512, 512, "smaller"),
                                                    ("sdxs", 512, 512, "lighter")]  # fmt: skip
    assert _rungs(SDXS, 256, 256, models, {}) == [("sdxs", 256, 256, "asked")]
    # remembered here: the size that came out (as many pixels, the shape kept), the model that did
    assert _rungs(FLUX, 1344, 768, models, {"flux": {"pixels": 512 * 512}}) == [
        ("flux", 672, 384, "asked"), ("sdxs", 672, 384, "lighter")]  # fmt: skip
    assert _rungs(FLUX, 1024, 1024, models, {"flux": {"instead": "sdxs"}}) == [
        ("sdxs", 512, 512, "asked"), ("sdxs", 256, 256, "smaller")]  # fmt: skip
    for odd in ({"instead": "zimg"}, {"instead": "gone"}):  # not lighter, not here: not followed
        assert _rungs(FLUX, 1024, 1024, models, {"flux": odd})[0] == ("flux", 1024, 1024, "asked")


def test_the_processor_is_the_last_rung():
    """29.4в: where a model has a block on the processor's build, the lightest one there is the last rung - no more
    pixels than CPU_PIXELS; remembered, a picture begins there."""
    flux, sdxs = {**FLUX, "cpu_id": "flux-cpu"}, {**SDXS, "cpu_id": "sdxs-cpu"}
    assert _rungs(flux, 1024, 1024, [sdxs, flux], {})[-2:] == [("sdxs", 256, 256, "smaller"),
                                                              ("sdxs-cpu", 512, 512, "cpu")]  # fmt: skip
    zimg = {**ZIMG, "cpu_id": "zimg-cpu"}
    assert len(_rungs(zimg, 1024, 1024, [sdxs, flux, zimg], {})) == visual.MAX_TRIES + 1
    assert _rungs(flux, 1344, 768, [flux], {}) == [("flux", 1344, 768, "asked"), ("flux", 672, 384, "smaller"),
                                                   ("flux-cpu", 672, 384, "cpu")]  # fmt: skip
    assert _rungs(flux, 1344, 768, [flux], {"flux": {"instead": "flux-cpu"}}) == [("flux-cpu", 672, 384, "asked")]
    on_cpu = visual.on_cpu([sdxs, flux, FLUX])
    assert [(m["model_id"], m["of"]) for m in on_cpu] == [("sdxs-cpu", "sdxs"), ("flux-cpu", "flux")]
    assert _rungs(on_cpu[1], 1024, 1024, [sdxs, flux], {}) == [("flux-cpu", 512, 512, "asked")]  # asked for itself
    tries = [{"model_id": m, "width": 512, "height": 512, "rung": r, "ok": ok} for m, r, ok in (
        ("flux", "asked", False), ("sdxs", "lighter", False), ("sdxs-cpu", "cpu", True))]  # fmt: skip
    assert visual.learned(tries, "flux") == {"flux": {"instead": "sdxs-cpu"}, "sdxs": {"instead": "sdxs-cpu"}}


def test_what_the_ladder_learns():
    def tried(*rows):
        return [{"model_id": m, "width": w, "height": h, "rung": r, "ok": ok} for m, w, h, r, ok in rows]

    assert visual.learned(tried(("flux", 1024, 1024, "asked", True)), "flux") == {}
    assert visual.learned(tried(("flux", 1024, 1024, "asked", False), ("flux", 512, 512, "smaller", True)),
                          "flux") == {"flux": {"pixels": 512 * 512}}  # fmt: skip
    lighter = tried(("flux", 1024, 1024, "asked", False), ("flux", 512, 512, "smaller", False),
                    ("sdxs", 512, 512, "lighter", True))  # fmt: skip
    assert visual.learned(lighter, "flux") == {"flux": {"instead": "sdxs"}}
    # started from a remembered model that no longer comes out: the one asked for goes on to the next
    assert visual.learned(lighter, "zimg") == {"flux": {"instead": "sdxs"}, "zimg": {"instead": "sdxs"}}
    # nothing came out: nothing learned
    assert visual.learned(tried(("flux", 1024, 1024, "asked", False), ("flux", 512, 512, "smaller", False)),
                          "flux") == {}  # fmt: skip


# --- the pictures people ask for (29.4): a hub, and llama-swap with a picture model behind it ---


class FakeDrawSwap(BaseHTTPRequestHandler):
    """llama-swap with sd-server behind it, as the hub's pictures use it: a synchronous txt2img drawn in steps, its
    progress through /upstream, «Стоп» as unloading the model (the picture in flight ends with 502)."""

    steps_s = 0.05  # a step's time
    answer: ClassVar = "picture"  # picture | blank | fail - or what a request gets, by its body
    video: ClassVar[list] = []  # the videos asked for (29.8)
    video_polls = 3  # a video's job is done at this look
    polls = 0
    asked: ClassVar[list] = []
    unloaded: ClassVar[list] = []
    step = total = 0
    stopped = False

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cls = FakeDrawSwap
        if "/sdcpp/v1/jobs/" in self.path:  # a video's job (29.8): generating for `video_polls` looks, then done
            if cls.stopped:
                self._send(502, {"error": "upstream exited"})
                return
            cls.polls += 1
            if cls.polls < cls.video_polls:
                self._send(200, {"id": "job_v", "status": "generating", "result": None})
                return
            self._send(200, {"id": "job_v", "status": "completed", "result": {
                "b64_json": base64.b64encode(WEBP).decode(), "mime_type": "image/webp", "output_format": "webp",
                "fps": 24, "frame_count": cls.video[-1]["video_frames"]}})  # fmt: skip
            return
        if self.path.startswith("/upstream/") and self.path.endswith("/sdapi/v1/progress"):
            cls = FakeDrawSwap
            self._send(200, {"progress": cls.step / cls.total if cls.total else 0, "current_image": "jpeg" * 3,
                             "state": {"sampling_step": cls.step, "sampling_steps": cls.total}})  # fmt: skip
        else:
            self._send(200, {"data": []})  # /v1/models: no chat models

    def do_POST(self):
        cls = FakeDrawSwap
        if self.path.startswith("/api/models/unload/"):
            cls.unloaded.append(self.path.rsplit("/", 1)[1])
            cls.stopped = True
            self._send(200, {"msg": "ok"})
            return
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path.endswith("/sdcpp/v1/vid_gen"):  # a video: accepted at once, drawn in the background (29.8)
            cls.video.append({**body, "path": self.path})
            cls.polls, cls.stopped = 0, False
            self._send(202, {"id": "job_v", "kind": "vid_gen", "status": "queued", "poll_url": "/sdcpp/v1/jobs/job_v"})
            return
        cls.asked.append({**body, "path": self.path} if self.path.endswith("/img2img") else body)
        cls.stopped, cls.total = False, body["steps"]
        for step in range(1, cls.total + 1):
            cls.step = step
            time.sleep(cls.steps_s)
            if cls.stopped:
                self._send(502, {"error": "upstream exited"})
                return
        cls.step = cls.total = 0
        answer = cls.answer(body) if callable(cls.answer) else cls.answer
        if answer == "fail":
            self._send(500, {"error": "CUDA error: out of memory"})
            return
        picture = PICTURE if answer == "picture" else GREY
        self._send(200, {"images": [base64.b64encode(picture).decode()], "info": "{}"})


_rnd = random.Random(5)
PICTURE = png(32, 32, lambda x, y: (_rnd.randrange(256), _rnd.randrange(256), _rnd.randrange(256)))
GREY = png(32, 32, lambda x, y: (128, 128, 128))
WEBP = b"RIFF" + struct.pack("<I", 30) + b"WEBPVP8X" + bytes(22)  # what sd-server's video comes as (29.8 live)
WAN_FLAGS = ["--cfg-scale", "1", "--sampling-method", "euler", "--steps", "4", "--flow-shift", "5"]
CLIP_PLAN = {"engine": "sd", "set": "clip", "kind": "video", "tier": "video", "size": 832, "flags": WAN_FLAGS}
DRAW_PLAN = {"engine": "sd", "set": "draw", "tier": "main", "size": 512, "flags": ["--cfg-scale", "1", "--steps", "4"]}


@pytest.fixture
def hub(monkeypatch):
    FakeDrawSwap.steps_s, FakeDrawSwap.answer, FakeDrawSwap.asked, FakeDrawSwap.unloaded = 0.05, "picture", [], []
    FakeDrawSwap.video, FakeDrawSwap.video_polls, FakeDrawSwap.polls = [], 3, 0
    FakeDrawSwap.step = FakeDrawSwap.total = 0
    swap = ThreadingHTTPServer(("127.0.0.1", 0), FakeDrawSwap)
    threading.Thread(target=swap.serve_forever, daemon=True).start()
    monkeypatch.setattr(visual, "STEP_S", 0.05)
    monkeypatch.setattr(visual, "VIDEO_STEP_S", 0.05)
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url=f"http://127.0.0.1:{swap.server_port}",
                                 status_interval_s=0, process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.app.state.installer.machine = lambda: {"build": "cuda12", "pictures": "main", "quality": False}
        seen = client.seen = []
        publish = app.state.bus.publish

        async def recorded(kind, payload=None, **kw):
            seen.append((kind, payload))
            return await publish(kind, payload, **kw)

        monkeypatch.setattr(app.state.bus, "publish", recorded)
        yield client
    swap.shutdown()


def _install(hub, model_id="draw", plan=DRAW_PLAN):
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO model_installs (id, repo, quant, files, folder, model_id, name, status, plan) VALUES "
        "(?, 'test/Draw-GGUF', 'Q4_0', '[]', 'x', ?, 'Draw', 'done', ?)",
        (model_id, model_id, json.dumps(plan))))  # fmt: skip


def _wait(hub, job_id, *statuses, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = hub.get(f"/v1/visual/jobs/{job_id}").json()["job"]
        if found["status"] in statuses:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{job_id}: still {found['status']} ({found['error']})")


def test_a_picture_asked_for_drawn_shown_as_it_goes_and_kept(hub):
    nothing = hub.post("/v1/visual/images", json={"prompt": "a fox"})
    assert nothing.status_code == 404 and "Установка моделей" in nothing.json()["detail"]
    _install(hub)
    _install(hub, "tiny", {**DRAW_PLAN, "set": "tiny", "tier": "light", "flags": ["--steps", "1"]})
    assert hub.get("/v1/visual/models").json()["default"] == "draw"  # the main one: this computer draws it fast
    assert hub.post("/v1/visual/images", json={"prompt": "a fox", "width": 500}).status_code == 422

    job = hub.post("/v1/visual/images", json={"prompt": "a red fox in snow", "seed": 7}).json()["job"]
    assert (job["status"], job["model_id"], job["width"], job["height"], job["seed"]) == ("queued", "draw", 512, 512, 7)
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert FakeDrawSwap.asked[-1] == {"model": "draw", "prompt": "a red fox in snow", "negative_prompt": "",
                                      "width": 512, "height": 512, "seed": 7, "steps": 4, "cfg_scale": 1.0,
                                      "batch_size": 1}  # fmt: skip
    picture = hub.get(f"/v1/visual/jobs/{job['id']}/file")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    assert visual.pixels(picture.content) == visual.pixels(PICTURE)  # and what it was drawn from, in the file:
    assert visual.parameters(picture.content).startswith("a red fox in snow\nSteps: 4, CFG scale: 1.0, Seed: 7, ")
    assert done["file"].endswith(f"/{job['id']}.png") and done["tries"][0]["ok"] and done["seconds"] >= 0
    steps = [p for kind, p in hub.seen if kind == "visual.progress"]
    assert steps and steps[0]["id"] == job["id"] and steps[0]["steps"] == 4 and steps[0]["preview"]
    changes = [p["job"]["status"] for kind, p in hub.seen if kind == "visual.changed"]
    assert changes[-3:] == ["queued", "running", "done"]
    light = hub.post("/v1/visual/images", json={"prompt": "a fox", "model": "tiny", "seed": -1}).json()["job"]
    assert light["width"] == 512 and light["seed"] >= 0  # a new seed
    _wait(hub, light["id"], "done", "failed")
    assert FakeDrawSwap.asked[-1]["steps"] == 1 and FakeDrawSwap.asked[-1]["model"] == "tiny"
    assert [j["id"] for j in hub.get("/v1/visual/jobs").json()["jobs"]][:2] == [light["id"], job["id"]]


def test_stop_unloads_the_model_and_the_picture_ends(hub):
    _install(hub)
    FakeDrawSwap.steps_s = 0.5  # a long picture
    job = hub.post("/v1/visual/images", json={"prompt": "a fox", "seed": 1}).json()["job"]
    _wait(hub, job["id"], "running")
    time.sleep(0.3)
    hub.post(f"/v1/visual/jobs/{job['id']}/cancel")
    stopped = _wait(hub, job["id"], "cancelled", "failed", "done", timeout=5.0)
    assert stopped["status"] == "cancelled" and FakeDrawSwap.unloaded == ["draw"]
    assert len(FakeDrawSwap.asked) == 1  # stopped: no rung lower
    assert hub.get(f"/v1/visual/jobs/{job['id']}/file").status_code == 404


@pytest.mark.parametrize(("answer", "words"), [("blank", "пустой"), ("fail", "движок ответил 500")])
def test_a_picture_that_does_not_come_out_says_why(hub, answer, words):
    _install(hub)
    FakeDrawSwap.answer = answer
    job = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    failed = _wait(hub, job["id"], "failed", "done")
    assert failed["status"] == "failed" and words in failed["error"]
    assert [(t["width"], t["rung"], t["ok"]) for t in failed["tries"]] == [(512, "asked", False),
                                                                          (256, "smaller", False)]  # fmt: skip
    models = hub.get("/v1/visual/models").json()["models"]
    assert (models[0]["pixels"], models[0]["instead"]) == (0, "")  # nothing came out: nothing remembered


def test_a_picture_that_fails_goes_a_rung_lower_and_the_rung_is_remembered(hub):
    _install(hub)
    _install(hub, "tiny", {**DRAW_PLAN, "set": "tiny", "tier": "light", "flags": ["--steps", "1"]})
    FakeDrawSwap.answer = lambda body: "fail" if body["model"] == "draw" and body["width"] > 256 else "picture"
    job = hub.post("/v1/visual/images", json={"prompt": "a fox", "seed": 3}).json()["job"]
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done" and (done["model_id"], done["width"], done["height"]) == ("draw", 256, 256)
    assert [(t["model_id"], t["width"], t["rung"], t["ok"]) for t in done["tries"]] == [
        ("draw", 512, "asked", False), ("draw", 256, "smaller", True)]  # fmt: skip
    assert "500" in done["tries"][0]["error"] and "Size: 256x256" in visual.parameters(
        hub.get(f"/v1/visual/jobs/{job['id']}/file").content)  # fmt: skip
    models = {m["model_id"]: m for m in hub.get("/v1/visual/models").json()["models"]}
    assert (models["draw"]["pixels"], models["tiny"]["pixels"]) == (256 * 256, 0)
    asked = len(FakeDrawSwap.asked)
    again = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    assert _wait(hub, again["id"], "done", "failed")["status"] == "done"
    assert len(FakeDrawSwap.asked) == asked + 1 and FakeDrawSwap.asked[-1]["width"] == 256  # began where it came out

    FakeDrawSwap.answer = lambda body: "blank" if body["model"] == "draw" else "picture"
    third = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done", "failed")
    assert third["status"] == "done" and (third["model_id"], third["width"]) == ("tiny", 512)
    assert [(t["model_id"], t["width"], t["rung"]) for t in third["tries"]] == [("draw", 256, "asked"),
                                                                               ("tiny", 512, "lighter")]  # fmt: skip
    models = hub.get("/v1/visual/models").json()["models"]
    assert {m["model_id"]: m["instead"] for m in models} == {"draw": "tiny", "tiny": ""}
    asked = len(FakeDrawSwap.asked)
    fourth = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done")
    assert len(FakeDrawSwap.asked) == asked + 1 and fourth["model_id"] == "tiny"  # straight to the one that comes out

    # another computer (data moved over, another card): what this one learned does not hold there
    hub.app.state.installer.machine = lambda: {"build": "vulkan", "card": "Radeon", "pictures": "main"}
    models = {m["model_id"]: m for m in hub.get("/v1/visual/models").json()["models"]}
    assert (models["draw"]["pixels"], models["draw"]["instead"]) == (0, "")


def test_where_the_card_draws_nothing_the_processor_does(hub):
    """29.4в: blank on the card at every rung - the model's block on the processor's build draws it; remembered, the
    next picture goes there at once; the window may ask for it by its own name."""
    _install(hub, plan={**DRAW_PLAN, "cpu": "draw-cpu"})
    FakeDrawSwap.answer = lambda body: "picture" if body["model"] == "draw-cpu" else "blank"
    job = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done" and done["model_id"] == "draw-cpu"
    assert [(t["model_id"], t["rung"], t["ok"]) for t in done["tries"]] == [
        ("draw", "asked", False), ("draw", "smaller", False), ("draw-cpu", "cpu", True)]  # fmt: skip
    assert FakeDrawSwap.asked[-1]["steps"] == 4  # the model's own steps on the processor too
    models = hub.get("/v1/visual/models").json()["models"]
    assert [(m["model_id"], m["cpu_id"], m["instead"]) for m in models] == [("draw", "draw-cpu", "draw-cpu")]
    asked = len(FakeDrawSwap.asked)
    again = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done")
    assert len(FakeDrawSwap.asked) == asked + 1 and again["model_id"] == "draw-cpu"
    by_name = hub.post("/v1/visual/images", json={"prompt": "a fox", "model": "draw-cpu", "width": 256,
                                                  "height": 256}).json()["job"]  # fmt: skip
    assert _wait(hub, by_name["id"], "done")["model_id"] == "draw-cpu" and FakeDrawSwap.asked[-1]["width"] == 256


def _chat(hub):
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    said = hub.post(f"/v1/conversations/{conv}/messages", json={"content": "нарисуй лису", "model_id": "fake/echo"})
    assert said.status_code == 202
    return conv


def _finished(hub, conv, timeout=20.0, *, stopped=False):
    """The chat's messages once its answer ended: not busy, the last one the model's words (`stopped`: a result)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = hub.get(f"/v1/conversations/{conv}").json()
        last = found["messages"][-1]
        said = last["role"] == "assistant" and all(b["type"] != "tool_call" for b in last["content"])
        if not found["busy"] and (said or (stopped and last["role"] == "tool")):
            return found["messages"]
        time.sleep(0.05)
    raise AssertionError(f"{conv}: still answering")


class OncePerTurn:
    """A chat model that asks for one scripted call a turn, then answers in words once its result came."""

    name = "fake"

    def __init__(self, steps):
        from tests.test_tools import ToolProvider

        self.inner = ToolProvider(steps)
        self.models, self.calls = self.inner.models, self.inner.calls

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        from hub.gateway.base import StreamEvent

        if messages and messages[-1].get("role") == "tool":
            self.calls.append((messages, tools))
            yield StreamEvent("content", "Готово")
            yield StreamEvent("end", usage={"input_tokens": 20, "output_tokens": 3}, stop_reason="end_turn")
            return
        async for event in self.inner.stream_chat(model_ref, messages, effort, cancel, tools):
            yield event


def _drawn(messages):
    return next(m for m in messages if m["role"] == "tool")["content"][0]


def test_a_chat_model_draws_a_picture_and_the_chat_keeps_it(hub):
    """29.6а: generate_image - offered to a chat begun once a picture model is here (a chat's tools stay as they were
    first sent, D14); the hub draws it with this computer's model, waits, keeps the PNG with the chat (data/blobs:
    as long as the chat, D30) - the window shows it; the model hears what was drawn. Blank - said; «Стоп» stops it."""

    def call(n, **args):
        return [{"id": f"g{n}", "name": "generate_image", "input": {"prompt": "a red fox in snow", **args}}]

    provider = OncePerTurn([call(1), call(2, shape="wide"), call(3), call(4)])
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)

    first = _chat(hub)  # no picture model yet: not offered; asked for anyway - said why not
    result = _drawn(_finished(hub, first))
    assert "generate_image" not in [t["name"] for t in provider.calls[0][1]]
    assert result["error"] and "no picture model" in result["output"] and "blob" not in result

    _install(hub)
    drawn = _chat(hub)
    result = _drawn(_finished(hub, drawn))
    tools = provider.calls[-2][1]
    assert "generate_image" in [t["name"] for t in tools]
    assert not result["error"], result["output"]
    assert "640×384" in result["output"] and "cannot see" in result["output"]  # wide, at the model's 512
    assert result["media_type"] == "image/png" and len(result["blob"]) == 64
    kept = hub.app.state.chat.blobs / result["blob"]
    picture = hub.get(f"/v1/visual/pictures/{result['blob']}")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    assert visual.pixels(picture.content) == visual.pixels(PICTURE) and "a red fox in snow" in visual.parameters(
        picture.content)  # fmt: skip
    assert kept.read_bytes() == picture.content
    job = hub.get("/v1/visual/jobs").json()["jobs"][0]
    assert (job["conversation_id"], job["client"], job["width"], job["height"]) == (drawn, "chat", 640, 384)
    told = provider.calls[-1][0]  # the model's next request: the result in words, not the picture
    assert any("640×384" in str(m.get("content")) for m in told if m.get("role") == "tool")
    assert hub.get("/v1/visual/pictures/" + "0" * 64).status_code == 404
    assert hub.get("/v1/visual/pictures/..%2Fhub.db").status_code in (404, 422)

    FakeDrawSwap.answer = "blank"
    result = _drawn(_finished(hub, _chat(hub)))
    assert result["error"] and "did not come out" in result["output"] and "пустой" in result["output"]
    assert "blob" not in result

    FakeDrawSwap.answer, FakeDrawSwap.steps_s = "picture", 0.5  # a long one, stopped with the answer
    stopped = _chat(hub)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and hub.get("/v1/visual/jobs").json()["jobs"][0]["status"] != "running":
        time.sleep(0.05)
    hub.post(f"/v1/conversations/{stopped}/cancel")
    result = _drawn(_finished(hub, stopped, stopped=True))
    assert result["error"] and "stopped" in result["output"]
    assert hub.get("/v1/visual/jobs").json()["jobs"][0]["status"] == "cancelled" and FakeDrawSwap.unloaded


def test_a_picture_drawn_from_a_picture(hub):
    """29.6б «По картинке»: a source - one drawn here, one a chat holds, a file sent - goes to the engine's img2img
    with how much to change it; the size follows its shape; only a model of several steps follows it (SDXS's one step
    draws anew: 29.6б live), so the ladder skips the others; the source is kept beside the job for its rungs."""
    _install(hub)
    _install(hub, "tiny", {**DRAW_PLAN, "set": "tiny", "tier": "light", "flags": ["--steps", "1"]})
    first = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done")

    job = hub.post("/v1/visual/images", json={"prompt": "a fox, watercolor", "from_job": first["id"],
                                              "strength": 0.6}).json()["job"]  # fmt: skip
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    asked = FakeDrawSwap.asked[-1]
    assert asked["path"] == "/sdapi/v1/img2img" and asked["denoising_strength"] == 0.6
    assert base64.b64decode(asked["init_images"][0]) == hub.get(f"/v1/visual/jobs/{first['id']}/file").content
    assert (asked["width"], asked["height"], done["strength"]) == (512, 512, 0.6)  # PICTURE is square
    assert (hub.app.state.visual.media / done["source"]).is_file()  # kept beside the job: its rungs use it
    picture = hub.get(f"/v1/visual/jobs/{job['id']}/file").content
    assert "Denoising strength: 0.6" in visual.parameters(picture)

    blob = hub.app.state.chat.blobs
    blob.mkdir(parents=True, exist_ok=True)
    (blob / ("ab" * 32)).write_bytes(PICTURE)  # a picture a chat holds
    from_chat = hub.post("/v1/visual/images", json={"prompt": "a fox", "from_blob": "ab" * 32}).json()["job"]
    assert _wait(hub, from_chat["id"], "done")["strength"] == 0.6  # «Заметно» unless said
    sent = base64.b64encode(PICTURE).decode()
    from_file = hub.post("/v1/visual/images", json={"prompt": "a fox", "from_png": sent, "strength": 0.3}).json()
    assert _wait(hub, from_file["job"]["id"], "done")["strength"] == 0.3

    for wrong, status in (({"from_blob": "cd" * 32}, 404), ({"from_job": "nope"}, 404),
                          ({"from_png": base64.b64encode(b"not a picture").decode()}, 422),
                          ({"from_png": "@@@"}, 422), ({"from_png": sent, "model": "tiny"}, 422),
                          ({"from_png": sent, "strength": 1.5}, 422)):  # fmt: skip
        said = hub.post("/v1/visual/images", json={"prompt": "a fox", **wrong})
        assert said.status_code == status, (wrong, said.json())
    one_step = hub.post("/v1/visual/images", json={"prompt": "a fox", "from_png": sent, "model": "tiny"})
    assert "несколько шагов" in one_step.json()["detail"]

    FakeDrawSwap.answer = "fail"  # the ladder: smaller, but no model of one step
    failed = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox", "from_png": sent}).json()["job"]["id"],
                   "failed", "done")  # fmt: skip
    assert [t["model_id"] for t in failed["tries"]] == ["draw", "draw"]


def test_a_video_drawn_in_the_background_and_kept(hub):
    """29.8а: a video - sd-server draws it in the background (vid_gen, through llama-swap's /upstream: it loads the
    model), the hub looks at its job; meanwhile the card is held - a local chat model waits (llama-swap would swap the
    video's engine out under it, 29.8 live); the WebP kept. «Стоп» unloads the model."""
    _install(hub)
    _install(hub, "clip", CLIP_PLAN)
    listed = hub.get("/v1/visual/models").json()
    assert [m["model_id"] for m in listed["models"]] == ["draw"]  # a video model draws no pictures
    assert [(m["model_id"], m["size"]) for m in listed["videos"]] == [("clip", 832)]

    FakeDrawSwap.video_polls = 8
    job = hub.post("/v1/visual/videos", json={"prompt": "a fox running through snow", "shape": "wide",
                                              "seconds": 2, "seed": 4}).json()["job"]  # fmt: skip
    assert (job["kind"], job["model_id"], job["width"], job["height"], job["frames"], job["fps"]) == (
        "video", "clip", 832, 480, 49, 24)  # fmt: skip
    _wait(hub, job["id"], "running")
    assert hub.app.state.visual.holding  # the card: a local chat model waits for the video's end
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert not hub.app.state.visual.holding
    asked = FakeDrawSwap.video[-1]
    assert asked["path"] == "/upstream/clip/sdcpp/v1/vid_gen" and asked["output_format"] == "webp"
    assert (asked["width"], asked["height"], asked["video_frames"], asked["fps"]) == (832, 480, 49, 24)
    assert asked["seed"] == 4
    assert asked["sample_params"]["sample_steps"] == 4 and asked["sample_params"]["flow_shift"] == 5.0
    assert asked["sample_params"]["guidance"]["txt_cfg"] == 1.0 and asked["prompt"] == "a fox running through snow"
    video = hub.get(f"/v1/visual/jobs/{job['id']}/file")
    assert video.status_code == 200 and video.headers["content-type"] == "image/webp" and video.content == WEBP
    assert done["file"].endswith(".webp")
    assert hub.post("/v1/visual/videos", json={"prompt": "x", "seconds": 9}).status_code == 422
    assert hub.post("/v1/visual/images", json={"prompt": "x", "model": "clip"}).status_code == 404  # not a picture

    FakeDrawSwap.video_polls = 1000  # a long one, stopped
    long = hub.post("/v1/visual/videos", json={"prompt": "a fox"}).json()["job"]
    _wait(hub, long["id"], "running")
    time.sleep(0.3)
    hub.post(f"/v1/visual/jobs/{long['id']}/cancel")
    stopped = _wait(hub, long["id"], "cancelled", "failed", "done", timeout=10.0)
    assert stopped["status"] == "cancelled" and FakeDrawSwap.unloaded[-1] == "clip"
    assert not hub.app.state.visual.holding


def test_a_local_chat_model_waits_while_the_card_draws_a_video():
    """29.8а: the llama-swap provider asks the hub's gate before each answer: while a video holds the card it says so
    (a «loading» line, as llama-swap's own) and sends nothing until the video ends; the user may stop meanwhile."""
    from hub.gateway.openai_compat import OpenAICompatProvider

    sent = []

    def handler(request):
        sent.append(time.monotonic())
        chunks = [{"choices": [{"delta": {"content": "да"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 1}}]  # fmt: skip
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def run():
        provider = OpenAICompatProvider("llama-swap", "http://swap", transport=httpx.MockTransport(handler))
        free = asyncio.Event()

        async def gate(cancel):
            if free.is_set():
                return False
            waiting = asyncio.ensure_future(cancel.wait())
            await asyncio.wait({waiting, asyncio.ensure_future(free.wait())}, return_when=asyncio.FIRST_COMPLETED)
            return True

        provider.gate = gate
        events, cancel = [], asyncio.Event()

        async def answer():
            async for event in provider.stream_chat("chat", [{"role": "user", "content": "привет"}], "low", cancel):
                events.append(event)

        task = asyncio.create_task(answer())
        await asyncio.sleep(0.2)
        held = (list(events), list(sent))
        free.set()
        await task
        return held, events

    (held_events, held_sent), events = asyncio.run(run())
    assert held_sent == [] and [e.kind for e in held_events] == ["loading"]  # nothing sent while the card is held
    assert "видео" in held_events[0].text and sent and "".join(e.text for e in events if e.kind == "content") == "да"


def test_without_llama_swap_the_ladder_stops_at_once(hub):
    _install(hub)
    hub.app.state.visual._url = "http://127.0.0.1:9"  # nothing listens there
    job = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    failed = _wait(hub, job["id"], "failed", "done")
    assert len(failed["tries"]) == 1 and "llama-swap не ответил" in failed["error"]


def test_the_progress_watch_ends_even_when_its_cancel_is_swallowed(monkeypatch, tmp_path):
    """2026-11-07: a cancel that reaches httpx mid-request can come out as an HTTPError (anyio under it) - the watch
    let that pass and went on asking for the progress, and the picture waited for it for ever (one test run in five
    hung «рисую»). A stop flag ends it whatever the cancel became."""

    class Swallowing:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise httpx.ReadError("cancelled mid-request") from None

    class Bus:
        async def publish(self, *args, **kw):
            pass

    monkeypatch.setattr(visual.httpx, "AsyncClient", Swallowing)
    monkeypatch.setattr(visual, "STEP_S", 0.01)

    async def run():
        watcher = visual.Visual(None, Bus(), llama_swap_url="http://swap", media=tmp_path, machine=dict)
        stop = asyncio.Event()
        task = asyncio.create_task(watcher._watch("job", "draw", stop))
        await asyncio.sleep(0.1)  # inside its request
        stop.set()
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=3.0)
        return task in done

    assert asyncio.run(run())


def test_pictures_a_stopped_hub_left_going_fail(hub):
    _install(hub)
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, prompt, model_id, width, height, seed, status) VALUES "
        "('left', 'a fox', 'draw', 512, 512, 1, 'running')"))  # fmt: skip
    assert hub.portal.call(hub.app.state.visual.start) == 1
    left = hub.get("/v1/visual/jobs/left").json()["job"]
    assert left["status"] == "failed" and "перезапускался" in left["error"]
