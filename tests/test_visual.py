"""Pictures and videos (D55, stage 29): a picture model's trial picture through llama-swap, and a blank picture told
from a real one - stable-diffusion.cpp on Vulkan can answer «success» with a flat grey one (#1220)."""

import asyncio
import base64
import json
import random
import shutil
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient

from app.hub_client import HubClient
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
    passes: ClassVar[list] = []  # (pass, step, its steps) at each look, as sd-server's job says them with a preview
    cancels: ClassVar[list] = []  # a video job's /cancel, each asked (48's review: routes_sdcpp.cpp 822)
    heeds = True  # a job told to stop stops (sd-server's cancel: at a step's end) - or goes on
    cancelling = False
    fails: ClassVar = None  # the error a video's job ends with, as sd-server says it: {code, message}
    logs: ClassVar[dict] = {}  # a model's log llama-swap keeps (/logs/stream/<id>): its sd-server's own lines
    filmed: ClassVar = None  # the WebP a video's job ends with: None - WEBP, an animated one (48.8: or STILL)
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
            if cls.cancelling and cls.heeds:
                self._send(200, {"id": "job_v", "status": "cancelled", "result": None,
                                 "error": {"code": "cancelled", "message": ""}})  # fmt: skip
                return
            if cls.fails is not None:
                self._send(200, {"id": "job_v", "status": "failed", "result": None, "error": cls.fails})
                return
            if cls.polls < cls.video_polls:
                state = {"id": "job_v", "status": "generating", "result": None}
                asked = cls.video[-1].get("preview") or "none"  # sd-server: the step only with a preview asked
                if asked != "none" and cls.polls <= len(cls.passes):
                    pass_, step, total = cls.passes[cls.polls - 1]
                    state["preview"] = {"pass": pass_, "step": step, "total_steps": total, "b64_json": "cG5n"}
                self._send(200, state)
                return
            self._send(200, {"id": "job_v", "status": "completed", "result": {
                "b64_json": base64.b64encode(cls.filmed or WEBP).decode(), "mime_type": "image/webp",
                "output_format": "webp", "fps": 24, "frame_count": cls.video[-1]["video_frames"]}})  # fmt: skip
            return
        if self.path.startswith("/logs/stream/"):  # llama-swap's: what it kept of the model's output, at once
            kept = cls.logs.get(self.path.rsplit("/", 1)[1])
            if kept is None:
                self._send(404, {"error": "no such model"})
                return
            data = kept.encode()
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/running":  # llama-swap's: the video's model, until it is unloaded
            model = cls.video[-1]["path"].split("/")[2] if cls.video and not cls.stopped else ""
            self._send(200, {"running": [{"model": model, "state": "ready"}] if model else []})
            return
        if self.path.startswith("/upstream/") and self.path.endswith("/sdapi/v1/progress"):
            cls = FakeDrawSwap
            self._send(200, {"progress": cls.step / cls.total if cls.total else 0, "current_image": "jpeg" * 3,
                             "state": {"sampling_step": cls.step, "sampling_steps": cls.total}})  # fmt: skip
        else:
            self._send(200, {"data": []})  # /v1/models: no chat models

    def do_POST(self):
        cls = FakeDrawSwap
        if self.path.startswith("/api/models/unload"):  # one model - or, «Выгрузить» (48.9г), all of them: "unload"
            cls.unloaded.append(self.path.rsplit("/", 1)[1])
            cls.stopped = True
            self._send(200, {"msg": "ok"})
            return
        if self.path.endswith("/sdcpp/v1/jobs/job_v/cancel"):
            cls.cancels.append(self.path)
            cls.cancelling = True
            self._send(200, {"id": "job_v", "status": "generating"})
            return
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path.endswith("/sdcpp/v1/vid_gen"):  # a video: accepted at once, drawn in the background (29.8)
            cls.video.append({**body, "path": self.path})
            cls.polls, cls.stopped, cls.cancelling = 0, False, False
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


def webp(*chunks: tuple[bytes, bytes]) -> bytes:
    body = b"".join(name + struct.pack("<I", len(data)) + data + bytes(len(data) & 1) for name, data in chunks)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


_SIDE = (31).to_bytes(3, "little") * 2  # 32x32, as VP8X and ANMF say a side: less one
_FRAME = bytes(6) + _SIDE + (42).to_bytes(3, "little") + bytes(1) + b"VP8 " + struct.pack("<I", 2) + bytes(2)
WEBP = webp((b"VP8X", bytes([2, 0, 0, 0]) + _SIDE), (b"ANIM", bytes(6)), (b"ANMF", _FRAME),
            (b"ANMF", _FRAME))  # an animated WebP, as sd-server's video comes (29.8 live)  # fmt: skip
# (48.8 live) LTX's white frames: libwebp folds the same frames into one - a still picture, no ANIM, no ANMF
STILL = webp((b"VP8X", bytes([4, 0, 0, 0]) + _SIDE), (b"VP8 ", bytes(10)), (b"XMP ", b"<x:xmpmeta/>"))
WAN_FLAGS = ["--cfg-scale", "1", "--sampling-method", "euler", "--steps", "4", "--flow-shift", "5"]
CLIP_PLAN = {"engine": "sd", "set": "clip", "kind": "video", "tier": "video", "size": 832, "flags": WAN_FLAGS}
DRAW_PLAN = {"engine": "sd", "set": "draw", "tier": "main", "size": 512, "flags": ["--cfg-scale", "1", "--steps", "4"]}


@pytest.fixture
def hub(monkeypatch):
    FakeDrawSwap.steps_s, FakeDrawSwap.answer, FakeDrawSwap.asked, FakeDrawSwap.unloaded = 0.05, "picture", [], []
    FakeDrawSwap.video, FakeDrawSwap.video_polls, FakeDrawSwap.polls, FakeDrawSwap.passes = [], 3, 0, []
    FakeDrawSwap.cancels, FakeDrawSwap.heeds, FakeDrawSwap.cancelling, FakeDrawSwap.fails = [], True, False, None
    FakeDrawSwap.filmed, FakeDrawSwap.logs = None, {}
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


def _install(hub, model_id="draw", plan=DRAW_PLAN, files=()):
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO model_installs (id, repo, quant, files, folder, model_id, name, status, plan) VALUES "
        "(?, 'test/Draw-GGUF', 'Q4_0', ?, 'x', ?, 'Draw', 'done', ?)",
        (model_id, json.dumps(list(files)), model_id, json.dumps(plan))))  # fmt: skip


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


