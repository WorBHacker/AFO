"""Local models from Hugging Face into llama-swap (stage 10.1, D37): a repository's files as variants, each judged
against the computer's memory, the download that goes on after a broken connection and counts only with the right
sha256, the model's block in the config, the trial run that tries smaller settings, and removal. A stand-in
Hugging Face serves a tiny GGUF model; the stand-in llama-swap of the 8.5 tests reads the same config."""

import hashlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import hf
from hub import llama_config as lc
from hub import model_install as mi
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_llama_config import CONFIG, FakeLlamaSwap

GIB = 1024**3


def tiny_gguf(size: int, *, layers: int = 8, context: int = 16384) -> bytes:
    """A GGUF file of `size` bytes: a real header (the architecture's keys, then a tokenizer list), then filler."""

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data

    keys = {"general.architecture": "tiny", "general.name": "Tiny", "tiny.block_count": layers,
            "tiny.context_length": context, "tiny.embedding_length": 256, "tiny.attention.head_count": 4,
            "tiny.attention.head_count_kv": 2}  # fmt: skip
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(keys) + 1)
    for key, value in keys.items():
        out += string(key) + (struct.pack("<I", 8) + string(value) if isinstance(value, str)
                              else struct.pack("<II", 4, value))  # fmt: skip
    out += string("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + b"".join(string(t) for t in "abc")
    return out + bytes(range(256)) * ((size - len(out)) // 256) + b"\0" * ((size - len(out)) % 256)


FILES = {
    "Tiny-Q4_K_M.gguf": tiny_gguf(180_000),
    "Tiny-Q8_0.gguf": tiny_gguf(340_000),
    "BF16/Tiny-BF16-00001-of-00002.gguf": tiny_gguf(320_000),
    "BF16/Tiny-BF16-00002-of-00002.gguf": b"\1" * 320_000,
    "mmproj-Tiny-Q8_0.gguf": tiny_gguf(50_000),
    "mtp-Tiny-Q8_0.gguf": tiny_gguf(20_000),  # a speculative draft: not a variant
    "README.md": b"# Tiny\n",
}
PARAMS = 320_000  # bits a weight: Q4_K_M 4.5, Q8_0 8.5, BF16 16


class FakeHub(BaseHTTPRequestHandler):
    """Hugging Face's API and file downloads as LocalChat uses them: search, a repository's info and tree, files by
    Range (redirected to a 'CDN' on another host name), a gated repository that wants a token."""

    port = 0
    break_after: ClassVar[dict] = {}  # file -> bytes sent before the connection breaks (once)
    corrupt: ClassVar[set] = set()  # files served with a wrong byte (their sha256 will not match)
    slow = 0.0  # seconds a chunk
    cdn_auth: ClassVar[list] = []  # the Authorization header each CDN request came with
    ranges: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        repo = "test/Tiny-GGUF"
        if path == "/api/models":
            query = parse_qs(url.query)
            assert query["filter"] == ["gguf"]
            self._json([{"id": repo, "downloads": 10, "likes": 2, "tags": ["license:mit", "gguf"],
                         "lastModified": "2026-10-01T00:00:00.000Z"},
                        {"id": "someone/Tiny-abliterated-GGUF", "downloads": 5, "tags": []}])  # fmt: skip
        elif path == "/api/whoami-v2":
            ok = self.headers.get("Authorization") == "Bearer hf_good"
            self._json({"name": "tester"} if ok else {"error": "Invalid credentials"}, 200 if ok else 401)
        elif path in (f"/api/models/{repo}", "/api/models/test/Gated-GGUF"):
            if path.endswith("Gated-GGUF") and self.headers.get("Authorization") != "Bearer hf_good":
                self._json({"error": "gated"}, 401)
                return
            self._json({"id": path.removeprefix("/api/models/"), "sha": "abc123", "gated": False, "downloads": 10,
                        "cardData": {"license": "mit"}, "tags": ["license:mit"], "lastModified": "2026-10-01",
                        "gguf": {"total": PARAMS, "architecture": "tiny", "context_length": 16384}})  # fmt: skip
        elif path in (f"/api/models/{repo}/tree/main", "/api/models/test/Gated-GGUF/tree/main"):
            self._json([{"type": "file", "path": name, "size": len(data), "oid": "x",
                         **({"lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}}
                            if name.endswith(".gguf") else {})} for name, data in FILES.items()])  # fmt: skip
        elif path.startswith(f"/{repo}/resolve/"):
            name = path.split("/resolve/", 1)[1].split("/", 1)[1]
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.port}/cdn/{name}")
            self.send_header("content-length", "0")
            self.end_headers()
        elif path.startswith("/cdn/"):
            self._file(path.removeprefix("/cdn/"))
        else:
            self._json({"error": "not found"}, 404)

    def _file(self, name):
        FakeHub.cdn_auth.append(self.headers.get("Authorization"))
        data = FILES[name]
        if name in self.corrupt:
            data = data[:-1] + b"X"
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            FakeHub.ranges.append((name, start))
            self.send_response(206)
            self.send_header("content-range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        cut = self.break_after.pop(name, None)
        for i in range(0, len(body), 16384):
            if cut is not None and i >= cut:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)  # the connection breaks in the middle of the file
                return
            if self.slow:
                time.sleep(self.slow)
            try:
                self.wfile.write(body[i : i + 16384])
            except OSError:  # the reader had enough (a header read stops early)
                return


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a connection the test broke on purpose


@pytest.fixture
def fake_hub(monkeypatch):
    FakeHub.break_after, FakeHub.corrupt, FakeHub.slow, FakeHub.cdn_auth, FakeHub.ranges = {}, set(), 0.0, [], []
    server = QuietServer(("127.0.0.1", 0), FakeHub)
    FakeHub.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(hf.BASE_ENV, f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(hf, "CHUNK", 32768)
    yield server
    server.shutdown()


SPACE = {
    "gpu": {"name": "RTX", "vram_total_mib": 16384, "vram_used_mib": 1024},
    "memory": {"ram_total_gib": 64.0, "ram_used_gib": 16.0},
    "local": {"reachable": True, "running": []},
}


def test_a_repositorys_files_as_variants_and_their_names():
    files = [{"path": n, "size": len(d), "sha256": "s"} for n, d in FILES.items()]
    files += [{"path": "Tiny-Q5_K_M-00001-of-00002.gguf", "size": 1}]  # its second part is missing: not offered
    found, projectors = hf.variants(files)
    assert [(v["quant"], len(v["files"])) for v in found] == [("Q4_K_M", 1), ("Q8_0", 1), ("BF16", 2)]
    assert [p["file"] for p in projectors] == ["mmproj-Tiny-Q8_0.gguf"]
    split_and_whole = [{"path": "m-q4_k_m.gguf", "size": 10}, {"path": "m-q4_k_m-00001-of-00002.gguf", "size": 6},
                       {"path": "m-q4_k_m-00002-of-00002.gguf", "size": 4}]  # fmt: skip
    assert [v["files"][0]["path"] for v in hf.variants(split_and_whole)[0]] == ["m-q4_k_m.gguf"]  # the one file
    names = {"Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf": "UD-Q4_K_XL", "gpt-oss-20b-MXFP4.gguf": "MXFP4",
             "x-IQ3_XXS.gguf": "IQ3_XXS", "qwen2.5-coder-7b-instruct-q8_0-00001-of-00003.gguf": "Q8_0",
             "Model-f16.gguf": "F16", "readme.gguf": ""}  # fmt: skip
    assert {name: hf.quant_of(name) for name in names} == names
    assert hf.summary({"id": "a/Qwen-27B-abliterated-GGUF", "tags": []})["unrestricted"]
    assert not hf.summary({"id": "ggml-org/gemma-4-E4B-it-GGUF", "tags": ["license:apache-2.0"]})["unrestricted"]


def test_this_computer_against_a_model():
    space = mi.room(SPACE)
    assert (space["vram_gib"], space["ram_gib"]) == (14.7, 44.0)  # free video memory; RAM over Windows' share
    swapped = mi.room({**SPACE, "local": {"running": [{"model": "big"}]}})
    assert swapped["vram_gib"] == 14.4 and swapped["loaded"] == ["big"]  # the loaded one would unload
    info = {"layers": 48, "kv_layers": 48, "kv_heads": 8, "key_length": 128, "value_length": 128,
            "context_trained": 131072}  # fmt: skip
    assert mi.verdict(8 * GIB, info, space) == "gpu"
    assert mi.verdict(20 * GIB, info, space) == "split" and mi.verdict(20 * GIB, info, space, moe=True) == "split_moe"
    assert mi.verdict(80 * GIB, info, space) == "no"
    assert mi.verdict(8 * GIB, info, mi.room({"memory": SPACE["memory"]})) == "cpu"  # no graphics card
    variants = [{"quant": "Q2_K", "size": 6 * GIB, "bits": 2.9}, {"quant": "Q4_K_M", "size": 9 * GIB, "bits": 4.8},
                {"quant": "Q6_K", "size": 12 * GIB, "bits": 6.6}, {"quant": "Q8_0", "size": 15 * GIB, "bits": 8.5},
                {"quant": "BF16", "size": 28 * GIB, "bits": 16.0}]  # fmt: skip
    judged = {v["quant"]: mi.verdict(v["size"], info, space) for v in variants}
    assert mi.recommend(variants, judged) == "Q6_K"  # the best that fits the video memory, not the 2-bit one
    small = mi.room({**SPACE, "gpu": {"vram_total_mib": 4096, "vram_used_mib": 0}})
    judged = {v["quant"]: mi.verdict(v["size"], info, small) for v in variants}
    assert mi.recommend(variants, judged) == "Q4_K_M"  # nothing fits the 4 GB: the smallest decent one, in part
    experts = [{"quant": "UD-Q3_K_XL", "size": 12 * GIB, "bits": 3.6}, {"quant": "Q4_K_S", "size": 15 * GIB,
               "bits": 4.6}, {"quant": "Q4_K_M", "size": 16 * GIB, "bits": 4.9}]  # fmt: skip
    dense = {v["quant"]: mi.verdict(v["size"], info, space) for v in experts}
    moe = {v["quant"]: mi.verdict(v["size"], info, space, moe=True) for v in experts}
    assert mi.recommend(experts, dense) == "UD-Q3_K_XL"  # a dense model: all of it in the video memory
    assert mi.recommend(experts, moe, moe=True) == "Q4_K_S"  # experts in RAM cost little: the better 4-bit one

    plan = mi.launch_plan(info, 9 * GIB, space)
    assert (plan["context"], plan["gpu_layers"], plan["cache_type"]) == (32768, "all", "q8_0")
    tight = mi.launch_plan(info, 13 * GIB, space, mmproj=2 * GIB)
    assert (tight["gpu_layers"], tight["mmproj_on_gpu"]) == ("all", False)  # the projector to RAM lets it fit
    big = mi.launch_plan(info, 20 * GIB, space)
    assert (big["gpu_layers"], big["context"], big["ttl"]) == ("auto", 8192, mi.SPLIT_TTL_S)
    steps, step = [], plan
    while step := mi.smaller(step):
        steps.append((step["context"], step["gpu_layers"]))
    assert steps == [(16384, "all"), (8192, "all"), (4096, "all"), (4096, "auto")]


def test_a_new_models_block_in_the_config_and_out_again():
    assert lc.server_command(CONFIG) == "${llama_server}"
    plain = 'models:\n  "a":\n    cmd: |\n      "D:/llama/llama-server.exe" -m "x.gguf"\n      --port ${PORT}\n'
    assert lc.server_command(plain) == '"D:/llama/llama-server.exe"'
    with pytest.raises(lc.ConfigError, match="нет llama-server"):
        lc.server_command('models:\n  "s":\n    cmd: |\n      strata serve\n')
    assert lc.free_id(CONFIG, "Big") == "big-2" and lc.free_id(CONFIG, "Tiny-GGUF Q8") == "tiny-gguf-q8"
    block = lc.model_block("tiny-q8_0", name='Tiny "Q8"', description="test", server="${llama_server}",
                           model="F:\\models\\tiny.gguf", mmproj="F:/models/mmproj.gguf", mmproj_on_gpu=False,
                           context=16384, gpu_layers="all",
                           metadata={"source": "huggingface.co/test/Tiny"})  # fmt: skip
    new = lc.add_model(CONFIG, "tiny-q8_0", block)
    assert new.startswith(CONFIG.rstrip("\n"))  # everything before as it was
    view = lc.model_view(new, "tiny-q8_0")
    assert (view["name"], view["file"], view["mmproj"], view["mmproj_on_gpu"], view["context"], view["gpu_layers"],
            view["cache_type"]) == ("Tiny 'Q8'", "F:/models/tiny.gguf", "F:/models/mmproj.gguf", False, 16384, "all",
                                    "q8_0")  # fmt: skip
    assert lc.edit(new, "tiny-q8_0", {"gpu_layers": "auto"})  # the window's launch settings (8.5) work on it
    assert lc.remove_model(new, "tiny-q8_0") == CONFIG
    with pytest.raises(lc.ConfigError, match="уже есть"):
        lc.add_model(new, "tiny-q8_0", block)
    grouped = new + 'groups:\n  "pair":\n    members: ["big", "tiny-q8_0"]\n'
    with pytest.raises(lc.ConfigError, match="группу"):
        lc.remove_model(grouped, "tiny-q8_0")
    followed = CONFIG + "\nhooks:\n  on_startup: {}\n"  # a section after models: the block goes before it
    assert lc.model_blocks(lc.add_model(followed, "tiny-q8_0", block))["tiny-q8_0"]
    assert lc.add_model(followed, "tiny-q8_0", block).endswith("hooks:\n  on_startup: {}\n")


def test_a_download_goes_on_after_a_break_and_counts_only_with_its_sha256(fake_hub, tmp_path):
    name = "Tiny-Q8_0.gguf"
    data = FILES[name]
    url = hf.file_url("test/Tiny-GGUF", name)
    dest = tmp_path / name
    FakeHub.break_after[name] = 100_000
    seen, cancel, progress = [], threading.Event(), hf.Progress()
    with pytest.raises(hf.HubError, match="продолжится"):
        hf.download(url, dest, size=len(data), sha256=hashlib.sha256(data).hexdigest(), token="hf_secret",
                    report=seen.append, cancel=cancel, progress=progress)  # fmt: skip
    part = tmp_path / (name + ".part")
    assert not dest.exists() and 0 < part.stat().st_size < len(data)
    hf.download(url, dest, size=len(data), sha256=hashlib.sha256(data).hexdigest(), token="hf_secret",
                report=seen.append, cancel=cancel, progress=progress)  # fmt: skip
    assert dest.read_bytes() == data and not part.exists() and [n for n, _ in FakeHub.ranges] == [name]
    assert FakeHub.ranges[0][1] >= 98_304 and seen[-1] == len(data)  # asked from where the part ended
    assert FakeHub.cdn_auth and set(FakeHub.cdn_auth) == {None}  # the token stays with huggingface.co

    again = tmp_path / "again" / name  # a part from an earlier hub: read again for its sha256, then the rest
    again.parent.mkdir()
    (again.parent / (name + ".part")).write_bytes(data[:50_000])
    hf.download(url, again, size=len(data), sha256=hashlib.sha256(data).hexdigest(), token=None,
                report=lambda n: None, cancel=cancel)  # fmt: skip
    assert again.read_bytes() == data and FakeHub.ranges[-1] == (name, 50_000)

    FakeHub.corrupt.add(name)
    broken = tmp_path / "broken" / name
    broken.parent.mkdir()
    with pytest.raises(hf.HubError, match="повреждённым") as caught:
        hf.download(url, broken, size=len(data), sha256=hashlib.sha256(data).hexdigest(), token=None,
                    report=lambda n: None, cancel=cancel)  # fmt: skip
    assert caught.value.status == 409 and not broken.exists() and not (broken.parent / (name + ".part")).exists()

    cancel.set()
    with pytest.raises(hf.Cancelled):
        hf.download(url, tmp_path / "x.gguf", size=len(data), sha256=None, token=None, report=lambda n: None,
                    cancel=cancel)  # fmt: skip
    head = hf.header("test/Tiny-GGUF", "Tiny-Q4_K_M.gguf")
    assert (head["layers"], head["kv_heads"], head["context_trained"]) == (8, 2, 16384)


@pytest.fixture
def hub(tmp_path, monkeypatch, fake_hub):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    FakeLlamaSwap.config, FakeLlamaSwap.frozen, FakeLlamaSwap.fail, FakeLlamaSwap.asked = config, None, False, []
    swap = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlamaSwap)
    threading.Thread(target=swap.serve_forever, daemon=True).start()
    monkeypatch.setattr("hub.llama_config.RELOAD_S", 2.0)
    monkeypatch.setattr(mi, "RETRIES_S", (0.1, 0.1, 0.1))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": [{"id": "tiny", "repo": "test/Tiny-GGUF", "name": "Tiny",
                                               "purposes": ["chat", "vision"], "about": {"ru": "Крошка"}}]}),
                       encoding="utf-8")  # fmt: skip
    monkeypatch.setattr(mi, "CATALOG", catalog)
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url=f"http://127.0.0.1:{swap.server_port}",
                                 status_interval_s=0, process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.portal.call(app.state.gateway.sync_catalog)
        app.state.status.latest = SPACE
        app.state.installer.drives = lambda: [{"letter": "T", "root": str(tmp_path / "T"), "free_gib": 500.0,
                                               "total_gib": 900.0, "kind": "nvme", "system": False}]  # fmt: skip
        client.config, client.tmp = config, tmp_path
        yield client
    swap.shutdown()


