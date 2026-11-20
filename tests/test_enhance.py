"""«Улучшить ролик» (stage 54): its programs fetched as parts, and run. A real hub, stand-ins of GitHub and Hugging
Face, and a stand-in of the programs - a real process that writes real files (tests/fake_enhance.py)."""

import asyncio
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from common.paths import engines_home, models_home
from hub import engines, enhance, ffmpeg, hf
from tests.test_visual import hub  # noqa: F401 - the Studio's stand

FFMPEG = shutil.which("ffmpeg")

FAKE = Path(__file__).with_name("fake_enhance.py")
RIFE_ROOT = "rife-ncnn-vulkan-refs/heads/master-windows/"  # as TNTwise's archive nests it (54.1)


def archive(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in files:
            zf.writestr(name, b"MZ" + name.encode())
    return data.getvalue()


SD_ARCHIVE = enhance.PARTS["detail"]["files"][0]["name"]
ARCHIVES = {  # name -> (its repo and tag, its bytes, whether GitHub gives its sum)
    "windows.zip": ("TNTwise/rife-ncnn-vulkan", "20250112", archive(
        [RIFE_ROOT + n for n in ("rife-ncnn-vulkan.exe", "vcomp140.dll", "LICENSE", "README.md",
                                 "rife-v4.26/flownet.param", "rife-v4.26/flownet.bin", "rife-v4.25/flownet.bin")]),
        False),
    "realesrgan-ncnn-vulkan-20220424-windows.zip": ("xinntao/Real-ESRGAN", "v0.2.5.0", archive(
        ["realesrgan-ncnn-vulkan.exe", "vcomp140.dll", "input.jpg", "models/realesrgan-x4plus.param",
         "models/realesrgan-x4plus.bin", "models/realesr-animevideov3-x2.bin"]), False),
    SD_ARCHIVE: (engines.SD_CPP, enhance.SD_TAG, archive(["sd-cli.exe", "sd-server.exe", "ggml-cuda.dll"]), True),
    engines.SD_RUNTIME: (engines.SD_CPP, enhance.SD_TAG, archive(enhance.RUNTIME_DLLS), True),
}  # fmt: skip
WEIGHTS = {spec["path"]: spec["role"].encode() * 70000 for spec in enhance.weights("detail")}


class FakeSources(BaseHTTPRequestHandler):
    """GitHub's releases of the three programs with their archives, and RelayVSR's files on Hugging Face."""

    port = 0
    corrupt: ClassVar[set] = set()  # the archives that come with a wrong byte
    asked: ClassVar[list] = []

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
        type(self).asked.append(self.path)
        url = f"http://127.0.0.1:{self.port}/download/"
        for repo, tag in {(r, t) for r, t, _d, _s in ARCHIVES.values()}:
            if self.path == f"/repos/{repo}/releases/tags/{tag}":
                assets = [{"name": name, "size": len(data), "browser_download_url": url + name,
                           "digest": "sha256:" + hashlib.sha256(data).hexdigest() if summed else None}
                          for name, (r, t, data, summed) in ARCHIVES.items() if (r, t) == (repo, tag)]  # fmt: skip
                return self._send({"tag_name": tag, "assets": assets})
        if self.path.startswith("/download/") and (name := self.path.removeprefix("/download/")) in ARCHIVES:
            data = ARCHIVES[name][2]
            return self._send(b"X" + data[1:] if name in self.corrupt else data, "application/zip")
        for spec in enhance.weights("detail"):
            if self.path == f"/{spec['hf']}/resolve/{spec['revision']}/{spec['path']}":
                return self._send(WEIGHTS[spec["path"]], "application/octet-stream")
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def stand(hub, monkeypatch, tmp_path):  # noqa: F811
    """The hub with the stand-ins: GitHub, Hugging Face, the programs; the pins - the stand-ins' files'; a card of 16
    GB; what the programs were asked - `hub.asked()`."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSources)
    FakeSources.port, FakeSources.corrupt, FakeSources.asked = server.server_port, set(), []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv(hf.BASE_ENV, f"http://127.0.0.1:{server.server_port}")
    by_name = {name: data for name, (_r, _t, data, _s) in ARCHIVES.items()}
    for part, spec in enhance.PARTS.items():
        if enhance.on_torch(part):  # (56) an engine on PyTorch: tests/test_torch_engines.py
            continue
        files = tuple(f | {"size": len(by_name[f["name"]]), "sha256": hashlib.sha256(by_name[f["name"]]).hexdigest()}
                      if "repo" in f else
                      f | {"size": len(WEIGHTS[f["path"]]), "sha256": hashlib.sha256(WEIGHTS[f["path"]]).hexdigest()}
                      for f in spec["files"])  # fmt: skip
        monkeypatch.setitem(enhance.PARTS, part, spec | {"files": files})
    monkeypatch.setattr(enhance, "command", lambda program: [sys.executable, str(FAKE), program.name])
    monkeypatch.setattr(enhance, "TICK_S", 0.05)
    log = tmp_path / "programs.jsonl"
    monkeypatch.setenv("FAKE_ENHANCE_LOG", str(log))
    hub.app.state.enhance.machine = lambda: {"build": "cuda12", "memory_gib": 15.9}
    hub.asked = lambda: [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() \
        else []  # fmt: skip
    yield hub
    server.shutdown()


def part(state, name):
    return next(p for p in state["parts"] if p["id"] == name)


def wait_part(hub, name, *states, timeout=20.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = part(hub.get("/v1/enhance").json(), name)
        if found["state"] in states:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{name} is still {found}")


def install(hub, name):  # noqa: F811
    assert hub.post(f"/v1/enhance/parts/{name}").status_code == 200
    return wait_part(hub, name, "ready", "missing")


def files_in(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_the_programs_that_improve_a_video_are_fetched_as_parts_and_keep_only_what_they_run(stand):
    """54.2: «Скачать» fetches a part's archive (pinned by size and sha256: RIFE's and Real-ESRGAN's have no sum on
    GitHub - ours is checked), keeps of it the program and the model it runs, and nothing of the download. RelayVSR's
    part is the newer sd.cpp build - its CUDA libraries linked from the filming build's folder, their archive not
    fetched - and the weights; on a card of less than 16 GB it is not offered. A wrong byte - said, nothing put in."""
    hub = stand  # noqa: F811
    service = hub.app.state.enhance
    was = hub.get("/v1/enhance").json()
    sizes = {name: len(data) for name, (_r, _t, data, _s) in ARCHIVES.items()}
    weights = sum(len(w) for w in WEIGHTS.values())
    assert [(p["id"], p["state"], p["size"]) for p in was["parts"][:3]] == [
        ("smooth", "missing", sizes["windows.zip"]),
        ("sharp", "missing", sizes["realesrgan-ncnn-vulkan-20220424-windows.zip"]),
        ("detail", "missing", sizes[SD_ARCHIVE] + sizes[engines.SD_RUNTIME] + weights)]  # fmt: skip
    assert [p["id"] for p in was["parts"][3:]] == ["select"]  # (56) the engine on PyTorch after them
    assert hub.post("/v1/enhance/parts/sparkle").status_code == 404

    FakeSources.corrupt = {"windows.zip"}
    broken = install(hub, "smooth")
    assert broken["state"] == "missing" and "sha256" in broken["error"]
    assert enhance.find(service.tools, "smooth") is None and not (service.tools / "enhance" / "smooth.part").exists()
    FakeSources.corrupt = set()
    assert install(hub, "smooth")["state"] == "ready"
    assert files_in(enhance.folder(service.tools, "smooth")) == [
        "LICENSE", "rife-ncnn-vulkan.exe", "rife-v4.26/flownet.bin", "rife-v4.26/flownet.param", "vcomp140.dll"
    ]  # fmt: skip
    assert not (service.tools / "downloads" / "enhance" / "smooth").exists()
    told = [payload for kind, payload in hub.seen if kind == "enhance.changed"]
    assert any(part(p, "smooth")["state"] == "loading" for p in told) and part(told[-1], "smooth")["state"] == "ready"

    assert install(hub, "sharp")["state"] == "ready"
    assert files_in(enhance.folder(service.tools, "sharp")) == [
        "models/realesrgan-x4plus.bin", "models/realesrgan-x4plus.param", "realesrgan-ncnn-vulkan.exe",
        "vcomp140.dll"]  # fmt: skip

    filming = engines_home() / "stable-diffusion.cpp" / f"{engines.SD_TAG}-cuda12"  # the filming build's libraries
    filming.mkdir(parents=True)
    for name in enhance.RUNTIME_DLLS:
        (filming / name).write_bytes(b"MZ" + name.encode())
    assert part(hub.get("/v1/enhance").json(), "detail")["size"] == sizes[SD_ARCHIVE] + weights
    FakeSources.asked.clear()
    assert install(hub, "detail")["state"] == "ready"
    assert not any(engines.SD_RUNTIME in path for path in FakeSources.asked)
    built = enhance.folder(service.tools, "detail")
    assert files_in(built) == sorted(["ggml-cuda.dll", "sd-cli.exe", "sd-server.exe", *enhance.RUNTIME_DLLS])
    assert (built / "cudart64_12.dll").stat().st_ino == (filming / "cudart64_12.dll").stat().st_ino  # a hard link
    for spec in enhance.weights("detail"):
        assert enhance.weight_file(models_home(), spec).read_bytes() == WEIGHTS[spec["path"]]

    assert hub.delete("/v1/enhance/parts/detail").json()["parts"][2]["state"] == "missing"
    assert not built.exists() and not any(enhance.weight_file(models_home(), w).exists()
                                          for w in enhance.weights("detail"))  # fmt: skip
    engines.remove_tree(filming)  # no filming build: the libraries' own archive
    assert install(hub, "detail")["state"] == "ready"
    assert any(engines.SD_RUNTIME in path for path in FakeSources.asked)
    assert (built / "cublasLt64_12.dll").read_bytes() == b"MZcublasLt64_12.dll"

    service.machine = lambda: {"build": "vulkan", "memory_gib": 16.0, "card": "Radeon"}
    hub.delete("/v1/enhance/parts/detail")
    small = part(hub.get("/v1/enhance").json(), "detail")
    assert small["state"] == "unavailable" and "NVIDIA" in small["why"]
    refused = hub.post("/v1/enhance/parts/detail")
    assert refused.status_code == 422 and "NVIDIA" in refused.json()["detail"]


def test_a_part_runs_its_program_in_its_folder_and_stop_kills_it(stand, tmp_path, monkeypatch):
    """54.2: a piece is made by the part's program - a real process, in the program's folder (its model lies there);
    «Стоп» kills it at once; its failure is said in its words; a card too small for RelayVSR is said plainly; a part
    not fetched is said so."""
    hub = stand  # noqa: F811
    service = hub.app.state.enhance
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    target.mkdir()
    for n in range(1, 4):
        (source / f"{n:06d}.png").write_bytes(b"frame %d" % n)

    def run(name, args, stop_after=None):
        async def go():
            cancel = asyncio.Event()
            if stop_after is not None:
                asyncio.get_running_loop().call_later(stop_after, cancel.set)
            return await service.run(name, args, cancel)

        return hub.portal.call(go)

    with pytest.raises(enhance.EnhanceError) as missing:
        run("smooth", enhance.smooth_args(source, target, 7))
    assert missing.value.status == 422 and "Плавность" in str(missing.value)
    install(hub, "smooth")
    run("smooth", enhance.smooth_args(source, target, 7))
    assert [f.read_bytes() for f in sorted(target.iterdir())] == [b"frame %d" % n for n in (1, 1, 2, 2, 2, 3, 3)]
    asked = hub.asked()[-1]
    assert asked["program"] == "rife-ncnn-vulkan.exe" and Path(asked["cwd"]) == enhance.folder(service.tools, "smooth")
    assert asked["args"][asked["args"].index("-m") + 1] == enhance.RIFE_MODEL

    monkeypatch.setenv("FAKE_ENHANCE_SLOW", "30")
    began = time.monotonic()
    with pytest.raises(enhance.EnhanceError, match="остановлено"):
        run("smooth", enhance.smooth_args(source, target, 7), stop_after=0.5)
    assert time.monotonic() - began < 10
    monkeypatch.delenv("FAKE_ENHANCE_SLOW")

    monkeypatch.setenv("FAKE_ENHANCE_FAIL", "vkCreateDevice failed -3")
    with pytest.raises(enhance.EnhanceError) as failed:
        run("smooth", enhance.smooth_args(source, target, 7))
    assert failed.value.status == 502 and "vkCreateDevice failed -3" in str(failed.value)

    install(hub, "detail")
    monkeypatch.setenv("FAKE_ENHANCE_FAIL", "oom")
    with pytest.raises(enhance.EnhanceError, match="не хватило памяти видеокарты"):
        run("detail", enhance.detail_args(models_home(), source, target, frames=3, fps=24))
    monkeypatch.delenv("FAKE_ENHANCE_FAIL")
    out = tmp_path / "up"
    out.mkdir()
    run("detail", enhance.detail_args(models_home(), source, out, frames=3, fps=24))
    assert sorted(f.name for f in out.iterdir()) == ["000001.png", "000002.png", "000003.png"]
    asked = hub.asked()[-1]["args"]
    assert asked[asked.index("--vsr-window-size") + 1] == "3"  # the window takes the whole piece (54.1)


def two_shots(target, side=64):
    """A video as a film may be: 48 frames of a moving picture, a hard cut, 24 frames of red - 24 a second, a tone of
    440 Hz all along."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={side}x{side}:rate=24:duration=2", "-f", "lavfi", "-i",
                    f"color=c=red:size={side}x{side}:rate=24:duration=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=3", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-g", "1", "-c:a", "aac", str(target)], check=True, timeout=60)  # fmt: skip