def test_a_picture_deleted_with_its_own_files_and_what_must_stay_refused(hub):
    """(The user's word, 2026-11-25) the Studio's «Удалить»: the job's own files and its row go, another job's file and
    a film's photo stay; refused in words - a job going on, a film scene's clip (its latest, its clip with the lips
    made), the source of an improvement going on, a file Windows holds past the retries; one let go within them goes."""
    _install(hub)
    media = data_paths().media
    posted = [hub.post("/v1/visual/images", json={"prompt": f"a fox {n}", "seed": n}).json()["job"] for n in (1, 2, 3)]
    first, second, third = (_wait(hub, j["id"], "done", "failed") for j in posted)
    assert [j["status"] for j in (first, second, third)] == ["done"] * 3
    folder = (media / first["file"]).parent
    for name in (f"{first['id']}.jpg", f"{first['id']}-source.png", f"voice-{first['id']}.wav", "film-f1-photo.png"):
        (folder / name).write_bytes(b"x")

    gone = hub.delete(f"/v1/visual/jobs/{first['id']}")
    assert gone.status_code == 200 and gone.json() == {"deleted": first["id"]}
    assert not list(folder.glob(f"*{first['id']}*")), "its own files"
    assert (folder / "film-f1-photo.png").is_file() and (media / second["file"]).is_file()
    assert hub.get(f"/v1/visual/jobs/{first['id']}").status_code == 404
    assert hub.delete(f"/v1/visual/jobs/{first['id']}").status_code == 404
    assert ("visual.deleted", {"id": first["id"]}) in hub.seen

    idea = "Лампа на столе, что светит ночью всем, кто не спит до утра"
    lips = json.dumps({"job": third["id"], "key": "k"})
    hub.app.state.db.call(lambda conn: (
        conn.execute("INSERT INTO films (id, idea, seconds) VALUES ('f1', ?, 10)", (idea,)),
        conn.execute("INSERT INTO film_scenes (film_id, n, mode, prompt, frames, job_id, status) VALUES "
                     "('f1', 1, 'text', 'a lamp', 33, ?, 'done')", (second["id"],)),
        conn.execute("INSERT INTO film_scenes (film_id, n, mode, prompt, frames, lips) VALUES "
                     "('f1', 2, 'text', 'a lamp', 33, ?)", (lips,))))  # fmt: skip
    for scene in (second, third):
        refused = hub.delete(f"/v1/visual/jobs/{scene['id']}")
        assert refused.status_code == 409 and refused.json()["detail"] == (
            "это сцена фильма «Лампа на столе, что светит ночью всем,…» — удалите фильм или переснимите сцену в "
            "«Режиссёре»")  # fmt: skip
        assert (media / scene["file"]).is_file()
    hub.app.state.db.call(lambda conn: (conn.execute("DELETE FROM film_scenes"), conn.execute("DELETE FROM films")))
    assert hub.delete(f"/v1/visual/jobs/{third['id']}").status_code == 200  # no film's now

    improves = json.dumps({"job": second["id"]})  # an improvement of the second, as if going on
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, kind, prompt, model_id, width, height, seed, status, enhance) VALUES "
        "('e1', 'video', '', 'enhance', 512, 512, 0, 'running', ?)", (improves,)))  # fmt: skip
    refused = hub.delete(f"/v1/visual/jobs/{second['id']}")
    assert refused.status_code == 409 and "«Улучшить…» — дождитесь или отмените" in refused.json()["detail"]
    hub.app.state.db.call(lambda conn: conn.execute("UPDATE visual_jobs SET status = 'done' WHERE id = 'e1'"))

    FakeDrawSwap.steps_s = 0.5  # a long picture: refused while it waits and while it is drawn; cancelled - it goes
    going = hub.post("/v1/visual/images", json={"prompt": "a long fox", "seed": 4}).json()["job"]
    for _ in ("queued", "running"):
        refused = hub.delete(f"/v1/visual/jobs/{going['id']}")
        assert refused.status_code == 409 and refused.json()["detail"] == "это ещё делается — сначала отмените (✕)"
        _wait(hub, going["id"], "running")
    hub.post(f"/v1/visual/jobs/{going['id']}/cancel")
    _wait(hub, going["id"], "cancelled", "failed", "done", timeout=5.0)
    assert hub.delete(f"/v1/visual/jobs/{going['id']}").status_code == 200

    path = media / second["file"]
    with path.open("rb"):  # another program holds it all the while the hub tries (Windows: no deleting an open file)
        held = hub.delete(f"/v1/visual/jobs/{second['id']}")
    assert held.status_code == 409
    assert held.json()["detail"] == f"файл занят другой программой — закройте её и удалите снова: {path.name}"
    assert hub.get(f"/v1/visual/jobs/{second['id']}").status_code == 200 and path.is_file()
    handle = path.open("rb")  # the window's player letting it go a moment later
    threading.Timer(0.4, handle.close).start()
    assert hub.delete(f"/v1/visual/jobs/{second['id']}").status_code == 200 and not path.exists()
    assert [p["id"] for k, p in hub.seen if k == "visual.deleted"] == [first["id"], third["id"], going["id"],
                                                                        second["id"]]  # fmt: skip


def _taken_not_running(hub, monkeypatch):
    """The worker held where it has taken a job from the queue and not yet said «running» - its model not asked for: a
    picture at its look at the computer, a video at its look for the model's files. `at` - it is there; `go` - on."""
    state = hub.app.state.visual
    at, go = threading.Event(), threading.Event()
    machine, missing = state._machine, state.missing

    def looked():
        at.set()
        go.wait(10)
        return machine()

    async def files(model_id):
        at.set()
        await asyncio.to_thread(go.wait, 10)
        return await missing(model_id) if missing is not None else ""

    monkeypatch.setattr(state, "_machine", looked)
    monkeypatch.setattr(state, "missing", files)
    return at, go


JOBS = (("images", {"prompt": "a fox", "model": "draw", "seed": 1}, "asked"),
        ("videos", {"prompt": "the fox runs", "model": "clip", "seconds": 1}, "video"))  # fmt: skip


def test_x_on_a_job_the_worker_has_just_taken_stops_it_before_its_model_loads(hub, monkeypatch):
    """48.10а (stage 48's review, finding 5): ✕ on a job the worker had just taken from the queue, not yet «running» -
    `cancel` saw «в очереди» and wrote «отменено», the worker wrote «running» over it, and the picture was drawn, the
    video filmed. Such a job is stopped as one going on: before its model is asked for the worker looks - a picture
    and a video alike -, nothing is loaded, it ends «отменено»."""
    _install(hub)
    _install(hub, "clip", CLIP_PLAN)
    at, go = _taken_not_running(hub, monkeypatch)
    for kind, spec, asked in JOBS:
        at.clear()
        go.clear()
        job = hub.post(f"/v1/visual/{kind}", json=spec).json()["job"]
        assert at.wait(10), "the worker did not take it"
        assert hub.post(f"/v1/visual/jobs/{job['id']}/cancel").status_code == 200
        go.set()
        stopped = _wait(hub, job["id"], "cancelled", "failed", "done")
        assert stopped["status"] == "cancelled", (kind, stopped["status"])
        assert not getattr(FakeDrawSwap, asked), f"{kind}: its model was asked for after ✕"


