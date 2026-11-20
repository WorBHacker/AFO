"""Engines on PyTorch (stage 56): put in by uv in AFO's own folder, run in a Windows job. A real hub, a stand-in of
the files' sources (uv's archive, Hugging Face), a stand-in of uv (tests/fake_uv.py) and of the engine's script
(tests/fake_torch_engine.py) - real processes; the environment uv makes is a real one, of this Python."""

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

import psutil
import pytest

from common.paths import models_home
from hub import enhance, ffmpeg, hf, torch_engines
from tests.test_enhance import FFMPEG, a_video_job, described, wait_job
from tests.test_visual import hub  # noqa: F401 - the Studio's stand

FAKE_UV = Path(__file__).with_name("fake_uv.py")
FAKE_ENGINE = Path(__file__).with_name("fake_torch_engine.py")


def uv_archive():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in ("uv.exe", "uvw.exe", "uvx.exe"):
            zf.writestr(name, b"MZ" + name.encode())
    return data.getvalue()


UV_ZIP = uv_archive()
WEIGHTS = b"sam2.1" * 50000


class FakeFiles(BaseHTTPRequestHandler):
    """uv's archive where GitHub keeps it, SAM 2.1's weights where Hugging Face does; a wrong byte when asked."""

    corrupt: ClassVar[set] = set()
    asked: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).asked.append(self.path)
        spec = torch_engines.ENGINES["select"]["weights"][0]
        if self.path == "/" + torch_engines.UV["url"].split("://", 1)[1]:
            body = UV_ZIP
        elif self.path == f"/{spec['hf']}/resolve/{spec['revision']}/{spec['path']}":
            body = WEIGHTS
        else:
            self.send_response(404)
            self.end_headers()
            return
        if self.path.rsplit("/", 1)[-1] in self.corrupt:
            body = b"X" + body[1:]
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stand(hub, monkeypatch, tmp_path):  # noqa: F811
    """The hub with the stand-ins; the pins - the stand-ins' files'; an NVIDIA card of 16 GB; what uv and the engine
    were asked - `hub.uv()`, `hub.engine()`; uv's own settings of the person - which must not reach it."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeFiles)
    FakeFiles.corrupt, FakeFiles.asked = set(), []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv(torch_engines.SOURCES_ENV, base)
    monkeypatch.setenv(hf.BASE_ENV, base)
    monkeypatch.setitem(torch_engines.UV, "size", len(UV_ZIP))
    monkeypatch.setitem(torch_engines.UV, "sha256", hashlib.sha256(UV_ZIP).hexdigest())
    spec = torch_engines.ENGINES["select"]
    weight = spec["weights"][0] | {"size": len(WEIGHTS), "sha256": hashlib.sha256(WEIGHTS).hexdigest()}
    monkeypatch.setitem(torch_engines.ENGINES, "select", spec | {"weights": (weight,)})
    monkeypatch.setattr(torch_engines, "command", lambda program: [sys.executable, str(FAKE_UV)])
    monkeypatch.setattr(torch_engines, "script_of", lambda engine: FAKE_ENGINE)
    monkeypatch.setattr(torch_engines, "LOOK_S", 0.05)
    monkeypatch.setattr(enhance, "TICK_S", 0.05)
    monkeypatch.setenv("UV_INDEX_URL", "http://elsewhere.invalid/simple")  # the person's own: not for AFO's uv
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "someone-elses-env"))
    for name in ("uv", "engine"):
        monkeypatch.setenv(f"FAKE_{name.upper()}_LOG", str(tmp_path / f"{name}.jsonl"))

    def read(name):
        log = tmp_path / f"{name}.jsonl"
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []

    hub.uv, hub.engine = (lambda: read("uv")), (lambda: read("engine"))
    hub.app.state.enhance.machine = lambda: {"build": "cuda12", "memory_gib": 15.9}
    yield hub
    server.shutdown()


def state(hub):  # noqa: F811
    return next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "select")


def wait_state(hub, *states, timeout=60.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = state(hub)
        if found["state"] in states:
            return found
        time.sleep(0.05)
    raise AssertionError(f"select is still {found}")


def inside(path, folder):
    return Path(path).resolve().is_relative_to(Path(folder).resolve())


def test_an_engine_on_pytorch_is_put_in_by_uv_in_afos_folder_and_nothing_of_it_is_left_outside(stand, tmp_path):
    """56.2-56.3: «Скачать» of «Выделение предмета» - uv's archive (sha256), its own Python, an environment: PyTorch by
    its URL and sha256, the libraries by their pins, SAM 2.1's code built without its CUDA extension, the weights
    (sha256), then a look that it starts. Every call of uv is given AFO's folders - the cache, its Pythons - runs in
    AFO's folder, with `--no-bin --no-registry --no-project`; the person's own uv settings do not reach it. The size
    told before is what it brings; the second time PyTorch is in the cache - not counted. «Стоп» kills uv with what
    it started; «Удалить» of the last engine leaves nothing of them. No NVIDIA card - not offered."""
    hub = stand  # noqa: F811
    base, spec = torch_engines.home(), torch_engines.ENGINES["select"]
    before = state(hub)
    wanted = (len(UV_ZIP) + torch_engines.PYTHON_SIZE + sum(t["size"] for t in torch_engines.TORCH)
              + torch_engines.LIBRARIES_SIZE + spec["code"]["size"] + len(WEIGHTS))  # fmt: skip
    assert before["state"] == "missing" and before["size"] == wanted and before["disk"] > before["size"]
    assert before["name"] == "Выделение предмета"

    FakeFiles.corrupt = {torch_engines.UV["name"]}
    assert hub.post("/v1/enhance/parts/select").status_code == 200
    broken = wait_state(hub, "missing", "ready")
    assert broken["state"] == "missing" and "sha256" in broken["error"] and not torch_engines.uv_exe(base).exists()
    FakeFiles.corrupt = set()

    assert hub.post("/v1/enhance/parts/select").status_code == 200
    ready = wait_state(hub, "ready", "missing")
    assert ready["state"] == "ready", ready["error"]
    calls = hub.uv()
    assert [c["args"][:2] for c in calls] == [["python", "install"], ["venv", str(base / "select" / "env")],
                                              ["pip", "install"], ["pip", "install"]]  # fmt: skip
    for call in calls:  # AFO's folders, AFO's folder, none of the person's settings
        assert Path(call["cwd"]) == base
        assert call["env"]["UV_CACHE_DIR"] == str(base / "cache")
        assert call["env"]["UV_PYTHON_INSTALL_DIR"] == str(base / "python")
        assert call["env"]["UV_NO_CONFIG"] == "1" and "UV_INDEX_URL" not in call["env"]
        # (56.6) its temporary files - in AFO's folder, a folder a call, gone after it
        assert inside(call["env"]["TEMP"], base / "tmp") and call["env"]["TMP"] == call["env"]["TEMP"]
        assert not Path(call["env"]["TEMP"]).exists()
    assert len({c["env"]["TEMP"] for c in calls}) == len(calls)
    assert {"--no-bin", "--no-registry"} <= set(calls[0]["args"]) and "--no-project" in calls[1]["args"]
    torch_line, code_call = calls[2]["args"], calls[3]
    assert "--no-build-isolation" not in torch_line and "--no-build-isolation" in code_call["args"]
    assert code_call["env"]["SAM2_BUILD_CUDA"] == "0"
    put = (base / "select" / "env" / "afo-fake-uv.txt").read_text(encoding="utf-8").splitlines()
    for t in torch_engines.TORCH:  # PyTorch by its address and sum - uv checks it, and keeps it in its cache by it
        assert f"{t['name']} @ {torch_engines.source(t['url'])}#sha256={t['sha256']}" in put
    assert f"sam-2 @ {torch_engines.source(spec['code']['url'])}#sha256={spec['code']['sha256']}" in put
    pins = (base / "select" / "pins.txt").read_text(encoding="utf-8").split()
    assert "numpy==2.2.6" in pins and "hydra-core==1.3.8" in pins and calls[2]["args"][-2:] == ["-c", str(
        base / "select" / "pins.txt")]  # fmt: skip

    looked = hub.engine()[-1]  # the look: the engine's Python, isolated, in its folder, its caches there, offline
    assert looked["args"] == ["--check"] and looked["isolated"]
    assert Path(looked["python"]).resolve().is_relative_to((base / "select" / "env").resolve())
    assert Path(looked["cwd"]) == base / "select" / "home"
    assert all(inside(looked["env"][k], base / "select" / "home") for k in ("HF_HOME", "TORCH_HOME", "MPLCONFIGDIR"))
    assert looked["env"]["HF_HUB_OFFLINE"] == "1" and looked["env"]["VIRTUAL_ENV"] is None
    assert inside(looked["env"]["TEMP"], base / "select" / "tmp") and not Path(looked["env"]["TEMP"]).exists()
    weight = torch_engines.weight_file(models_home(), "select", spec["weights"][0])
    assert weight.read_bytes() == WEIGHTS and not (base / "downloads").exists()
    told = [p for kind, p in hub.seen if kind == "enhance.changed"]
    going = [next(x for x in p["parts"] if x["id"] == "select") for p in told]
    assert any(g["state"] == "loading" for g in going) and going[-1]["state"] == "ready"
    shared = torch_engines.size("select")  # uv, its Python and PyTorch are here: another engine would not fetch them
    assert shared == torch_engines.LIBRARIES_SIZE + spec["code"]["size"] + len(WEIGHTS)
    assert state(hub)["size"] == shared - len(WEIGHTS)  # (56.6) nor its weights, whole here: not counted
    env_files = sum(f.stat().st_size for f in (base / "select" / "env").rglob("*") if f.is_file())
    assert state(hub)["disk"] >= env_files + len(WEIGHTS) and state(hub)["disk"] != before["disk"]  # as measured
    began = [p for kind, p in hub.seen if kind == "enhance.changed"]
    weight_kept = weight.read_bytes()
    engines_dir = torch_engines.engine_dir(base, "select")
    (engines_dir / "ready.json").unlink()  # put in again with its weights here: they are not fetched, nor counted
    asked = len(FakeFiles.asked)
    assert hub.post("/v1/enhance/parts/select").status_code == 200
    assert wait_state(hub, "ready", "missing")["state"] == "ready" and weight.read_bytes() == weight_kept
    assert not FakeFiles.asked[asked:]
    again = [next(x for x in p["parts"] if x["id"] == "select") for kind, p in hub.seen if kind == "enhance.changed"]
    totals = {g["total"] for g in again[len(began) :] if g["state"] == "loading"}
    assert totals == {torch_engines.LIBRARIES_SIZE + spec["code"]["size"]}

    assert hub.delete("/v1/enhance/parts/select").status_code == 200
    assert state(hub)["state"] == "missing" and not base.exists() and not weight.exists()

    pid_file = tmp_path / "uv-child.pid"
    with pytest.MonkeyPatch.context() as slow:
        slow.setenv("FAKE_UV_SLOW", "60")
        slow.setenv("FAKE_UV_CHILD", str(pid_file))
        hub.post("/v1/enhance/parts/select")
        deadline = time.monotonic() + 30
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pid_file.read_text())
        began = time.monotonic()
        stopped = hub.post("/v1/enhance/parts/select/stop").json()
        assert next(p for p in stopped["parts"] if p["id"] == "select")["state"] == "missing"
        assert time.monotonic() - began < 15
        assert not psutil.pid_exists(child)  # uv's own child went with it: the job
        assert not Path(hub.uv()[-1]["env"]["TEMP"]).exists()  # and what it left in its temporary folder
    fetched, called = len(FakeFiles.asked), len(hub.uv())
    assert hub.post("/v1/enhance/parts/select").status_code == 200  # again: goes on from what came
    assert wait_state(hub, "ready", "missing")["state"] == "ready"
    assert not any(p.endswith(torch_engines.UV["name"]) for p in FakeFiles.asked[fetched:])  # uv is here
    assert [c["args"][0] for c in hub.uv()[called:]] == ["venv", "pip", "pip"]  # and its Python

    hub.delete("/v1/enhance/parts/select")
    hub.app.state.enhance.machine = lambda: {"build": "vulkan", "memory_gib": 16.0}
    other = state(hub)
    assert other["state"] == "unavailable" and "NVIDIA" in other["why"]
    refused = hub.post("/v1/enhance/parts/select")
    assert refused.status_code == 422 and "NVIDIA" in refused.json()["detail"]


def value(args, flag):
    return args[args.index(flag) + 1]


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_a_video_keeps_only_the_object_clicked_and_the_engine_runs_in_its_folder_in_a_job(stand, tmp_path,
                                                                                          monkeypatch):  # fmt: skip
    """56.4: «Только предмет» - a job of the Studio's queue: ffmpeg takes the video apart, the engine on PyTorch gets
    its frames, the frame clicked (a share of the video - its frame by number) and the point (shares of the frame);
    it runs with its own Python, isolated, in its folder; the frames it gives back become the new video, its sound
    with it. ✕ - the engine killed with what it started, nothing left. A card too small - said plainly; another
    failure - in the engine's words. Not put in, a point outside the frame - refused at once."""
    hub = stand  # noqa: F811
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: Path(FFMPEG))
    media, base = hub.app.state.visual.media, torch_engines.home()
    source_id = a_video_job(hub)  # 72 frames, 24 a second, with sound
    clicked = {"share": 0.5, "x": 0.25, "y": 0.75}
    refused = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked})
    assert refused.status_code == 422 and "не установлено" in refused.json()["detail"]
    hub.post("/v1/enhance/parts/select")
    assert wait_state(hub, "ready", "missing")["state"] == "ready"
    outside = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked | {"x": 1.5}})
    assert outside.status_code == 422

    asked = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked})
    assert asked.status_code == 200, asked.text
    job = asked.json()["job"]
    assert job["enhance"]["select"] == clicked and job["enhance"]["size"] is None
    done = wait_job(hub, job["id"], "done", "failed", "cancelled")
    assert done["status"] == "done", done
    run = hub.engine()[-1]
    assert value(run["args"], "--at") == "36" and value(run["args"], "--x") == "0.2500"  # frame 36 of 0..71
    assert value(run["args"], "--y") == "0.7500"
    weight = torch_engines.weight_file(models_home(), "select", torch_engines.ENGINES["select"]["weights"][0])
    assert value(run["args"], "--weights") == str(weight) and run["isolated"]
    assert Path(run["cwd"]) == base / "select" / "home"
    assert Path(run["python"]).resolve().is_relative_to((base / "select" / "env").resolve())
    assert inside(run["env"]["TEMP"], base / "select" / "tmp") and not Path(run["env"]["TEMP"]).exists()
    shown = described(media / done["file"])
    assert shown["size"] == (64, 64) and shown["fps"] == 24 and shown["sound"] and abs(shown["seconds"] - 3) < 0.1
    assert done["frames"] == 72
    steps = [p["step"] for kind, p in hub.seen if kind == "visual.progress" and p["id"] == job["id"]]
    assert steps == [100]
    assert not [p for p in media.rglob("*") if p.name.startswith(".")]

    pid_file = tmp_path / "engine-child.pid"
    monkeypatch.setenv("FAKE_ENGINE_SLOW", "60")
    monkeypatch.setenv("FAKE_ENGINE_CHILD", str(pid_file))
    stopping = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked}).json()["job"]["id"]
    deadline = time.monotonic() + 30
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    child = int(pid_file.read_text())
    began = time.monotonic()
    assert hub.post(f"/v1/visual/jobs/{stopping}/cancel").status_code == 200
    stopped = wait_job(hub, stopping, "cancelled", "failed", "done")
    assert stopped["status"] == "cancelled" and stopped["error"] == "" and time.monotonic() - began < 15
    assert not psutil.pid_exists(child)  # what the engine started went with it: the job
    assert not list(media.glob(f"*/{stopping}*")) and not [p for p in media.rglob("*") if p.name.startswith(".")]
    killed = hub.engine()[-1]["env"]["TEMP"]  # (56.6) its frames in its temporary folder: gone with it
    assert inside(killed, base / "select" / "tmp") and not Path(killed).exists()
    monkeypatch.delenv("FAKE_ENGINE_SLOW")

    monkeypatch.setenv("FAKE_ENGINE_FAIL", "oom")
    small = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked}).json()["job"]["id"]
    failed = wait_job(hub, small, "failed", "done", "cancelled")
    assert failed["status"] == "failed" and "не хватило памяти видеокарты" in failed["error"]
    monkeypatch.setenv("FAKE_ENGINE_FAIL", "ModuleNotFoundError: No module named 'sam2'")
    broken = hub.post("/v1/visual/improve", json={"from_job": source_id, "select": clicked}).json()["job"]["id"]
    failed = wait_job(hub, broken, "failed", "done", "cancelled")
    assert "No module named 'sam2'" in failed["error"] and "Выделение предмета" in failed["error"]