def described(video):
    """What ffmpeg says of a video: its frames a second, its size, whether it has sound, its length."""
    said = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, timeout=60,
                          check=False).stderr.decode("utf-8", "replace")  # fmt: skip
    picture = re.search(r"Video: .*?, (\d+)x(\d+).*?, ([\d.]+) fps", said)
    hours, minutes, seconds = re.search(r"Duration: (\d+):(\d+):([\d.]+)", said).groups()
    return {"fps": float(picture.group(3)), "size": (int(picture.group(1)), int(picture.group(2))),
            "sound": " Audio: " in said, "seconds": int(hours) * 3600 + int(minutes) * 60 + float(seconds)}  # fmt: skip


def frames_of(video, into):
    into.mkdir()
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(video), "-fps_mode", "passthrough",
                    str(into / "%d.png")], check=True, timeout=60)  # fmt: skip
    return sorted(into.iterdir(), key=lambda f: int(f.stem))


def red(frame):
    from PySide6.QtGui import QImage

    image = QImage(str(frame))
    pixel = image.pixelColor(image.width() // 2, image.height() // 2)
    return pixel.red() > 200 and pixel.green() < 60 and pixel.blue() < 60


def make(hub, source, target, *, stop_after=None, **how):  # noqa: F811
    shares = []

    async def told(share):
        shares.append(share)

    async def go():
        cancel = asyncio.Event()
        if stop_after is not None:
            asyncio.get_running_loop().call_later(stop_after, cancel.set)
        return await hub.app.state.enhance.make(source, target, cancel, ffmpeg=Path(FFMPEG), told=told, **how)

    hub.portal.call(go)
    return shares


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_a_video_is_made_smoother_or_slower_shot_by_shot_and_keeps_its_sound(stand, tmp_path, monkeypatch):
    """54.3: «60 кадров в секунду» - ffmpeg takes the video apart into frames, finds where a new shot begins, RIFE
    makes the frames between shot by shot (none across the cut), ffmpeg puts them together at 60 a second, the
    video's sound with them; the same length. «Замедлить ×4» - four times the frames at the video's own rate, four
    times longer, without sound. «Стоп» - nothing is left, not even the frames."""
    hub = stand  # noqa: F811
    source = tmp_path / "two-shots.mp4"
    two_shots(source)
    assert ffmpeg.cuts(Path(FFMPEG), source) == [48]  # the red shot begins at frame 48
    install(hub, "smooth")

    smooth = tmp_path / "smooth.mp4"
    assert make(hub, source, smooth, fps=24, motion="fps60") == [0.5, 1.0]  # told after each shot
    asked = [a["args"] for a in hub.asked() if a["program"] == "rife-ncnn-vulkan.exe"]
    assert [a[a.index("-n") + 1] for a in asked] == ["120", "60"]  # 48 frames → 2 s at 60; 24 → 1 s
    shown = described(smooth)
    assert shown["fps"] == 60 and shown["sound"] and abs(shown["seconds"] - 3.0) < 0.1
    frames = frames_of(smooth, tmp_path / "smooth")
    assert len(frames) == 180 and not red(frames[119]) and red(frames[120])  # the cut where it was: none across
    assert not list(tmp_path.glob(".*.work"))

    slow = tmp_path / "slow.mp4"
    make(hub, source, slow, fps=24, motion="slow4")
    shown = described(slow)
    assert shown["fps"] == 24 and not shown["sound"] and abs(shown["seconds"] - 12.0) < 0.1
    assert len(frames_of(slow, tmp_path / "slow")) == 288

    with pytest.raises(enhance.EnhanceError, match="уже 60"):
        make(hub, smooth, tmp_path / "again.mp4", fps=60, motion="fps60")
    monkeypatch.setenv("FAKE_ENHANCE_SLOW", "30")
    stopped = tmp_path / "stopped.mp4"
    began = time.monotonic()
    with pytest.raises(enhance.EnhanceError, match="остановлено"):
        make(hub, source, stopped, fps=24, motion="fps60", stop_after=1.0)
    assert time.monotonic() - began < 15
    assert not stopped.exists() and not list(tmp_path.glob(".*.work")) and not list(tmp_path.glob("*.part"))


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_a_video_is_made_twice_as_large_piece_by_piece_then_smoother(stand, tmp_path, monkeypatch):
    """54.4: «Увеличить ×2» - fast: Real-ESRGAN makes each frame of a piece four times larger and ffmpeg brings the
    piece to twice at once; with details: RelayVSR, its window the whole piece. Pieces go shot by shot, a long shot in
    even pieces; the video's sound stays. Larger and smoother at once: enlarged first, then the frames between - at
    the new size. A video larger than RelayVSR fits on the card - said, the fast way named."""
    hub = stand  # noqa: F811
    source = tmp_path / "two-shots.mp4"
    two_shots(source)
    install(hub, "smooth")
    install(hub, "sharp")
    install(hub, "detail")

    both = tmp_path / "both.mp4"
    shares = make(hub, source, both, fps=24, size="sharp", motion="fps60")
    assert shares == [0.25, 0.5, 0.75, 1.0]  # two pieces enlarged, two shots made smoother
    asked = hub.asked()
    assert [a["program"] for a in asked] == ["realesrgan-ncnn-vulkan.exe"] * 2 + ["rife-ncnn-vulkan.exe"] * 2
    assert [a["args"][a["args"].index("-s") + 1] for a in asked[:2]] == ["4", "4"]
    shown = described(both)
    assert shown["size"] == (128, 128) and shown["fps"] == 60 and shown["sound"]
    frames = frames_of(both, tmp_path / "both")
    assert len(frames) == 180 and not red(frames[119]) and red(frames[120])
    assert not list(tmp_path.glob(".*.work"))

    monkeypatch.setattr(enhance, "PIECE_FRAMES", 20)  # a long shot in even pieces
    large = tmp_path / "large.mp4"
    make(hub, source, large, fps=24, size="detail")
    windows = [a["args"][a["args"].index("--vsr-window-size") + 1] for a in hub.asked()
               if a["program"] == "sd-cli.exe"]  # fmt: skip
    assert windows == ["16", "16", "16", "12", "12"]  # 48 frames in three, 24 in two: none across the cut
    shown = described(large)
    assert shown["size"] == (128, 128) and shown["fps"] == 24 and shown["sound"] and abs(shown["seconds"] - 3) < 0.1
    frames = frames_of(large, tmp_path / "large")
    assert len(frames) == 72 and not red(frames[47]) and red(frames[48])

    monkeypatch.setattr(enhance, "DETAIL_PIXELS", 64 * 64 - 1)
    with pytest.raises(enhance.EnhanceError, match="быстрое увеличение") as refused:
        make(hub, source, tmp_path / "too-large.mp4", fps=24, size="detail")
    assert refused.value.status == 422 and not list(tmp_path.glob(".*.work"))
    with pytest.raises(enhance.EnhanceError, match="выберите"):
        make(hub, source, tmp_path / "nothing.mp4", fps=24)


def a_video_job(hub, *, side=64):  # noqa: F811
    """A video the Studio filmed with sound, as it lies in data/media: the engine's WebM, its poster, its MP4."""
    visual, job_id = hub.app.state.visual, uuid.uuid4().hex[:12]
    (visual.media / "2026-11").mkdir(parents=True, exist_ok=True)
    two_shots(visual.media / f"2026-11/{job_id}.mp4", side)
    (visual.media / f"2026-11/{job_id}.webm").write_bytes(b"\x1a\x45\xdf\xa3 the engine's clip")
    (visual.media / f"2026-11/{job_id}.jpg").write_bytes(b"\xff\xd8\xff poster")
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, kind, prompt, model_id, width, height, seed, status, file, fps, sound, frames) "
        "VALUES (?, 'video', 'a lamp', 'film', ?, ?, 1, 'done', ?, 24, 1, 72)",
        (job_id, side, side, f"2026-11/{job_id}.webm")))  # fmt: skip
    return job_id