def test_unload_in_the_same_instant_stops_a_job_the_worker_has_just_taken(hub, monkeypatch):
    """48.10а: «Выгрузить» stops the Studio's job through the same `cancel` (48.9г) - in the instant the worker has
    taken a job and not yet said «running», a video's model was asked for all the same: it filmed on the card the
    person had been given. The job stops before its model is asked for, the card stays the person's, the queue waits."""
    _install(hub)
    _install(hub, "clip", CLIP_PLAN)
    state = hub.app.state
    at, go = _taken_not_running(hub, monkeypatch)
    for kind, spec, asked in JOBS:
        at.clear()
        go.clear()
        job = hub.post(f"/v1/visual/{kind}", json=spec).json()["job"]
        assert at.wait(10), "the worker did not take it"
        said: list = []
        threading.Thread(target=lambda said=said: said.append(hub.post("/v1/models/unload")), daemon=True).start()
        deadline = time.monotonic() + 5
        while not state.card.given:  # «Выгрузить» said: the card is the person's
            assert time.monotonic() < deadline, "«Выгрузить» was not said"
            time.sleep(0.02)
        go.set()
        stopped = _wait(hub, job["id"], "cancelled", "failed", "done")
        deadline = time.monotonic() + 30
        while not said:  # it answers once the card is let go: no request left going when the test ends
            assert time.monotonic() < deadline, "«Выгрузить» did not answer"
            time.sleep(0.05)
        assert stopped["status"] == "cancelled", (kind, stopped["status"])
        assert not getattr(FakeDrawSwap, asked), f"{kind}: its model was asked for after «Выгрузить»"
        assert said[0].status_code == 200 and state.card.given and state.visual.held
        assert hub.post("/v1/gpu/continue").status_code == 200  # the card everyone's again, for the next one


def test_a_videos_frame_deleted_before_it_begins_fails_it_with_words(hub, monkeypatch):
    """48.10б (stage 48's review, finding 9): a video's frame - the first, or the last - deleted from the disk while the
    job waited in the queue was skipped without a word: one by a picture filmed by its words alone, one by two frames
    without its last. As a picture by a sample does («образец этой картинки уже удалён»), the video fails with words,
    and its model is not asked for."""
    _install(hub, "film", {**CLIP_PLAN, "set": "film", "modes": ["text", "picture", "frames"]})
    first, last = base64.b64encode(PICTURE).decode(), base64.b64encode(GREY).decode()
    at, go = _taken_not_running(hub, monkeypatch)
    media = hub.app.state.visual.media
    for key, words in (("source", "первый кадр этого видео уже удалён"),
                       ("last_frame", "последний кадр этого видео уже удалён")):  # fmt: skip
        at.clear()
        go.clear()
        job = hub.post("/v1/visual/videos", json={"prompt": "the fox turns", "model": "film", "from_png": first,
                                                  "last_png": last, "seconds": 1}).json()["job"]  # fmt: skip
        assert at.wait(10), "the worker did not take it"
        (media / job[key]).unlink()  # deleted by hand while it waited
        go.set()
        failed = _wait(hub, job["id"], "failed", "done", "cancelled")
        assert failed["status"] == "failed" and failed["error"] == words, (key, failed["status"], failed["error"])
    assert not FakeDrawSwap.video, "a video filmed without its frame"


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


def test_the_windows_client_asks_the_hub_for_a_video(hub):
    """48.8 live: «Снять видео» in the window never reached the hub - HubClient.visual_video passed `json=`, which
    `_call` does not take (a TypeError each time since 29.8б); the window's tests stood in a fake client for it. The
    real client against the real hub: the job comes back as the window takes it, HD's size, and it films."""
    _install(hub, "clip", CLIP_PLAN)
    client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
    job = client.visual_video({"prompt": "a fox running through snow", "model": "clip", "shape": "wide",
                               "seconds": 2, "hd": True, "seed": 4})["job"]  # fmt: skip
    assert (job["kind"], job["model_id"], job["frames"]) == ("video", "clip", 49)
    assert (job["width"], job["height"]) == (1280, 704)
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert (FakeDrawSwap.video[-1]["width"], FakeDrawSwap.video[-1]["height"]) == (1280, 704)


def test_a_video_that_came_back_as_one_still_picture_fails_in_words(hub):
    """48.8 live, found by the user: LTX filmed white - each frame the same, libwebp folding them into one still picture
    - and the Studio said «готово за 104 с», its player «0:00 / 0:00»; the trial had passed it the same way. A video, or
    a trial, the engine gives back as one still picture fails, in words; nothing is kept."""
    _install(hub, "clip", CLIP_PLAN)
    FakeDrawSwap.filmed = STILL
    job = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "clip", "seconds": 2}).json()["job"]
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "failed" and "одна неподвижная картинка" in done["error"] and not done["file"]
    trial = asyncio.run(visual.trial_video(hub.app.state.visual._url, "clip"))
    assert not trial["ok"] and "одна неподвижная картинка" in trial["error"]

    FakeDrawSwap.filmed = None  # an animated one: a video
    assert asyncio.run(visual.trial_video(hub.app.state.visual._url, "clip"))["ok"]


SHORT_LOG = "\n".join(
    (  # sd-server master-1053's own lines, as it failed on the stand (stage 55's probe)
        "[I] generate_video 768x512x49 --- video.cpp:1928",
        "[W] model manager memory on CUDA0: reported free 1245.02 MB / total 16275.75 MB, tracked weights 12941.76 MB"
        " / other runtime 0.00 MB / current runtime 0.00 MB --- model_manager.cpp:1796",
        "[W] model manager cannot make enough memory available on CUDA0: need 1639.89 MB device / 615.89 MB budget,"
        " available 1245.02 MB device / 1531.24 MB budget --- model_manager.cpp:2046",
        "[E] ltxav segment 1/1 (graph) failed during workspace capacity check --- ggml_runner.cpp:933",
        "[E] sampling failed after 13.47s --- video.cpp:2022",
        "",
    )
)
FILMED_LOG = "[I] generate_video completed in 37.68s --- video.cpp:2282\n"
NO_RESULTS = {"code": "generation_failed", "message": "generate_video returned no results"}


def test_a_video_the_card_had_no_room_for_says_so(hub):
    """«Стабилизация», 2026-11-20 («первый ролик падает, если другие программы заняли часть видеокарты»), the plan's
    step 5 - the user's «Да, делай 5»: a video the engine could not film for the card's memory said only «движок не
    снял видео: generate_video returned no results» - the Studio's red line, the Director's stopped film. The hub
    reads the model's log llama-swap keeps: the engine's words of the card's memory since the last video it filmed -
    the person is told so, and what to do. A failure its log says nothing of, a memory line of a video before one it
    filmed, a log llama-swap does not give - the engine's words, as before."""
    _install(hub, "clip", CLIP_PLAN)
    FakeDrawSwap.fails = NO_RESULTS

    def film(log):
        FakeDrawSwap.logs = {} if log is None else {"clip": log}
        job = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "clip"}).json()["job"]
        failed = _wait(hub, job["id"], "done", "failed")
        assert failed["status"] == "failed" and not failed["file"]
        return failed["error"]

    short = film(SHORT_LOG)
    assert "памяти видеокарты" in short and "generate_video" not in short, short
    assert "закройте программы" in short  # what to do
    for log in ("[I] generate_video 768x512x49 --- video.cpp:1928\n[E] sampling failed --- video.cpp:2022\n",
                SHORT_LOG + FILMED_LOG + "[I] generate_video 768x512x49 --- video.cpp:1928\n", None):  # fmt: skip
        said = film(log)
        assert said.endswith("generate_video returned no results") and "памяти" not in said, said