def wait_for(hub, job_id, *statuses, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = next(j for j in hub.get("/v1/install/jobs").json()["jobs"] if j["id"] == job_id)
        if found["status"] in statuses:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{job_id}: still {found['status']} ({found['error']})")


def test_a_model_installed_tried_and_removed(hub):
    system = hub.get("/v1/install/system").json()
    assert system["server"] == "${llama_server}" and system["space"]["vram_gib"] == 14.7
    home = hub.tmp / "localchat-model"  # LocalChat's own `model` folder (conftest points it at the test's folder)
    assert system["folder"] == str(home) and system["home"]["folder"] == str(home)
    assert system["drives"][0]["folder"] == str(Path(hub.tmp / "T" / "AFO" / "models"))
    shown = hub.get("/v1/install/repo/test/Tiny-GGUF").json()
    assert [(v["quant"], v["bits"], v["verdict"]) for v in shown["variants"]] == [
        ("Q4_K_M", 4.5, "gpu"), ("Q8_0", 8.5, "gpu"), ("BF16", 16.0, "gpu")]  # fmt: skip
    assert shown["recommended"] == "Q8_0" and shown["projector"]["file"] == "mmproj-Tiny-Q8_0.gguf"
    assert shown["header"]["layers"] == 8 and shown["license"] == "mit"
    listed = hub.get("/v1/install/catalog").json()["models"]
    assert listed[0]["recommended"]["quant"] == "Q8_0" and listed[0]["purpose_words"] == ["Разговоры", "Картинки"]
    found = hub.get("/v1/install/search", params={"q": "tiny"}).json()["models"]
    assert [m["unrestricted"] for m in found] == [False, True]

    FakeHub.break_after["Tiny-Q8_0.gguf"] = 150_000  # once: the job tries again and goes on
    job = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q8_0"}).json()["job"]
    assert job["status"] == "queued" and job["model_id"] == "Tiny-Q8_0".lower()
    assert [f["role"] for f in job["files"]] == ["model", "mmproj"]  # the catalog offers it for pictures
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert done["trial"]["tries"][0]["ok"] and done["trial"]["where"] == "gpu" and done["percent"] == 100.0
    folder = home / "test" / "Tiny-GGUF"  # no drive, no folder chosen: LocalChat's own `model` folder
    assert (folder / "Tiny-Q8_0.gguf").read_bytes() == FILES["Tiny-Q8_0.gguf"] and FakeHub.ranges
    view = lc.model_view(hub.config.read_text(encoding="utf-8"), "tiny-q8_0")
    assert view["file"] == str(folder / "Tiny-Q8_0.gguf").replace("\\", "/") and view["mmproj"].endswith(
        "mmproj-Tiny-Q8_0.gguf") and view["context"] == 16384 and view["gpu_layers"] == "all"  # fmt: skip
    assert FakeLlamaSwap.asked[-1]["model"] == "tiny-q8_0"
    models = {m["id"]: m for m in hub.get("/v1/models").json()["models"]}
    assert models["llama-swap/tiny-q8_0"]["enabled"]
    again = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q8_0"})
    assert again.status_code == 409 and "уже установлена" in again.json()["detail"]
    assert hub.get("/v1/install/catalog").json()["models"][0]["installed"] == ["tiny-q8_0"]

    gone = hub.delete("/v1/install/models/tiny-q8_0").json()["job"]
    assert gone["status"] == "removed" and not folder.exists() and not folder.parent.exists()
    assert hub.config.read_text(encoding="utf-8") == CONFIG
    assert "llama-swap/tiny-q8_0" not in {m["id"] for m in hub.get("/v1/models").json()["models"]}  # off the list
    assert hub.delete("/v1/install/models/big").status_code == 404  # not one LocalChat put there

    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.get("/v1/install/jobs", headers={"Authorization": f"Bearer {other}"}).status_code == 403


def test_a_model_that_will_not_start_tries_smaller_settings_then_leaves_the_config(hub):
    FakeLlamaSwap.fail = True
    job = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False,
                                        "folder": str(hub.tmp / "models")}).json()["job"]  # fmt: skip
    failed = wait_for(hub, job["id"], "failed", "done")
    assert failed["status"] == "failed" and "не запустилась" in failed["error"]
    assert [(t["context"], t["gpu_layers"]) for t in failed["trial"]["tries"]] == [
        (16384, "all"), (8192, "all"), (4096, "all"), (4096, "auto")]  # fmt: skip
    assert hub.config.read_text(encoding="utf-8") == CONFIG  # its block went
    kept = hub.tmp / "models" / "test" / "Tiny-GGUF" / "Tiny-Q4_K_M.gguf"
    assert kept.exists() and [f["role"] for f in failed["files"]] == ["model"]  # files stay for «Повторить»
    FakeLlamaSwap.fail = False
    hub.post(f"/v1/install/jobs/{job['id']}/resume")
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done" and "tiny-q4_k_m" in lc.model_blocks(hub.config.read_text(encoding="utf-8"))


