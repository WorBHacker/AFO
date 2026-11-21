"""«Замена и дорисовка» (stage 58): an object, the background or a frame's edges drawn anew by Wan2.1-VACE - the
plan of hub/enhance.py, the engine on PyTorch run in its folder in a Windows job. A real hub, real ffmpeg, the
stand-ins of uv, of the engines' scripts (tests/fake_torch_engine.py) and of RIFE (tests/fake_enhance.py) - real
processes."""

import hashlib
import json
import os
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest

from common.paths import models_home
from hub import enhance, ffmpeg, torch_engines
from tests.test_enhance import FAKE as FAKE_PROGRAMS
from tests.test_enhance import FFMPEG, a_video_job, described, frames_of, make, red, two_shots, wait_job
from tests.test_torch_engines import REPLACE, UV_ZIP, FakeFiles, stand, wait_state  # noqa: F401 - the engines' stand
from tests.test_visual import hub  # noqa: F401 - the Studio's stand


def test_what_vace_draws_is_planned_in_windows_that_keep_the_seam():
    """58.3: the windows of 81 moments, each after the first beginning with the last 9 of the one before; «Быстрее»
    on 24 a second - a moment each 1.5 frames, the last at the last frame; other rates - each frame; VACE's size of
    a video - its own when 480p with sides of 16; the new frame of a format - the source at its size in the middle."""
    assert enhance.windows_of(200) == [(0, 81, 0), (72, 153, 9), (144, 200, 9)]
    assert enhance.windows_of(81) == [(0, 81, 0)] and enhance.windows_of(5) == [(0, 5, 0)]
    assert enhance.stride(24, True) == Fraction(3, 2) and enhance.stride(32, True) == 2
    assert enhance.stride(24, False) == 1 and enhance.stride(16, True) == 1 and enhance.stride(25, True) == 1
    times = enhance.moments_of(121, Fraction(3, 2))
    assert len(times) == 81 and times[1] == Fraction(3, 2) and times[-1] == 120  # 58.1's window: 5 s in 81
    assert enhance.moments_of(122, Fraction(3, 2))[-1] == 121  # past the last frame: the last frame
    assert enhance.moments_of(3, Fraction(1)) == [0, 1, 2]
    assert enhance.vace_size(832, 480) == (832, 480) and enhance.vace_size(768, 512) == (768, 512)
    assert enhance.vace_size(1280, 720) == (848, 480) and enhance.vace_size(64, 64) == (64, 64)
    assert enhance.canvas_of(832, 480, "9:16") == (832, 1442, 0, 481)
    assert enhance.canvas_of(480, 832, "16:9") == (1442, 832, 481, 0)
    with pytest.raises(enhance.EnhanceError, match="уже в формате"):
        enhance.canvas_of(832, 480, "16:9")