PAIR_PLAN = {**CLIP_PLAN, "set": "pair", "flags": [*WAN_FLAGS, "--high-noise-steps", "2"]}  # 2 high steps, 4 low


def test_a_videos_steps_shown_as_it_films(hub, monkeypatch):
    """Stage 48's review: a video showed «снимаю», 0 %, for tens of minutes - sd-server gives a job's step only in its
    `preview`, and only when the request asks for one (async_jobs.cpp 249); `proj` is the latents put into colour, no
    VAE. Each step goes to the window as a picture's does; a pair's two passes (the high half's, then the low's) are
    one count. A model that gives no step (no projection for its latents: HunyuanVideo 1.5) - the time it has taken."""
    monkeypatch.setattr(visual, "VIDEO_CLOCK_S", 0.05, raising=False)
    _install(hub, "pair", PAIR_PLAN)
    FakeDrawSwap.passes = [(1, 1, 2), (1, 2, 2), (2, 1, 4), (2, 2, 4), (2, 3, 4), (2, 4, 4)]
    FakeDrawSwap.video_polls = 8
    job = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "pair"}).json()["job"]
    assert _wait(hub, job["id"], "done", "failed")["status"] == "done"
    assert FakeDrawSwap.video[-1].get("preview") == "proj"
    shown = [(p["step"], p["steps"]) for k, p in hub.seen if k == "visual.progress" and p["id"] == job["id"]]
    assert shown == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]  # through both passes, each step once

    FakeDrawSwap.passes = []  # no step: the time
    FakeDrawSwap.video_polls = 12
    plain = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "pair"}).json()["job"]
    assert _wait(hub, plain["id"], "done", "failed")["status"] == "done"
    clock = [p for k, p in hub.seen if k == "visual.progress" and p["id"] == plain["id"]]
    assert clock and all(p["steps"] == 0 and p["preview"] == "" for p in clock)
    assert [p["seconds"] for p in clock] == sorted(p["seconds"] for p in clock) and clock[-1]["seconds"] > 0


def test_a_video_that_stops_moving_is_stopped_in_the_engine(hub, monkeypatch):
    """Stage 48's review, finding 6: past its hour a video was «не вышло», the card let go to a chat - and sd-server
    filmed on: nobody told it to stop (POST /sdcpp/v1/jobs/<id>/cancel, routes_sdcpp.cpp 822); the next video stood in
    its own queue behind it. And an hour was no limit for LTX and MiniMax on 16 GB. Now a video stops when it does not
    move - no new step, pass, status or place in the engine's queue - for VIDEO_STALL_S (15 min); a model that gives no
    step at all - after VIDEO_TIMEOUT_S (3 h). Stopped in the engine: its job cancelled (asked only of a model
    llama-swap runs), the model unloaded if the cancel does not take within CANCEL_WAIT_S; the card let go after it.
    The engine's error in words is its message, not its object."""
    for name, value in (("VIDEO_STALL_S", 0.5), ("VIDEO_TIMEOUT_S", 0.6), ("CANCEL_WAIT_S", 0.4)):
        monkeypatch.setattr(visual, name, value, raising=False)
    _install(hub, "clip", CLIP_PLAN)

    def film(**fake):
        for key, value in fake.items():
            setattr(FakeDrawSwap, key, value)
        job = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "clip"}).json()["job"]
        return _wait(hub, job["id"], "done", "failed", timeout=10.0)

    moving = film(passes=[(1, i, 40) for i in range(1, 30)], video_polls=30)  # slowly, past the old limit
    assert moving["status"] == "done", moving["error"]
    assert not FakeDrawSwap.cancels

    stalled = film(passes=[(1, 1, 4), (1, 2, 4), (1, 3, 4)], video_polls=10_000)  # three steps, then nothing
    assert stalled["status"] == "failed" and "хода" in stalled["error"], stalled["error"]
    assert FakeDrawSwap.cancels == ["/upstream/clip/sdcpp/v1/jobs/job_v/cancel"] and FakeDrawSwap.unloaded == []
    assert not hub.app.state.visual.holding

    deaf = film(passes=[], video_polls=10_000, heeds=False)  # no step at all, and a cancel that does not take
    assert deaf["status"] == "failed" and "не готово" in deaf["error"], deaf["error"]
    assert len(FakeDrawSwap.cancels) == 2 and FakeDrawSwap.unloaded == ["clip"]
    assert not hub.app.state.visual.holding

    broken = film(heeds=True, fails={"code": "generation_failed", "message": "CUDA error: out of memory"})
    assert broken["status"] == "failed" and broken["error"].endswith("CUDA error: out of memory"), broken["error"]


def test_a_trial_video_past_its_time_is_stopped_in_the_engine(hub, monkeypatch):
    """Stage 48's review, finding 6: an install's trial video past its 15 minutes left sd-server filming too."""
    monkeypatch.setattr(visual, "TRIAL_TIMEOUT_S", 0.4)
    monkeypatch.setattr(visual, "CANCEL_WAIT_S", 0.3, raising=False)
    FakeDrawSwap.video_polls = 10_000
    result = asyncio.run(visual.trial_video(hub.app.state.visual._url, "clip"))
    assert not result["ok"] and FakeDrawSwap.cancels == ["/upstream/clip/sdcpp/v1/jobs/job_v/cancel"]


def test_a_video_familys_own_clip(hub):
    """48.3: each video family its own clip - its frames a second (Wan2.1's 16, not TI2V-5B's 24), its frames' step
    (LTX's VAE takes 8k+1), its sizes and the grid its sides keep (LTX's 32) - from its install's plan; a model
    installed before keeps TI2V-5B's."""
    film = {**CLIP_PLAN, "set": "film", "size": 768,
            "clip": {"fps": 16, "step": 8, "grid": 32, "sizes": {"wide": [768, 512], "tall": [512, 768],
                                                                 "square": [640, 640]},
                     "hd": {"wide": [1216, 704], "tall": [704, 1216], "square": [960, 960]}}}  # fmt: skip
    _install(hub, "film", film)
    _install(hub, "clip", CLIP_PLAN)
    job = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "film", "shape": "wide",
                                              "seconds": 2}).json()["job"]  # fmt: skip
    assert (job["width"], job["height"], job["frames"], job["fps"]) == (768, 512, 33, 16)  # 2 s: 32 frames + 1
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    asked = FakeDrawSwap.video[-1]
    assert (asked["width"], asked["height"], asked["video_frames"], asked["fps"]) == (768, 512, 33, 16)
    tall = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "film", "shape": "tall", "hd": True,
                                               "seconds": 1}).json()["job"]  # fmt: skip
    assert (tall["width"], tall["height"], tall["frames"]) == (704, 1216, 17)
    _wait(hub, tall["id"], "done", "failed")
    before = hub.post("/v1/visual/videos", json={"prompt": "a fox", "model": "clip", "seconds": 2}).json()["job"]
    assert (before["width"], before["height"], before["frames"], before["fps"]) == (832, 480, 49, 24)
    _wait(hub, before["id"], "done", "failed")
    from common.picture_sizes import clip_of, trial_size  # a trial's size on the family's grid

    assert trial_size(clip_of(film["clip"])) == (480, 288) and trial_size(clip_of(None)) == (480, 272)


