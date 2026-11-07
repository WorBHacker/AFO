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


# --- the pictures people ask for (29.4): a hub, and llama-swap with a picture model behind it ---


class FakeDrawSwap(BaseHTTPRequestHandler):
    """llama-swap with sd-server behind it, as the hub's pictures use it: a synchronous txt2img drawn in steps, its
    progress through /upstream, «Стоп» as unloading the model (the picture in flight ends with 502)."""

    steps_s = 0.05  # a step's time
    answer = "picture"  # picture | blank | fail
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
        cls.asked.append(body)
        cls.stopped, cls.total = False, body["steps"]
        for step in range(1, cls.total + 1):
            cls.step = step
            time.sleep(cls.steps_s)
            if cls.stopped:
                self._send(502, {"error": "upstream exited"})
                return
        cls.step = cls.total = 0
        if cls.answer == "fail":
            self._send(500, {"error": "CUDA error: out of memory"})
            return
        picture = PICTURE if cls.answer == "picture" else GREY
        self._send(200, {"images": [base64.b64encode(picture).decode()], "info": "{}"})


_rnd = random.Random(5)
PICTURE = png(32, 32, lambda x, y: (_rnd.randrange(256), _rnd.randrange(256), _rnd.randrange(256)))
GREY = png(32, 32, lambda x, y: (128, 128, 128))
DRAW_PLAN = {"engine": "sd", "set": "draw", "tier": "main", "size": 512, "flags": ["--cfg-scale", "1", "--steps", "4"]}


@pytest.fixture
def hub(monkeypatch):
    FakeDrawSwap.steps_s, FakeDrawSwap.answer, FakeDrawSwap.asked, FakeDrawSwap.unloaded = 0.05, "picture", [], []
    FakeDrawSwap.step = FakeDrawSwap.total = 0
    swap = ThreadingHTTPServer(("127.0.0.1", 0), FakeDrawSwap)
    threading.Thread(target=swap.serve_forever, daemon=True).start()
    monkeypatch.setattr(visual, "STEP_S", 0.05)
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
    assert picture.status_code == 200 and picture.content == PICTURE and picture.headers["content-type"] == "image/png"
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
    assert hub.get(f"/v1/visual/jobs/{job['id']}/file").status_code == 404


@pytest.mark.parametrize(("answer", "words"), [("blank", "пустой"), ("fail", "движок ответил 500")])
def test_a_picture_that_does_not_come_out_says_why(hub, answer, words):
    _install(hub)
    FakeDrawSwap.answer = answer
    job = hub.post("/v1/visual/images", json={"prompt": "a fox"}).json()["job"]
    failed = _wait(hub, job["id"], "failed", "done")
    assert failed["status"] == "failed" and words in failed["error"] and not failed["tries"][0]["ok"]


def test_pictures_a_stopped_hub_left_going_fail(hub):
    _install(hub)
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, prompt, model_id, width, height, seed, status) VALUES "
        "('left', 'a fox', 'draw', 512, 512, 1, 'running')"))  # fmt: skip
    assert hub.portal.call(hub.app.state.visual.start) == 1
    left = hub.get("/v1/visual/jobs/left").json()["job"]
    assert left["status"] == "failed" and "перезапускался" in left["error"]
