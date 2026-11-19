"""«Улучшить ролик» (stage 54): its programs fetched as parts, and run. A real hub, stand-ins of GitHub and Hugging
Face, and a stand-in of the programs - a real process that writes real files (tests/fake_enhance.py)."""

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

from common.paths import engines_home, models_home
from hub import engines, enhance, hf
from tests.test_visual import hub  # noqa: F401 - the Studio's stand

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
    assert [(p["id"], p["state"], p["size"]) for p in was["parts"]] == [
        ("smooth", "missing", sizes["windows.zip"]),
        ("sharp", "missing", sizes["realesrgan-ncnn-vulkan-20220424-windows.zip"]),
        ("detail", "missing", sizes[SD_ARCHIVE] + sizes[engines.SD_RUNTIME] + weights)]  # fmt: skip
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