def test_an_install_paused_resumed_and_cancelled(hub):
    FakeHub.slow = 0.05
    job = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "BF16", "drive": "T"}).json()["job"]
    wait_for(hub, job["id"], "downloading")
    time.sleep(0.3)
    hub.post(f"/v1/install/jobs/{job['id']}/pause")
    paused = wait_for(hub, job["id"], "paused")
    folder = Path(paused["folder"])
    assert list(folder.glob("*.part")) and not paused["files"][0]["done"]
    FakeHub.slow = 0.0
    hub.post(f"/v1/install/jobs/{job['id']}/resume")
    assert wait_for(hub, job["id"], "done", "failed")["status"] == "done"
    assert (folder / "Tiny-BF16-00002-of-00002.gguf").read_bytes() == FILES["BF16/Tiny-BF16-00002-of-00002.gguf"]
    assert lc.model_view(hub.config.read_text(encoding="utf-8"), "tiny-bf16")["file"].endswith("00001-of-00002.gguf")

    FakeHub.slow = 0.05
    other = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False,
                                          "folder": str(hub.tmp / "cancel")}).json()["job"]  # fmt: skip
    wait_for(hub, other["id"], "downloading")
    hub.post(f"/v1/install/jobs/{other['id']}/cancel")
    assert wait_for(hub, other["id"], "cancelled")["status"] == "cancelled"
    assert not (hub.tmp / "cancel" / "test" / "Tiny-GGUF").exists()
    assert hub.delete(f"/v1/install/jobs/{other['id']}").json() == {"forgotten": other["id"]}
    assert hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q3"}).status_code == 404
    assert hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "drive": "Q"}).status_code == 422


def test_a_gated_model_wants_the_token_and_the_hub_checks_it(hub):
    refused = hub.get("/v1/install/repo/test/Gated-GGUF")
    assert refused.status_code == 403 and "лицензию" in refused.json()["detail"]
    hub.app.state.secrets.set(hf.TOKEN, "hf_good")
    assert hub.get("/v1/install/repo/test/Gated-GGUF").status_code == 200
    assert hub.get("/v1/install/system").json()["token"]