def wait_job(hub, job_id, *statuses, timeout=30.0):  # noqa: F811
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = hub.get(f"/v1/visual/jobs/{job_id}").json()["job"]
        if found["status"] in statuses:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{job_id} is still {found}")


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_an_improved_video_is_a_job_of_the_studios_queue_beside_its_source(stand, tmp_path, monkeypatch):
    """54.5: «Улучшить ролик» - a job of the Studio's queue, in turn with the rest: the models llama-swap holds leave
    the card, how far it is goes to the window as a video's steps do, its result a new MP4 with a poster beside, the
    source as it was. A part not fetched, nothing chosen, no such video - refused at once. ✕ - its program killed,
    nothing left. A film's joined video improved - named as the film's own (the cleanup leaves it while the film is),
    listed with the film; slowed - without sound."""
    from hub import director, retention
    from tests.test_visual import FakeDrawSwap

    hub = stand  # noqa: F811
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: Path(FFMPEG))
    media = hub.app.state.visual.media
    source_id = a_video_job(hub)
    source = media / f"2026-11/{source_id}.mp4"
    before = source.read_bytes()

    refused = hub.post("/v1/visual/improve", json={"from_job": source_id, "motion": "fps60"})
    assert refused.status_code == 422 and "не установлено" in refused.json()["detail"]
    install(hub, "smooth")
    install(hub, "sharp")
    assert hub.post("/v1/visual/improve", json={"from_job": source_id}).status_code == 422
    assert hub.post("/v1/visual/improve", json={"from_job": "nothing", "motion": "fps60"}).status_code == 404
    assert hub.post("/v1/visual/improve", json={"from_job": source_id, "motion": "fast"}).status_code == 422

    asked = hub.post("/v1/visual/improve", json={"from_job": source_id, "size": "sharp", "motion": "fps60"})
    assert asked.status_code == 200, asked.text
    job_id = asked.json()["job"]["id"]
    assert asked.json()["job"]["enhance"] == {"source": f"2026-11/{source_id}.mp4", "job": source_id, "fps": 24,
                                              "size": "sharp", "motion": "fps60", "sound": True}  # fmt: skip
    done = wait_job(hub, job_id, "done", "failed", "cancelled")
    assert done["status"] == "done", done
    month = done["file"].split("/")[0]
    assert done["file"] == f"{month}/{job_id}.mp4" and done["frames"] == 180 and done["fps"] == 60
    assert (done["width"], done["height"], done["sound"]) == (128, 128, 1)
    improved = media / done["file"]
    shown = described(improved)
    assert shown["size"] == (128, 128) and shown["fps"] == 60 and shown["sound"] and abs(shown["seconds"] - 3) < 0.1
    assert (media / f"{month}/{job_id}.jpg").read_bytes()[:3] == b"\xff\xd8\xff"  # the poster the lists show
    assert source.read_bytes() == before  # the source as it was
    assert "unload" in FakeDrawSwap.unloaded  # llama-swap's models left the card first
    steps = [p["step"] for kind, p in hub.seen if kind == "visual.progress" and p["id"] == job_id]
    assert steps == [25, 50, 75, 100] and all(p["steps"] == 100 for kind, p in hub.seen
                                              if kind == "visual.progress" and p["id"] == job_id)  # fmt: skip
    served = hub.get(f"/v1/visual/jobs/{job_id}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "video/mp4"
    assert hub.get(f"/v1/visual/jobs/{job_id}/mp4").content == improved.read_bytes()
    assert not [p for p in media.rglob("*") if p.name.startswith(".") or p.suffix == ".part"]

    again = hub.post("/v1/visual/improve", json={"from_job": job_id, "motion": "fps60"})  # an improved one, again
    assert again.status_code == 422 and "уже 60" in again.json()["detail"]

    monkeypatch.setenv("FAKE_ENHANCE_SLOW", "30")
    stopping = hub.post("/v1/visual/improve", json={"from_job": source_id, "motion": "slow2"}).json()["job"]["id"]
    wait_job(hub, stopping, "running")
    time.sleep(0.5)
    began = time.monotonic()
    assert hub.post(f"/v1/visual/jobs/{stopping}/cancel").status_code == 200
    stopped = wait_job(hub, stopping, "cancelled", "failed", "done")
    assert stopped["status"] == "cancelled" and stopped["error"] == "" and time.monotonic() - began < 15
    assert not list(media.glob(f"*/{stopping}*")) and not [p for p in media.rglob("*") if p.name.startswith(".")]
    monkeypatch.delenv("FAKE_ENHANCE_SLOW")

    db = hub.app.state.db
    film_id = db.call(lambda conn: director.new_film(conn, idea="Ролик о лампе", seconds=3, checks=False))["id"]
    joined = f"2026-11/film-{film_id}-joined.mp4"
    shutil.copyfile(source, media / joined)

    def filmed(conn):
        conn.execute("INSERT INTO film_scenes (film_id, n, mode, words, prompt, frames, job_id, status) VALUES "
                     "(?, 1, 'picture', 'Лампа', 'a lamp', 73, ?, 'done')", (film_id, source_id))  # fmt: skip
        director.update_film(conn, film_id, file=joined, status="ready")

    db.call(filmed)
    slow = hub.post("/v1/visual/improve", json={"film": film_id, "motion": "slow2"})
    assert slow.status_code == 200, slow.text
    slowed = wait_job(hub, slow.json()["job"]["id"], "done", "failed", "cancelled")
    assert slowed["status"] == "done" and slowed["film_id"] == film_id and not slowed["sound"]
    assert slowed["file"] == f"{month}/film-{film_id}-{slowed['id']}.mp4" and slowed["frames"] == 144
    shown = described(media / slowed["file"])
    assert shown["fps"] == 24 and not shown["sound"] and abs(shown["seconds"] - 6) < 0.1
    assert retention.owner(Path(slowed["file"]).name) in db.call(director.kept)  # kept as long as the film
    listed = hub.get(f"/v1/films/{film_id}").json()["film"]["improved"]
    assert [(i["id"], i["status"], i["file"]) for i in listed] == [(slowed["id"], "done", slowed["file"])]
    assert hub.post("/v1/visual/improve", json={"film": "nothing", "motion": "slow2"}).status_code == 404

    left = uuid.uuid4().hex[:12]  # the hub went away while a video was being improved
    db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, kind, prompt, model_id, width, height, seed, status, enhance) VALUES "
        "(?, 'video', 'a lamp', 'enhance', 64, 64, 0, 'running', ?)",
        (left, json.dumps({"source": joined}))))  # fmt: skip
    assert hub.portal.call(hub.app.state.visual.start) == 1
    assert wait_job(hub, left, "failed")["error"] == "хаб перезапускался — ролик не улучшен"


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_the_windows_client_improves_a_video_through_the_hub(stand, monkeypatch):
    """54.6: what the window asks, as it asks it (app/hub_client.py) - the parts, «Скачать», «Стоп», «Удалить»,
    «Улучшить», the job - against the real hub; what the hub refuses comes as its words; `enhance.*` reaches the
    window."""
    from app.events import TOPICS
    from app.hub_client import HubClient, HubError
    from hub.events import matches

    hub = stand  # noqa: F811
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: Path(FFMPEG))
    client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
    assert [p["state"] for p in client.enhance()["parts"]] == ["missing", "missing", "missing", "missing"]
    assert client.install_enhance("smooth")["parts"][0]["state"] in ("loading", "ready")
    wait_part(hub, "smooth", "ready")
    assert any(kind == "enhance.changed" for kind, _p in hub.seen) and matches("enhance.changed", tuple(TOPICS))
    source_id = a_video_job(hub)
    with pytest.raises(HubError, match="не установлено"):
        client.visual_improve({"from_job": source_id, "size": "sharp", "motion": None})
    job = client.visual_improve({"from_job": source_id, "size": None, "motion": "slow2"})["job"]
    assert job["enhance"]["motion"] == "slow2" and job["status"] in ("queued", "running")
    done = wait_job(hub, job["id"], "done", "failed", "cancelled")
    assert done["status"] == "done" and client.visual_job(job["id"])["job"]["file"] == done["file"]
    assert client.visual_mp4(job["id"]) == (hub.app.state.visual.media / done["file"]).read_bytes()
    assert client.remove_enhance("smooth")["parts"][0]["state"] == "missing"
    with pytest.raises(HubError, match="нет такой части"):
        client.stop_enhance("nothing")


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_the_frame_to_click_the_object_on_is_the_one_the_engine_is_given(stand, tmp_path, monkeypatch):
    """56.5: «Только предмет» - the window shows a frame of the video to click the object on: a share of its length
    is the frame of the same number the engine gets (`round(share * (frames - 1))`): of 72 frames, 48 moving and
    24 red, 0.66 is frame 47 - still moving -, 0.68 frame 48 - red. A Studio's video and a film's joined one; no
    such video, a share past the video - refused; the hub's words reach the window."""
    from app.hub_client import HubClient, HubError
    from hub import director

    hub = stand  # noqa: F811
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: Path(FFMPEG))
    client = HubClient("http://127.0.0.1:8765", hub.headers["Authorization"].removeprefix("Bearer "), http=hub)
    source_id = a_video_job(hub, side=96)

    def shown(data, name):
        assert data[:3] == b"\xff\xd8\xff"  # a JPEG
        (tmp_path / name).write_bytes(data)
        return red(tmp_path / name)

    assert not shown(client.improve_frame({"from_job": source_id}, 0.66), "47.jpg")
    assert shown(client.improve_frame({"from_job": source_id}, 0.68), "48.jpg")
    assert not shown(client.improve_frame({"from_job": source_id}, 0.0), "0.jpg")
    db, media = hub.app.state.db, hub.app.state.visual.media
    film_id = db.call(lambda conn: director.new_film(conn, idea="Ролик о кружке", seconds=3, checks=False))["id"]
    with pytest.raises(HubError, match="ролик фильма ещё не собран"):
        client.improve_frame({"film": film_id}, 1.0)
    joined = f"2026-11/film-{film_id}-joined.mp4"
    shutil.copyfile(media / f"2026-11/{source_id}.mp4", media / joined)
    db.call(lambda conn: director.update_film(conn, film_id, file=joined, status="ready"))
    assert shown(client.improve_frame({"film": film_id}, 1.0), "film.jpg")
    with pytest.raises(HubError, match="нет такого ролика"):
        client.improve_frame({"from_job": "nothing"}, 0.5)
    past = hub.get("/v1/visual/improve/frame", params={"from_job": source_id, "share": 1.5})
    assert past.status_code == 422


def test_a_video_is_cut_into_pieces_by_its_shots_and_its_length():
    """54.4: the pieces enlarged at once - shot by shot, a long shot in even pieces, a shot of one frame with its
    neighbour (RelayVSR takes no video of one frame)."""
    assert enhance.pieces(72, [48], longest=121) == [(0, 48), (48, 72)]
    assert enhance.pieces(250, [], longest=121) == [(0, 83), (83, 167), (167, 250)]
    assert enhance.pieces(50, [20, 21], longest=121) == [(0, 21), (21, 50)]
    assert enhance.pieces(30, [1], longest=121) == [(0, 30)]