def test_a_video_by_a_picture_and_by_two_frames(hub):
    """48.5: a video by a picture - its first frame (`init_image`), the video's shape the picture's - and by two frames,
    the first and the last (`end_image`); each kept beside the job. A family films as its catalog says (`modes`: text,
    picture, frames): Wan2.2-I2V-A14B only by a picture; a model installed before - only by words."""
    _install(hub, "film", {**CLIP_PLAN, "set": "film", "modes": ["text", "picture", "frames"]})
    _install(hub, "still", {**CLIP_PLAN, "set": "still", "modes": ["picture", "frames"]})
    _install(hub, "clip", CLIP_PLAN)
    wide = png(64, 36, lambda x, y: (x * 4, y * 7, 128))
    first, last = base64.b64encode(wide).decode(), base64.b64encode(PICTURE).decode()

    job = hub.post("/v1/visual/videos", json={"prompt": "the fox runs", "model": "film", "from_png": first,
                                              "shape": "tall"}).json()["job"]  # fmt: skip
    assert (job["width"], job["height"]) == (832, 480)  # the picture's shape, not the one asked
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    asked = FakeDrawSwap.video[-1]
    assert base64.b64decode(asked["init_image"]) == wide and "end_image" not in asked
    assert (hub.app.state.visual.media / done["source"]).read_bytes() == wide

    job = hub.post("/v1/visual/videos", json={"prompt": "the fox turns", "model": "film", "from_png": first,
                                              "last_png": last}).json()["job"]  # fmt: skip
    done = _wait(hub, job["id"], "done", "failed")
    asked = FakeDrawSwap.video[-1]
    assert base64.b64decode(asked["end_image"]) == PICTURE and base64.b64decode(asked["init_image"]) == wide
    assert (hub.app.state.visual.media / done["last_frame"]).read_bytes() == PICTURE

    picked = hub.post("/v1/visual/videos", json={"prompt": "the fox", "from_png": first}).json()["job"]
    assert picked["model_id"] == "film"  # none named: the first that films by a picture
    _wait(hub, picked["id"], "done", "failed")
    for wrong, words in (({"model": "still"}, "только по картинке"), ({"model": "clip", "from_png": first},
                         "не снимает по картинке"), ({"model": "still", "from_png": first, "last_png": last}, None),
                         ({"model": "film", "last_png": last}, "первым")):  # fmt: skip
        said = hub.post("/v1/visual/videos", json={"prompt": "the fox", **wrong})
        if words is None:
            assert said.status_code == 200, said.json()
            _wait(hub, said.json()["job"]["id"], "done", "failed")
            continue
        assert said.status_code == 422 and words in said.json()["detail"], (wrong, said.json())


def test_a_scene_of_five_seconds_with_its_own_steps_and_guidance(hub):
    """50.4, for the director's scenes: a video may be 5 seconds long (LTX at 24 a second: 121 frames), and a request
    may say its own steps and guidance - sent to the engine instead of the ones written at the install, kept with
    the job; without them the model's own, as before. Before: 4 seconds at most, the install's steps always."""
    ltx = {**CLIP_PLAN, "set": "film", "size": 768,
           "flags": ["--cfg-scale", "3", "--sampling-method", "euler", "--steps", "20"],
           "clip": {"fps": 24, "step": 8, "grid": 32, "sizes": {"wide": [768, 512], "tall": [512, 768],
                                                                "square": [640, 640]}}}  # fmt: skip
    _install(hub, "film", ltx)
    sent = hub.post("/v1/visual/videos", json={"prompt": "a lamp turns", "seconds": 5, "steps": 12, "cfg": 2.5,
                                               "seed": 7})  # fmt: skip
    assert sent.status_code == 200, sent.json()
    job = sent.json()["job"]
    assert (job["frames"], job["fps"], job["seed"], job["steps"], job["cfg"]) == (121, 24, 7, 12, 2.5)
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    asked = FakeDrawSwap.video[-1]
    assert asked["video_frames"] == 121 and asked["seed"] == 7
    assert asked["sample_params"]["sample_steps"] == 12 and asked["sample_params"]["guidance"] == {"txt_cfg": 2.5}
    assert asked["sample_params"]["sample_method"] == "euler"

    own = hub.post("/v1/visual/videos", json={"prompt": "a lamp turns", "seconds": 1}).json()["job"]
    assert own["steps"] is None and own["cfg"] is None
    _wait(hub, own["id"], "done", "failed")
    asked = FakeDrawSwap.video[-1]
    assert asked["sample_params"]["sample_steps"] == 20 and asked["sample_params"]["guidance"] == {"txt_cfg": 3.0}
    for wrong in ({"seconds": 5.5}, {"steps": 0}, {"steps": 61}, {"cfg": 0.5}, {"cfg": 16}):
        assert hub.post("/v1/visual/videos", json={"prompt": "a lamp", **wrong}).status_code == 422, wrong