def _replace_script():
    """hub/torch_scripts/replace.py as a module: the engine's Python runs it, the hub never imports it; its
    PyTorch and diffusers are imported only where it draws."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("replace_script", torch_engines.SCRIPTS / "replace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_window_goes_on_from_what_the_window_before_drew():
    """58.6 found live: the film's second window drew another object from its first new frame - the first window's
    last 9 moments had been dropped before the second took them, and it drew them anew. Each window is given its
    first `keep` moments as the window before drew them (kept, mask black), the rest as they are with their areas;
    a window before that drew nothing - those given as they are; each moment saved once; 4k + 1 frames."""
    script = _replace_script()
    painted, saved = [], []

    def paint(videos, masks):
        painted.append((list(videos), list(masks)))
        return [f"w{len(painted)}:{v}" for v in videos]

    def run(windows):
        painted.clear()
        saved.clear()
        script.through(windows, given=lambda k: f"g{k}", area=lambda k: f"a{k}", black=lambda: "black",
                       paint=paint, kept=lambda k, drawn, mask: f"kept {drawn} {mask}",
                       save=lambda k, drawn: saved.append((k, drawn)))  # fmt: skip

    run(enhance.windows_of(163))  # the film of 58.6: 244 frames at «Быстрее» - 163 moments
    assert [len(videos) for videos, _masks in painted] == [81, 81, 21]  # the last: 19 moments and 2 more of its last
    second, masks = painted[1]
    assert second[:9] == [f"kept w1:g{k} a{k}" for k in range(72, 81)] and masks[:9] == ["black"] * 9
    assert second[9:] == [f"g{k}" for k in range(81, 153)] and masks[9:] == [f"a{k}" for k in range(81, 153)]
    third, masks = painted[2]
    assert third[:9] == [f"kept w2:g{k} a{k}" for k in range(144, 153)] and masks[:9] == ["black"] * 9
    assert third[19:] == ["g162", "g162"] and masks[19:] == ["a162", "a162"]
    assert [k for k, _drawn in saved] == list(range(163))
    assert saved[80] == (80, "w1:g80") and saved[81] == (81, "w2:g81") and saved[153] == (153, "w3:g153")

    run([(0, 81, 0), (144, 163, 9)])  # the window between drew nothing: the object was not seen there
    _first, (third, masks) = painted
    assert third[:9] == [f"g{k}" for k in range(144, 153)] and masks[:9] == [f"a{k}" for k in range(144, 153)]
    assert [k for k, _drawn in saved] == [*range(81), *range(144, 163)]


def test_the_replace_engine_is_put_in_beside_another_and_takes_what_afo_has(stand, tmp_path, monkeypatch):  # noqa: F811
    """58.2: «Замена и дорисовка» - no code archive: its libraries by their pins in uv's second call; its weights in
    folders, as the repository has them; umT5's GGUF that AFO's Wan2.2 keeps - linked, not fetched, not counted. Two
    engines put in at once: uv's Python once, both ready. One taken off while the other is put in: the shared folder
    stays for that one."""
    hub = stand  # noqa: F811
    base, models, spec = torch_engines.home(), models_home(), torch_engines.ENGINES["replace"]
    gguf = spec["weights"][-1]
    near = models / "pictures" / "_shared" / "city96" / "umt5-xxl-encoder-gguf" / gguf["path"]
    near.parent.mkdir(parents=True)
    near.write_bytes(REPLACE[gguf["path"]])
    fetched = sum(len(REPLACE[w["path"]]) for w in spec["weights"][:-1])
    shared = len(UV_ZIP) + torch_engines.PYTHON_SIZE + sum(t["size"] for t in torch_engines.TORCH)
    told = next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "replace")
    assert told["state"] == "missing" and told["name"] == "Замена и дорисовка"
    assert told["size"] == shared + torch_engines.LIBRARIES_SIZE + fetched  # the GGUF here: not counted

    monkeypatch.setenv("FAKE_UV_SLOW", "0.5")  # each pip install waits: the two installs overlap
    monkeypatch.setenv("FAKE_UV_CHILD", str(tmp_path / "uv-child.pid"))
    assert hub.post("/v1/enhance/parts/select").status_code == 200
    assert hub.post("/v1/enhance/parts/replace").status_code == 200
    assert wait_state(hub, "ready", "missing")["state"] == "ready"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        replace = next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "replace")
        if replace["state"] != "loading":
            break
        time.sleep(0.05)
    assert replace["state"] == "ready", replace["error"]
    calls = hub.uv()
    assert [c["args"][:2] for c in calls].count(["python", "install"]) == 1  # the shared: made once
    assert sorted(c["args"][1] for c in calls if c["args"][0] == "venv") == [str(base / "replace" / "env"),
                                                                              str(base / "select" / "env")]  # fmt: skip
    own = next(c for c in calls if "diffusers==0.41.0" in c["args"])
    assert not [a for a in own["args"] if " @ " in a] and "--no-build-isolation" in own["args"]
    pins = (base / "replace" / "pins.txt").read_text(encoding="utf-8").split()
    assert "numpy==2.2.6" in pins and "huggingface-hub==1.33.0" in pins
    config = torch_engines.vace_dir(models) / "transformer" / "config.json"
    assert config.read_bytes() == REPLACE["transformer/config.json"]
    assert (torch_engines.vace_dir(models) / "vae" / "config.json").read_bytes() == REPLACE["vae/config.json"]
    encoder = torch_engines.encoder_file(models)
    assert os.path.samefile(encoder, near)  # linked, not a copy
    assert not [p for p in FakeFiles.asked if p.endswith(".gguf")]
    assert torch_engines.fingerprint("replace")["code"] is None
    assert "packages" not in torch_engines.fingerprint("select")  # SAM put in by 56 is not taken for missing

    monkeypatch.setenv("FAKE_UV_SLOW", "3")
    hub.delete("/v1/enhance/parts/select")
    assert hub.post("/v1/enhance/parts/select").status_code == 200
    time.sleep(0.5)
    assert hub.delete("/v1/enhance/parts/replace").status_code == 200  # while select is put in
    assert base.exists() and not torch_engines.vace_dir(models).exists() and near.is_file()
    assert wait_state(hub, "ready", "missing")["state"] == "ready"
    hub.delete("/v1/enhance/parts/select")
    assert not base.exists()


@pytest.fixture
def drawn(stand, monkeypatch, tmp_path):  # noqa: F811
    """The stand with both engines put in, and RIFE («Плавность») on the disk - the stand-in of tests/fake_enhance.py;
    `hub.plans()` - what replace.py was given, `hub.rife()` - what RIFE was asked."""
    hub = stand  # noqa: F811
    monkeypatch.setattr(ffmpeg, "find", lambda _tools: Path(FFMPEG))
    for part in ("select", "replace"):
        hub.post(f"/v1/enhance/parts/{part}")
        deadline = time.monotonic() + 60
        while next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == part)["state"] != "ready":
            assert time.monotonic() < deadline, part
            time.sleep(0.05)
    smooth = enhance.folder(hub.app.state.enhance.tools, "smooth")
    (smooth / enhance.RIFE_MODEL).mkdir(parents=True)
    (smooth / "rife-ncnn-vulkan.exe").write_bytes(b"MZ rife")
    monkeypatch.setattr(enhance, "command", lambda program: [sys.executable, str(FAKE_PROGRAMS), program.name])
    monkeypatch.setenv("FAKE_ENHANCE_LOG", str(tmp_path / "programs.jsonl"))

    def plans():
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path).glob("plan-*.json"),
                                                                           key=lambda p: int(p.stem[5:]))]  # fmt: skip

    def rife():
        log = tmp_path / "programs.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
        return [int(a["args"][a["args"].index("-n") + 1]) for a in map(json.loads, lines)]

    hub.plans, hub.rife = plans, rife
    return hub


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_an_object_is_drawn_anew_shot_by_shot_and_faster_at_16_a_second(drawn, tmp_path, monkeypatch):
    """58.3-58.4: «Заменить предмет» - a job of the Studio's queue: SAM 2.1's masks of the object clicked, then
    VACE shot by shot - none across the cut. «Быстрее»: 20 steps, the video of 24 a second drawn at 16 - RIFE's
    half-frames first, a moment between two frames with both their masks; the frames between drawn ones by RIFE
    back. How far goes to the window as VACE's steps go. The new video: the same length, size and sound. The words
    are wanted; not with «Только предмет»; the parts not here - refused at once."""
    hub = drawn  # noqa: F811
    source_id = a_video_job(hub)  # 48 frames, a cut, 24 of red - 24 a second, with sound
    draw = {"kind": "object", "words": "a red mug", "fast": True, "share": 0.5, "x": 0.25, "y": 0.75}
    for wrong, said in (({**draw, "words": " "}, "напишите"), ({**draw, "share": None}, "нет такого")):
        refused = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": wrong})
        assert refused.status_code == 422 and said in refused.json()["detail"], refused.text
    both = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": draw,
                                                "select": {"share": 0.5, "x": 0.5, "y": 0.5}})  # fmt: skip
    assert both.status_code == 422
    monkeypatch.setenv("FAKE_ENGINE_STEP_S", "0.05")
    asked = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": draw})
    assert asked.status_code == 200, asked.text
    job = asked.json()["job"]
    assert job["enhance"]["draw"] == draw and (job["width"], job["height"]) == (64, 64)
    done = wait_job(hub, job["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done
    masks = next(r for r in reversed(hub.engine()) if "--masks" in r["args"])
    assert masks["args"][masks["args"].index("--at") + 1] == "36"
    plan, composed = hub.plans()
    assert plan["mode"] == "object" and plan["prompt"] == "a red mug" and plan["steps"] == 20
    assert plan["size"] == [64, 64] and plan["guidance"] == 5.0
    assert plan["windows"] == [[0, 33, 0], [33, 50, 0]]  # 48 frames → 33 moments, 24 → 17: no window over the cut
    assert Path(plan["moments"][1]["frame"]).parent.name == "0"  # RIFE's half-frame of shot 0
    assert [Path(m).name for m in plan["moments"][1]["masks"]] == ["000002.png", "000003.png"]  # 1.5: both
    assert [Path(m).name for m in plan["moments"][2]["masks"]] == ["000004.png"]  # 3: its own
    assert Path(plan["model"]) == torch_engines.vace_dir(models_home())
    assert hub.rife() == [96, 48, 99, 51]  # halves of each shot; back: 33 × 3, 17 × 3
    assert len(composed["frames"]) == 72 and composed["mode"] == "object"
    assert Path(composed["frames"][5]["drawn"]).name == "000011.png"  # frame 5 is RIFE's 5 × 2 back
    assert Path(composed["frames"][50]["drawn"]).parent.name == "1"  # the red shot: its own
    shown = described(hub.app.state.visual.media / done["file"])
    assert shown["size"] == (64, 64) and shown["fps"] == 24 and shown["sound"] and abs(shown["seconds"] - 3) < 0.1
    frames = frames_of(hub.app.state.visual.media / done["file"], tmp_path / "seen")
    assert len(frames) == 72 and not red(frames[47]) and red(frames[48])  # the cut where it was
    steps = [p["step"] for kind, p in hub.seen if kind == "visual.progress" and p["id"] == job["id"]]
    assert steps[0] == 5 and steps[-1] == 100 and any(5 < s < 90 for s in steps)
    assert not [p for p in hub.app.state.visual.media.rglob("*") if p.name.startswith(".")]

    careful = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": {**draw, "fast": False,
                                                                                   "kind": "background"}})  # fmt: skip
    done = wait_job(hub, careful.json()["job"]["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done
    plan = hub.plans()[-2]
    assert plan["mode"] == "background" and plan["steps"] == 25 and plan["windows"] == [[0, 48, 0], [48, 72, 0]]
    assert hub.rife() == [96, 48, 99, 51]  # every frame drawn: RIFE not asked


def test_the_engines_put_in_before_stay_put_in_beside_the_depth():
    """59.2: «Глубина кадра» is a part of its own, on the libraries of «Замена и дорисовка»: the engines put in
    before keep their fingerprints (taken on the code of 57.5) - not taken for missing, their gigabytes not fetched
    again (DirectorIssues Р26)."""
    before = {"select": "99c0259aaa8d9b2f80167ee253410f278aaf5bd49090ab059360148c0e25a350",
              "replace": "c54edba107e9efe9abe1e57fd381a49230b71d683f1d3b90d129cf8610d2a3be",
              "lips": "7f32cb5f5df5b1ef8e24ddde9d9dee865d6b0673ba0bda3ce6ee60357b55866b"}  # fmt: skip
    for engine, sha in before.items():
        assert hashlib.sha256(json.dumps(torch_engines.fingerprint(engine), sort_keys=True).encode()).hexdigest() == sha
    depth = torch_engines.ENGINES["depth"]
    assert depth["pins"] == torch_engines.ENGINES["replace"]["pins"] and depth["licence"] == "Apache-2.0"
    assert sum(w["size"] for w in depth["weights"]) < 100 << 20


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_the_whole_video_is_drawn_anew_by_its_depth(drawn, tmp_path, monkeypatch):
    """59.2: «Перерисовать всё» - nothing clicked, SAM not asked: «Глубина кадра» gives each moment's depth (at
    «Быстрее» on 24 a second - RIFE's half-frames' too), VACE draws all of each frame by it, shot by shot, none
    across the cut; the new video is what it drew - the same length, size and sound. The words are wanted; the depth
    part not here - refused at once, named."""
    hub = drawn  # noqa: F811
    source_id = a_video_job(hub)  # 48 frames, a cut, 24 of red - 24 a second, with sound
    draw = {"kind": "motion", "words": "a copper kettle in a cabin by a candle", "fast": True}
    refused = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": {**draw, "words": " "}})
    assert refused.status_code == 422 and "каким сделать ролик" in refused.json()["detail"]
    missing = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": draw})
    assert missing.status_code == 422 and "«Глубина кадра» не установлено" in missing.json()["detail"]
    assert hub.post("/v1/enhance/parts/depth").status_code == 200
    deadline = time.monotonic() + 60
    while next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "depth")["state"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    asked = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": draw})
    assert asked.status_code == 200, asked.text
    job = asked.json()["job"]
    assert job["enhance"]["draw"] == draw and (job["width"], job["height"]) == (64, 64)
    done = wait_job(hub, job["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done
    assert not [r for r in hub.engine() if "--masks" in r["args"]]
    (depth,) = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("depth-*.json")]
    assert Path(depth["model"]) == torch_engines.depth_dir(models_home())
    assert len(depth["frames"]) == 50 and Path(depth["frames"][1]).parent.name == "0"  # 33 + 17; a half-frame
    plan, composed = hub.plans()
    assert plan["mode"] == "motion" and plan["prompt"] == draw["words"] and plan["steps"] == 20
    assert plan["windows"] == [[0, 33, 0], [33, 50, 0]] and all(m["masks"] == [] for m in plan["moments"])
    assert [Path(m["frame"]).parent.name for m in plan["moments"]] == ["depth"] * 50
    assert Path(plan["moments"][49]["frame"]).name == "000050.png"
    assert composed["mode"] == "motion" and len(composed["frames"]) == 72
    assert composed["frames"][0]["mask"] is None and Path(composed["frames"][50]["drawn"]).parent.name == "1"
    shown = described(hub.app.state.visual.media / done["file"])
    assert shown["size"] == (64, 64) and shown["fps"] == 24 and shown["sound"] and abs(shown["seconds"] - 3) < 0.1
    assert not [p for p in hub.app.state.visual.media.rglob("*") if p.name.startswith(".")]


def a_wide_job(hub, frames, *, width=96, height=64, cut=False):  # noqa: F811
    """(59) A video the Studio filmed: one shot of `frames` frames, 24 a second, a tone all along - as it lies in
    data/media, its row in the queue's table; `cut` - its last 24 frames red, a shot of their own."""
    import subprocess
    import uuid

    visual, job_id = hub.app.state.visual, uuid.uuid4().hex[:12]
    (visual.media / "2026-11").mkdir(parents=True, exist_ok=True)
    picture = f"testsrc=size={width}x{height}:rate=24:duration={(frames - (24 if cut else 0)) / 24:.4f}"
    shots = ["-f", "lavfi", "-i", picture] + (["-f", "lavfi", "-i", f"color=c=red:size={width}x{height}:rate=24:"
                                                "duration=1"] if cut else [])  # fmt: skip
    joined = ["-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a"] if cut else []
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *shots, "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=48000:duration={frames / 24:.4f}", *joined, "-frames:v",
                    str(frames), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-g", "1", "-c:a", "aac", str(visual.media / f"2026-11/{job_id}.mp4")],
                   check=True, timeout=60)  # fmt: skip
    (visual.media / f"2026-11/{job_id}.webm").write_bytes(b"\x1a\x45\xdf\xa3 the engine's clip")
    (visual.media / f"2026-11/{job_id}.jpg").write_bytes(b"\xff\xd8\xff poster")
    hub.app.state.db.call(lambda conn: conn.execute(
        "INSERT INTO visual_jobs (id, kind, prompt, model_id, width, height, seed, status, file, fps, sound, frames) "
        "VALUES (?, 'video', 'a man talks in a kitchen', 'ltx', ?, ?, 1, 'done', ?, 24, 1, ?)",
        (job_id, width, height, f"2026-11/{job_id}.webm", frames)))  # fmt: skip
    return job_id


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_a_video_is_filmed_anew_by_another_cameras_move(drawn, tmp_path, monkeypatch):
    """59.4: «Камера» - ReCamMaster is given 81 frames, the video's moments k × count / 81: of a longer video the
    nearest frames, RIFE makes its own count back; of a shorter one RIFE's 81 first, the nearest ones back. The new
    video is 832 × 480, its length and sound its own; its words, unless said, the video's. The camera part and
    «Замена и дорисовка» wanted (its umT5 and VAE), «Плавность» when the video is not of 81 frames; a tall video,
    one with cuts, a film, with «Предмет» - refused at once. «Стоп» while it films - the engine killed, nothing left."""
    hub = drawn  # noqa: F811
    long_id, short_id = a_wide_job(hub, 100), a_wide_job(hub, 60)
    tall_id, cut_id = a_wide_job(hub, 48, width=64, height=96), a_wide_job(hub, 72, cut=True)
    camera = {"move": "pan_right"}
    missing = hub.post("/v1/visual/improve", json={"from_job": long_id, "camera": camera})
    assert missing.status_code == 422 and "«Камера» не установлено" in missing.json()["detail"], missing.text
    assert hub.post("/v1/enhance/parts/camera").status_code == 200
    deadline = time.monotonic() + 60
    while next(p for p in hub.get("/v1/enhance").json()["parts"] if p["id"] == "camera")["state"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    files = torch_engines.camera_files(models_home())
    assert files["wan_video_dit.py"].parent.name == "code" and files["step20000.ckpt"].is_file()
    for spec, said in (({"from_job": tall_id, "camera": camera}, "для горизонтального ролика"),
                       ({"from_job": cut_id, "camera": camera}, "есть склейки"),
                       ({"film": "f1", "camera": camera}, "для ролика «Студии»"),
                       ({"from_job": long_id, "camera": camera, "select": {"share": 0.5, "x": 0.5, "y": 0.5}},
                        "отдельно"),
                       ({"from_job": long_id, "camera": {"move": "spin"}}, "")):  # fmt: skip
        refused = hub.post("/v1/visual/improve", json=spec)
        assert refused.status_code == 422 and said in str(refused.json()["detail"]), refused.text

    asked = hub.post("/v1/visual/improve", json={"from_job": long_id, "camera": camera})
    assert asked.status_code == 200, asked.text
    job = asked.json()["job"]
    assert job["enhance"]["camera"] == {"move": "pan_right", "words": "a man talks in a kitchen"}
    assert (job["width"], job["height"]) == (832, 480)
    done = wait_job(hub, job["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done
    (plan,) = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("camera-*.json")]
    assert plan["move"] == 1 and plan["steps"] == 25 and plan["prompt"] == "a man talks in a kitchen"
    assert [int(Path(f).stem) - 1 for f in plan["frames"]] == enhance.camera_moments(100)
    assert enhance.camera_moments(100)[:4] == [0, 1, 2, 4] and enhance.camera_moments(100)[-1] == 99
    assert Path(plan["ckpt"]) == files["step20000.ckpt"] and Path(plan["dit"]) == files["wan_video_dit.py"]
    assert Path(plan["vace"]) == torch_engines.vace_dir(models_home())
    assert hub.rife() == [100]  # its own count back
    shown = described(hub.app.state.visual.media / done["file"])
    assert shown["size"] == (832, 480) and shown["fps"] == 24 and shown["sound"]
    assert abs(shown["seconds"] - 100 / 24) < 0.1
    assert not [p for p in hub.app.state.visual.media.rglob("*") if p.name.startswith(".")]

    arc = {"move": "arc_left", "words": "a kitchen"}
    shorter = hub.post("/v1/visual/improve", json={"from_job": short_id, "camera": arc})
    done = wait_job(hub, shorter.json()["job"]["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done
    plan = json.loads(sorted(tmp_path.glob("camera-*.json"))[-1].read_text(encoding="utf-8"))
    assert plan["move"] == 9 and plan["prompt"] == "a kitchen" and Path(plan["frames"][0]).parent.name == "given"
    assert hub.rife() == [100, 81]  # RIFE's 81 first; back - the nearest ones
    shown = described(hub.app.state.visual.media / done["file"])
    assert shown["size"] == (832, 480) and abs(shown["seconds"] - 60 / 24) < 0.1

    monkeypatch.setenv("FAKE_ENGINE_STEP_S", "5")
    source = hub.app.state.visual.media / f"2026-11/{long_id}.mp4"
    began = time.monotonic()
    with pytest.raises(enhance.EnhanceError, match="остановлено"):
        make(hub, source, tmp_path / "stopped.mp4", fps=24, stop_after=2.0, camera=camera)
    assert time.monotonic() - began < 20
    assert not (tmp_path / "stopped.mp4").exists() and not list(tmp_path.glob(".*.work"))


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_the_edges_are_drawn_to_another_format_and_a_stop_leaves_nothing(drawn, tmp_path, monkeypatch):
    """58.3-58.4: «Формат 9:16» - no object to find, the source in the middle of a taller frame, the edges drawn
    around it; its words, unless said, the video's own; the job is of the new size. A format the video has - refused.
    ✕ while VACE draws - the engine killed, nothing left."""
    hub = drawn  # noqa: F811
    source_id = a_video_job(hub)
    square = {"kind": "edges", "canvas": "1:1"}
    same = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": square})
    assert same.status_code == 422 and "уже в формате" in same.json()["detail"]
    tall = {"kind": "edges", "canvas": "9:16", "fast": False}
    asked = hub.post("/v1/visual/improve", json={"from_job": source_id, "draw": tall})
    job = asked.json()["job"]
    assert (job["width"], job["height"]) == (64, 110) and job["enhance"]["draw"]["words"] == "a lamp"
    done = wait_job(hub, job["id"], "done", "failed", "cancelled", timeout=60)
    assert done["status"] == "done", done["error"]
    assert not [r for r in hub.engine() if "--masks" in r["args"]]
    plan, composed = hub.plans()
    assert plan["size"] == [480, 832] and plan["place"] == [0, 172, 480, 480]  # 64 × 110 at 7.5 times
    assert composed["canvas"] == [64, 110, 0, 23]
    assert described(hub.app.state.visual.media / done["file"])["size"] == (64, 110)

    monkeypatch.setenv("FAKE_ENGINE_STEP_S", "5")
    source = tmp_path / "two.mp4"
    two_shots(source)
    began = time.monotonic()
    with pytest.raises(enhance.EnhanceError, match="остановлено"):
        make(hub, source, tmp_path / "stopped.mp4", fps=24, stop_after=2.0,
             draw={"kind": "edges", "canvas": "9:16", "fast": False, "words": "a lamp"})  # fmt: skip
    assert time.monotonic() - began < 20
    assert not (tmp_path / "stopped.mp4").exists() and not list(tmp_path.glob(".*.work"))