def test_a_models_own_settings_draw_and_film_until_as_it_was(hub):
    """«Стабилизация», portion 5 (the user's complaint 13 of 2026-11-27: no settings for picture and video models): a
    model's own steps, guidance («сила подсказки»), negative and seed, set in the Studio, are what it draws and films
    with - the Studio's pictures and a chat's alike (both are `draw`) -, a request's own first («Ещё вариант» - a new
    seed); a lighter model a picture goes down to keeps its own; «Как было» - as it was installed. Before: the
    install's steps and guidance always, no negative but the request's, a new seed each time."""
    _install(hub)
    _install(hub, "tiny", {**DRAW_PLAN, "set": "tiny", "tier": "light", "flags": ["--steps", "1"]})
    _install(hub, "clip", CLIP_PLAN)
    assert hub.put("/v1/visual/models/nothing/own", json={"steps": 9}).status_code == 404
    for wrong in ({"steps": 0}, {"steps": 61}, {"cfg": 0.5}, {"cfg": 16}, {"seed": -1}, {"negative": "x" * 2001}):
        assert hub.put("/v1/visual/models/draw/own", json=wrong).status_code == 422, wrong
    mine = {"steps": 9, "cfg": 3.5, "negative": "blur", "seed": 11}
    assert hub.put("/v1/visual/models/draw/own", json=mine).json()["own"] == mine
    listed = hub.get("/v1/visual/models").json()
    assert [(m["model_id"], m["steps"], m["own"]) for m in listed["models"]] == [("draw", 4, mine), ("tiny", 1, {})]

    job = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    assert (job["seed"], job["negative"], job["steps"], job["cfg"]) == (11, "blur", 9, 3.5)
    assert _wait(hub, job["id"], "done", "failed")["status"] == "done"
    assert FakeDrawSwap.asked[-1] == {
        "model": "draw",
        "prompt": "a fox",
        "negative_prompt": "blur",
        "width": 512,
        "height": 512,
        "seed": 11,
        "steps": 9,
        "cfg_scale": 3.5,
        "batch_size": 1,
    }
    assert visual.parameters(hub.get(f"/v1/visual/jobs/{job['id']}/file").content).startswith(
        "a fox\nNegative prompt: blur\nSteps: 9, CFG scale: 3.5, Seed: 11, ")  # fmt: skip
    other = hub.post("/v1/visual/images", json={"prompt": "a fox", "seed": -1, "negative": "rain"}).json()["job"]
    assert other["seed"] != 11 and other["negative"] == "rain" and other["steps"] == 9
    _wait(hub, other["id"], "done", "failed")

    assert hub.put("/v1/visual/models/clip/own", json={"steps": 6, "cfg": 2.0}).json()["own"] == {
        "steps": 6,
        "cfg": 2.0,
    }
    film = hub.post("/v1/visual/videos", json={"prompt": "a lamp turns", "seconds": 1}).json()["job"]
    assert (film["steps"], film["cfg"]) == (6, 2.0)
    assert _wait(hub, film["id"], "done", "failed")["status"] == "done"
    assert FakeDrawSwap.video[-1]["sample_params"]["sample_steps"] == 6
    assert FakeDrawSwap.video[-1]["sample_params"]["guidance"] == {"txt_cfg": 2.0}

    # «Как было»: as installed - its steps and guidance, no negative, a new seed
    assert hub.delete("/v1/visual/models/draw/own").json()["own"] == {}
    assert hub.delete("/v1/visual/models/clip/own").json()["own"] == {}
    assert [m["own"] for m in hub.get("/v1/visual/models").json()["videos"]] == [{}]
    back = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    assert (back["negative"], back["steps"], back["cfg"]) == ("", None, None)
    _wait(hub, back["id"], "done", "failed")
    assert (FakeDrawSwap.asked[-1]["steps"], FakeDrawSwap.asked[-1]["cfg_scale"]) == (4, 1.0)
    film = hub.post("/v1/visual/videos", json={"prompt": "a lamp turns", "seconds": 1}).json()["job"]
    _wait(hub, film["id"], "done", "failed")
    assert FakeDrawSwap.video[-1]["sample_params"]["sample_steps"] == 4

    # a picture gone down to a lighter model: that one's own steps and guidance, not those of the model asked
    hub.put("/v1/visual/models/draw/own", json={"steps": 9, "cfg": 3.5})
    FakeDrawSwap.answer = lambda body: "fail" if body["model"] == "draw" else "picture"
    asked = len(FakeDrawSwap.asked)
    down = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done", "failed")
    assert down["status"] == "done" and down["model_id"] == "tiny"
    assert [(b["model"], b["steps"], b["cfg_scale"]) for b in FakeDrawSwap.asked[asked:]] == [
        ("draw", 9, 3.5), ("draw", 9, 3.5), ("tiny", 1, 1.0)]  # fmt: skip


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_video_goes_on_from_the_last_frame_of_another(hub, monkeypatch):
    """50.3, for the director's «продолжение»: a video named as another's source (`from_job`) begins with that
    video's last frame - taken by a real ffmpeg, kept beside the clip and taken once; before, a video as a source
    was refused: «образец — картинка PNG»."""
    from PySide6.QtGui import QImage

    from hub import ffmpeg
    from tests.test_ffmpeg import GREEN, ORANGE, _near, _solid, animated_webp

    _install(hub, "film", {**CLIP_PLAN, "set": "film", "modes": ["text", "picture", "frames"]})
    FakeDrawSwap.filmed = animated_webp([_solid(ORANGE), _solid((200, 120, 40)), _solid(GREEN)], 64, 64)
    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    taken = []
    real = ffmpeg.last_frame
    monkeypatch.setattr(ffmpeg, "last_frame", lambda *a: (taken.append(a[1]), real(*a))[1])
    first = _wait(hub, hub.post("/v1/visual/videos", json={"prompt": "a lamp turns"}).json()["job"]["id"], "done")

    job = hub.post("/v1/visual/videos", json={"prompt": "the lamp lights", "from_job": first["id"]})
    assert job.status_code == 200, job.json()
    done = _wait(hub, job.json()["job"]["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    began = QImage.fromData(base64.b64decode(FakeDrawSwap.video[-1]["init_image"]), "PNG")
    pixel = began.pixelColor(32, 32)
    assert (began.width(), began.height()) == (64, 64) and _near((pixel.red(), pixel.green(), pixel.blue()), GREEN)
    assert (done["width"], done["height"]) == (640, 640)  # the frame's shape: square
    again = hub.post("/v1/visual/videos", json={"prompt": "the lamp dims", "from_job": first["id"]})
    _wait(hub, again.json()["job"]["id"], "done", "failed")
    assert len(taken) == 1  # the frame is kept beside the clip


def test_a_videos_mp4_made_once_with_ffmpeg_fetched_once(hub, monkeypatch, tmp_path):
    """29.8в: «Сохранить как…» MP4 - the hub turns the video's WebP into MP4 with ffmpeg (fetched the first time,
    hub/ffmpeg.py), keeps it beside the WebP; a picture has none."""
    from hub import ffmpeg

    _install(hub, "clip", CLIP_PLAN)
    _install(hub)
    video = _wait(hub, hub.post("/v1/visual/videos", json={"prompt": "a fox"}).json()["job"]["id"], "done")
    fetched, made = [], []
    tools = tmp_path / "tools"
    hub.app.state.visual.tools = tools

    async def latest(http):
        return {"version": "9.0", "asset": {"name": ffmpeg.ASSET}}

    def install(found, into, cancel):
        fetched.append(into)
        (into / "ffmpeg").mkdir(parents=True, exist_ok=True)
        (into / "ffmpeg" / "ffmpeg.exe").write_bytes(b"MZ")
        return into / "ffmpeg" / "ffmpeg.exe"

    def to_mp4(program, source, target):
        made.append((program, source.read_bytes()))
        target.write_bytes(b"MP4 of " + source.read_bytes()[:4])

    monkeypatch.setattr(ffmpeg, "latest", latest)
    monkeypatch.setattr(ffmpeg, "install", install)
    monkeypatch.setattr(ffmpeg, "to_mp4", to_mp4)
    mp4 = hub.get(f"/v1/visual/jobs/{video['id']}/mp4")
    assert mp4.status_code == 200 and mp4.headers["content-type"] == "video/mp4" and mp4.content == b"MP4 of RIFF"
    assert fetched == [tools] and made == [(tools / "ffmpeg" / "ffmpeg.exe", WEBP)]
    again = hub.get(f"/v1/visual/jobs/{video['id']}/mp4")
    assert again.content == mp4.content and len(fetched) == 1 and len(made) == 1  # kept beside the WebP
    assert (hub.app.state.visual.media / video["file"]).with_suffix(".mp4").is_file()
    picture = _wait(hub, hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]["id"], "done")
    refused = hub.get(f"/v1/visual/jobs/{picture['id']}/mp4")
    assert refused.status_code == 409 and "не видео" in refused.json()["detail"]


def test_a_chats_video_has_its_mp4_as_a_studios_does(hub, monkeypatch, tmp_path):
    """48.12: the window plays a video's MP4 - Qt's FFmpeg reads the WebP but goes to no frame of it - and a chat's
    card shows the chat's copy (data/blobs), not a job: its MP4 is made the same way, once, beside it; a chat's
    picture (PNG) has none, nor a name that is no file there."""
    from hub import attachments, ffmpeg

    made = []
    tools = tmp_path / "tools"
    (tools / "ffmpeg").mkdir(parents=True)
    (tools / "ffmpeg" / "ffmpeg.exe").write_bytes(b"MZ")
    hub.app.state.visual.tools = tools

    def to_mp4(program, source, target):
        made.append((program, source))
        target.write_bytes(b"MP4 of " + source.read_bytes()[:4])

    monkeypatch.setattr(ffmpeg, "to_mp4", to_mp4)
    blobs = hub.app.state.chat.blobs
    video = attachments.keep(blobs, WEBP)
    mp4 = hub.get(f"/v1/visual/pictures/{video}/mp4")
    assert mp4.status_code == 200, mp4.text
    assert mp4.headers["content-type"] == "video/mp4" and mp4.content == b"MP4 of RIFF"
    assert made == [(tools / "ffmpeg" / "ffmpeg.exe", blobs / video)] and (blobs / f"{video}.mp4").is_file()
    assert hub.get(f"/v1/visual/pictures/{video}/mp4").content == mp4.content and len(made) == 1  # kept
    picture = attachments.keep(blobs, PICTURE)
    refused = hub.get(f"/v1/visual/pictures/{picture}/mp4")
    assert refused.status_code == 409 and "не видео" in refused.json()["detail"]
    assert hub.get(f"/v1/visual/pictures/{'0' * 64}/mp4").status_code == 404
    assert hub.get("/v1/visual/pictures/..%5Csecrets/mp4").status_code == 404
    assert len(made) == 1


def test_a_videos_mp4_is_made_as_it_is_filmed_and_a_chats_card_has_it_at_once(hub, monkeypatch, tmp_path):
    """48.12.8 (the live check: the clip began twice - the window played the WebP until the hub had made the MP4 it
    asked for when the video was first shown, then the MP4 from its start): with an ffmpeg here a video's MP4 is made
    as it is filmed, none asking; a chat's video has its own beside the chat's copy before the model hears it was
    filmed - the job's, copied, not made again."""
    from hub import ffmpeg

    made = []
    tools = tmp_path / "tools"
    (tools / "ffmpeg").mkdir(parents=True)
    (tools / "ffmpeg" / "ffmpeg.exe").write_bytes(b"MZ")
    hub.app.state.visual.tools = tools

    def to_mp4(program, source, target):
        made.append(source)
        target.write_bytes(b"MP4 of " + source.read_bytes()[:4])

    monkeypatch.setattr(ffmpeg, "to_mp4", to_mp4)
    _install(hub, "clip", CLIP_PLAN)
    video = _wait(hub, hub.post("/v1/visual/videos", json={"prompt": "a fox"}).json()["job"]["id"], "done")
    mp4 = (hub.app.state.visual.media / video["file"]).with_suffix(".mp4")
    deadline = time.monotonic() + 5
    while not mp4.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert mp4.is_file() and mp4.read_bytes() == b"MP4 of RIFF" and made == [mp4.with_suffix(".webp")]

    provider = OncePerTurn([[{"id": "v1", "name": "generate_video", "input": {"prompt": "a fox running"}}]])
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    result = _drawn(_finished(hub, _chat(hub)))
    assert not result["error"], result["output"]
    copy = hub.app.state.chat.blobs / f"{result['blob']}.mp4"
    assert copy.is_file() and copy.read_bytes() == b"MP4 of RIFF" and len(made) == 2  # the job's, copied


def test_a_chat_model_films_a_video_and_the_chat_keeps_it(hub):
    """29.8б: generate_video - offered to a chat begun once a video model is here; drawn as any video, waited for,
    the WebP kept with the chat; the model hears its size and length in words."""

    def call(n, **args):
        return [{"id": f"v{n}", "name": "generate_video", "input": {"prompt": "a fox running through snow", **args}}]

    provider = OncePerTurn([call(1), call(2, shape="tall", seconds=1)])
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    _install(hub)  # a picture model only: no video tool
    first = _chat(hub)
    result = _drawn(_finished(hub, first))
    assert "generate_video" not in [t["name"] for t in provider.calls[0][1]]
    assert result["error"] and "no video model" in result["output"]

    _install(hub, "clip", CLIP_PLAN)
    filmed = _chat(hub)
    result = _drawn(_finished(hub, filmed))
    assert "generate_video" in [t["name"] for t in provider.calls[-2][1]]
    assert not result["error"], result["output"]
    assert "480×832" in result["output"] and "1 s" in result["output"] and result["media_type"] == "image/webp"
    assert (hub.app.state.chat.blobs / result["blob"]).read_bytes() == WEBP
    assert FakeDrawSwap.video[-1]["video_frames"] == 25
    picture = hub.get(f"/v1/visual/pictures/{result['blob']}")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"


def test_a_chat_is_offered_a_video_by_words_only_with_a_model_that_films_by_words(hub):
    """48.10в (stage 48's review, finding 10): generate_video films by words - a chat was offered it once any video
    model was here, one that films only by a picture too (Wan2.2-I2V-A14B): each call was refused («снимает только по
    картинке»). It is offered with a model that films by words; called all the same (a chat begun before keeps its
    tools), the model hears why and where the person can film."""

    def call(n):
        return [{"id": f"v{n}", "name": "generate_video", "input": {"prompt": "a fox running through snow"}}]

    provider = OncePerTurn([call(1), call(2)])
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    _install(hub, "still", {**CLIP_PLAN, "set": "still", "modes": ["picture", "frames"]})  # by a picture only
    first = _chat(hub)
    result = _drawn(_finished(hub, first))
    assert "generate_video" not in [t["name"] for t in provider.calls[0][1]], "offered: no model films by words"
    assert result["error"] and "only from a picture" in result["output"], result["output"]
    assert not FakeDrawSwap.video

    _install(hub, "clip", CLIP_PLAN)  # one installed before 48.5: by words
    filmed = _chat(hub)
    result = _drawn(_finished(hub, filmed))
    assert "generate_video" in [t["name"] for t in provider.calls[-2][1]]
    assert not result["error"], result["output"]


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


def test_the_hub_stopping_with_a_queue_begins_nothing_and_says_what_was_left(hub):
    """48.10г (stage 48's review, finding 11): the hub stopping stopped the job going on and waited for the worker -
    which took the next one from the queue meanwhile and loaded its model while the hub was going away. The queue is
    closed first: the job going on stops as by ✕, nothing else begins. What waited is failed once the hub starts again,
    as before (the user's word: the queue does not go on after a restart) - in its own words: a video «не снято», not
    «картинка не нарисована»."""
    _install(hub)
    _install(hub, "clip", CLIP_PLAN)
    FakeDrawSwap.video_polls = 10_000  # a video films until it is stopped
    state = hub.app.state.visual
    videos = [hub.post("/v1/visual/videos", json={"prompt": f"the fox runs, take {n}", "model": "clip",
                                                  "seconds": 1}).json()["job"] for n in (1, 2)]  # fmt: skip
    picture = hub.post("/v1/visual/images", json={"prompt": "a fox", "model": "draw", "seed": 1}).json()["job"]
    _wait(hub, videos[0]["id"], "running")
    deadline = time.monotonic() + 10
    while not FakeDrawSwap.video:  # its model asked for: it films
        assert time.monotonic() < deadline, "the first video did not begin"
        time.sleep(0.02)
    hub.portal.call(state.stop)  # the hub stops
    assert len(FakeDrawSwap.video) == 1 and not FakeDrawSwap.asked, "the worker began the next job as the hub stopped"
    assert _wait(hub, videos[0]["id"], "cancelled", "failed", "done")["status"] == "cancelled"
    left = [hub.get(f"/v1/visual/jobs/{j['id']}").json()["job"] for j in (videos[1], picture)]
    assert [j["status"] for j in left] == ["queued", "queued"]
    assert hub.portal.call(state.start) == 2  # the hub started again
    left = [hub.get(f"/v1/visual/jobs/{j['id']}").json()["job"] for j in (videos[1], picture)]
    assert [(j["status"], j["error"]) for j in left] == [
        ("failed", "хаб перезапускался — видео не снято"),
        ("failed", "хаб перезапускался — картинка не нарисована"),
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_a_video_filmed_with_sound_is_a_webm_with_its_mp4_and_poster(hub, monkeypatch, tmp_path):
    """52.3: a model with an audio VAE among its files films with sound - the engine is asked for a WebM (a WebP has
    no sound), what is heard goes after the description; the clip is kept as it came, its MP4 - with the sound - and
    its poster made at once by a real ffmpeg; its frames are there for a look. The switch off, or a model without the
    file - a silent WebP, as before. A WebM of one picture repeated fails in words, nothing kept."""
    from hub import ffmpeg
    from tests.test_ffmpeg import _clip_with_sound, _said

    program = Path(shutil.which("ffmpeg"))
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: program)
    loud, still = tmp_path / "loud.webm", tmp_path / "still.webm"
    _clip_with_sound(program, loud, 49)
    _clip_with_sound(program, still, 49, moves=False)
    _install(hub, "clip", CLIP_PLAN)
    files = [
        {"role": role, "repo": "test/Talk-GGUF", "path": name, "size": 1, "at": str(tmp_path / name)}
        for role, name in (("diffusion-model", "talk.gguf"), ("audio-vae", "talk_audio_vae.safetensors"))
    ]
    for f in files:
        Path(f["at"]).write_bytes(b"x")
    _install(hub, "talk", {**CLIP_PLAN, "set": "talk"}, files)
    assert {m["model_id"]: m["sound"] for m in hub.get("/v1/visual/models").json()["videos"]} == {
        "clip": False, "talk": True}  # fmt: skip

    FakeDrawSwap.filmed = loud.read_bytes()
    job = hub.post("/v1/visual/videos", json={"prompt": "rain on a street ", "model": "talk", "seconds": 2,
                                              "sound_words": " heavy rain,  a car passing "}).json()["job"]  # fmt: skip
    assert (job["sound"], job["sound_words"]) == (1, "heavy rain, a car passing")
    done = _wait(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    asked = FakeDrawSwap.video[-1]
    assert asked["output_format"] == "webm"
    assert asked["prompt"] == "rain on a street Sound: heavy rain, a car passing"
    assert done["prompt"] == "rain on a street"
    assert done["file"].endswith(f"{job['id']}.webm")
    kept = hub.get(f"/v1/visual/jobs/{job['id']}/file")
    assert kept.headers["content-type"] == "video/webm" and kept.content == loud.read_bytes()
    clip = data_paths().media / done["file"]
    assert "Audio: aac" in _said(program, "-i", str(clip.with_suffix(".mp4")))  # made at once, with the sound
    assert clip.with_suffix(".jpg").read_bytes()[:2] == b"\xff\xd8"
    played = hub.get(f"/v1/visual/jobs/{job['id']}/mp4")
    assert played.status_code == 200 and played.headers["content-type"] == "video/mp4"
    looked = asyncio.run(hub.app.state.visual.look(done, 6, 32))  # a WebM's frames: as many as the job asked for
    assert len(looked) == 6 and all(frame[:2] == b"\xff\xd8" for frame in looked)

    FakeDrawSwap.filmed = None  # the switch off: silent, a WebP, nothing said of the sound
    quiet = hub.post("/v1/visual/videos", json={"prompt": "rain", "model": "talk", "seconds": 2, "sound": False,
                                                "sound_words": "heavy rain"}).json()["job"]  # fmt: skip
    assert (quiet["sound"], quiet["sound_words"]) == (0, "")
    assert _wait(hub, quiet["id"], "done", "failed")["file"].endswith(".webp")
    assert (FakeDrawSwap.video[-1]["output_format"], FakeDrawSwap.video[-1]["prompt"]) == ("webp", "rain")
    plain = hub.post("/v1/visual/videos", json={"prompt": "rain", "model": "clip", "seconds": 2,
                                                "sound_words": "heavy rain"}).json()["job"]  # fmt: skip
    assert (plain["sound"], plain["sound_words"]) == (0, "")  # a model that films no sound
    assert _wait(hub, plain["id"], "done", "failed")["file"].endswith(".webp")
    assert FakeDrawSwap.video[-1]["output_format"] == "webp"

    FakeDrawSwap.filmed = still.read_bytes()  # one picture all along
    stuck = hub.post("/v1/visual/videos", json={"prompt": "rain", "model": "talk", "seconds": 2}).json()["job"]
    failed = _wait(hub, stuck["id"], "done", "failed")
    assert failed["status"] == "failed" and "одна неподвижная картинка" in failed["error"] and not failed["file"]
    assert not list(data_paths().media.rglob(f"{stuck['id']}*"))
    FakeDrawSwap.filmed = WEBP  # asked for a WebM, a WebP came: no video
    wrong = hub.post("/v1/visual/videos", json={"prompt": "rain", "model": "talk", "seconds": 2}).json()["job"]
    assert "ответ без видео" in _wait(hub, wrong["id"], "done", "failed")["error"]
