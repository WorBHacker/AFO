"""Local models from Hugging Face into llama-swap (stage 10.1, D37): a repository's files as variants, each judged
against the computer's memory, the download that goes on after a broken connection and counts only with the right
sha256, the model's block in the config, the trial run that tries smaller settings, and removal. A stand-in
Hugging Face serves a tiny GGUF model; the stand-in llama-swap of the 8.5 tests reads the same config."""

import asyncio
import contextlib
import hashlib
import io
import json
import socket
import struct
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from common import llama_memory
from common.paths import data_paths, engines_home
from hub import engines, hf, tuning
from hub import llama_config as lc
from hub import model_install as mi
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_chat import WS_HOST, drain_until
from tests.test_llama_config import CONFIG, FakeLlamaSwap

GIB = 1024**3


def tiny_gguf(size: int, *, layers: int = 8, context: int = 16384, architecture: str = "tiny") -> bytes:
    """A GGUF file of `size` bytes: a real header (the architecture's keys, then a tokenizer list), then filler."""

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data

    a = architecture
    keys = {"general.architecture": a, "general.name": "Tiny", f"{a}.block_count": layers,
            f"{a}.context_length": context, f"{a}.embedding_length": 256, f"{a}.attention.head_count": 4,
            f"{a}.attention.head_count_kv": 2}  # fmt: skip
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
SEARCH = [  # what the stand-in Hugging Face's search finds: its tags, and gguf.total - the count it read (32.5)
    {"id": "test/Tiny-GGUF", "downloads": 10, "likes": 2, "trendingScore": 1, "createdAt": "2026-09-01T00:00:00.000Z",
     "lastModified": "2026-10-01T00:00:00.000Z", "tags": ["license:mit", "gguf", "conversational", "code"],
     "gguf": {"total": PARAMS}},
    {"id": "someone/Big-27B-MTP-GGUF", "downloads": 7, "likes": 1, "trendingScore": 3,
     "createdAt": "2026-09-03T00:00:00.000Z",
     "lastModified": "2026-10-02T00:00:00.000Z", "tags": ["gguf", "conversational", "image-text-to-text"],
     "gguf": {"total": 460_730_096}},  # the count read from its projector (32.4)
    {"id": "someone/Tiny-abliterated-GGUF", "downloads": 5, "likes": 9, "trendingScore": 2,
     "createdAt": "2026-09-02T00:00:00.000Z",
     "lastModified": "2026-10-03T00:00:00.000Z", "tags": ["gguf", "conversational"], "gguf": {"total": 2_000_000_000}},
]  # fmt: skip


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


# a picture model (29.3): its files in three repositories, and the picture engine's pinned release on GitHub
PICTURES = {"test/Clip-GGUF": {"clip-Q4_K_M.gguf": b"C" * 50_000},  # a video model (29.8)
            "test/Umt5-GGUF": {"umt5-Q4_K_M.gguf": b"U" * 40_000},
            "test/Draw-GGUF": {"draw-Q4_0.gguf": b"D" * 70_000},
            "test/Words-GGUF": {"words-Q4_K_M.gguf": b"W" * 60_000},
            "test/Vae": {"split_files/vae/vae.safetensors": b"V" * 30_000}}  # fmt: skip
ENGINE = {f"sd-master-{engines.SD_TAG.rsplit('-', 1)[1]}-bin-win-cuda12-x64.zip":
          _zip({"sd-server.exe": b"MZ server", "ggml-cuda.dll": b"MZ"}),
          "cudart-sd-bin-win-cu12-x64.zip": _zip({"cudart64_12.dll": b"MZ"}),
          f"sd-master-{engines.SD_TAG.rsplit('-', 1)[1]}-bin-win-cpu-x64.zip":
          _zip({"sd-server.exe": b"MZ cpu server", "ggml-cpu-x64.dll": b"MZ"})}  # fmt: skip


class FakeHub(BaseHTTPRequestHandler):
    """Hugging Face's API and file downloads as LocalChat uses them: search, a repository's info and tree, files by
    Range (redirected to a 'CDN' on another host name), a gated repository that wants a token."""

    port = 0
    break_after: ClassVar[dict] = {}  # file -> bytes sent before the connection breaks (once)
    corrupt: ClassVar[set] = set()  # files served with a wrong byte (their sha256 will not match)
    slow = 0.0  # seconds a chunk
    cdn_auth: ClassVar[list] = []  # the Authorization header each CDN request came with
    ranges: ClassVar[list] = []
    spans: ClassVar[list] = []  # (file, start, end) of each Range request with an end (a piece, 2026-11-05)
    no_ranges = False  # a server that ignores Range: the whole file, 200
    overlong = 0  # bytes a piece's answer goes on past the end asked for (32.1)
    active = peak = 0  # CDN requests being served at once
    searches: ClassVar[list] = []  # the query of each search (32.5)
    picture_auth: ClassVar[list] = []  # the Authorization header of each picture file's request
    github_auth: ClassVar[list] = []  # and of each engine archive's (GitHub's: never Hugging Face's token)
    lock = threading.Lock()

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
        if path == "/api/models":  # as Hugging Face filters: every tag, the words in the id, the count it read
            query = parse_qs(url.query)
            FakeHub.searches.append(query)
            words = query.get("search", [""])[0].lower()
            found = [m for m in SEARCH if all(t in m["tags"] for t in query["filter"]) and words in m["id"].lower()]
            if "num_parameters" in query:
                bounds = dict(b.split(":") for b in query["num_parameters"][0].split(","))
                low, high = int(bounds.get("min", 0)), int(bounds.get("max", 10**15))
                found = [m for m in found if low <= (m["gguf"].get("total") or 0) <= high]
            if "author" in query:  # who put it there (41.2)
                found = [m for m in found if m["id"].startswith(query["author"][0] + "/")]
            found.sort(key=lambda m: m[query["sort"][0]], reverse=True)
            self._json(found[: int(query["limit"][0])])
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
        elif picture := next((r for r in PICTURES if path.startswith((f"/api/models/{r}", f"/{r}/"))), None):
            files = PICTURES[picture]
            if path == f"/api/models/{picture}":
                self._json({"id": picture, "sha": "def456", "tags": ["license:apache-2.0"]})
            elif path == f"/api/models/{picture}/tree/main":
                self._json([{"type": "file", "path": name, "size": len(data),
                             "lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}}
                            for name, data in files.items()])  # fmt: skip
            else:  # /<repo>/resolve/<commit>/<path>
                FakeHub.picture_auth.append(self.headers.get("Authorization"))
                self._send_file(path.split("/resolve/", 1)[1].split("/", 1)[1], files)
        elif path == f"/repos/{engines.SD_CPP}/releases/tags/{engines.SD_TAG}":
            assets = [{"name": name, "browser_download_url": f"http://127.0.0.1:{self.port}/gh/{name}",
                       "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                      for name, data in ENGINE.items()]  # fmt: skip
            self._json({"tag_name": engines.SD_TAG, "assets": assets})
        elif path.startswith("/gh/"):
            FakeHub.github_auth.append(self.headers.get("Authorization"))
            self._send_file(path.removeprefix("/gh/"), ENGINE)
        else:
            self._json({"error": "not found"}, 404)

    def _file(self, name):
        with FakeHub.lock:
            FakeHub.active += 1
            FakeHub.peak = max(FakeHub.peak, FakeHub.active)
        try:
            self._send_file(name)
        finally:
            with FakeHub.lock:
                FakeHub.active -= 1

    def _send_file(self, name, files=None):
        if files is None:
            FakeHub.cdn_auth.append(self.headers.get("Authorization"))
        data = (files or FILES)[name]
        if name in self.corrupt:
            data = data[:-1] + b"X"
        start, end = 0, len(data) - 1
        if self.headers.get("Range") and not self.no_ranges:
            first, _, last = self.headers["Range"].split("=")[1].partition("-")
            start = int(first)
            if last:
                end = min(int(last) + self.overlong, len(data) - 1)
                FakeHub.spans.append((name, start, end))
            else:
                FakeHub.ranges.append((name, start))
            self.send_response(206)
            self.send_header("content-range", f"bytes {start}-{end}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start : end + 1]
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
    FakeHub.spans, FakeHub.no_ranges, FakeHub.active, FakeHub.peak, FakeHub.overlong = [], False, 0, 0, 0
    FakeHub.picture_auth, FakeHub.github_auth, FakeHub.searches = [], [], []
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


def test_a_model_file_named_imatrix_is_a_model():
    """32.3: «imatrix» anywhere in a file's name threw the model away as imatrix data ("no GGUF files in this
    repository" for cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF) - only the data goes now."""
    files = [{"path": path, "size": size} for path, size in (
        ("Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf", 17_130_000_000), ("Model-IQ4_XS-imatrix-fromq8.gguf", 15_000_000_000),
        ("imatrix-agentic-v2.gguf", 9_000_000), ("imatrix.gguf", 8_000_000), ("Model.imatrix.gguf", 8_000_000),
        ("Model-imatrix.gguf", 8_000_000), ("mmproj-Qwen3.8-27B-F16.gguf", 930_000_000))]  # fmt: skip
    found, projectors = hf.variants(files)
    assert [(v["quant"], v["files"][0]["path"]) for v in found] == [
        ("IQ4_XS", "Model-IQ4_XS-imatrix-fromq8.gguf"), ("NVFP4", "Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf")]  # fmt: skip
    assert [p["file"] for p in projectors] == ["mmproj-Qwen3.8-27B-F16.gguf"]


def test_the_parameter_count_by_the_name_when_hugging_face_read_a_projector():
    """32.4: Hugging Face reads a repository's parameter count from one of its GGUF files - for some, the vision
    projector or a draft (cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF: 0.46 billion, architecture «clip»): «≈ 297 bits
    a weight», and the choice by bits went off. When the name says more than twice as many, the name is taken."""
    repo = "cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF"
    files = [("Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf", 17_130_000_000), ("mmproj-Qwen3.8-27B-F16.gguf", 930_000_000)]

    def answer(request):
        if request.url.path.endswith("/tree/main"):
            return httpx.Response(200, json=[{"type": "file", "path": p, "size": s, "lfs": {"oid": "0" * 64}}
                                             for p, s in files])  # fmt: skip
        return httpx.Response(200, json={"id": repo, "gguf": {"total": 460_730_096, "architecture": "clip"}})

    async def look():
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            return await hf.repository(http, repo)

    found = asyncio.run(look())
    assert found["params"] == 27_000_000_000 and [v["bits"] for v in found["variants"]] == [5.1]
    names = {"unsloth/Qwen3.8-27B-GGUF": 27_000_000_000, "ggml-org/Qwen3.5-0.8B-GGUF": 800_000_000,
             "x/SmolLM2-135M-Instruct-GGUF": 135_000_000, "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF": 30_000_000_000,
             "x/Qwen2.5-7B-Instruct-1M-GGUF": 7_000_000_000, "ggml-org/gpt-oss-20b-GGUF": 20_000_000_000,
             "ggml-org/gemma-4-E4B-it-GGUF": 0, "x/Mixtral-8x7B-GGUF": 0}  # fmt: skip
    assert {name: hf.named_params(name) for name in names} == names
    assert hf.params_of({"id": "x/Qwen3-30B-A3B-GGUF", "gguf": {"total": 30_532_122_624}}) == 30_532_122_624
    assert hf.params_of({"id": "ggml-org/gemma-4-E4B-it-GGUF", "gguf": {"total": 7_518_000_000}}) == 7_518_000_000
    assert hf.params_of({"id": "x/SmolLM2-135M-GGUF"}) == 135_000_000 and hf.params_of({"id": "x/y"}) == 0


def test_this_computer_against_a_model():
    space = mi.room(SPACE)
    assert (space["vram_gib"], space["ram_gib"]) == (15.0, 61.0)  # the computer's own, less the desktop's and Windows'
    swapped = mi.room({**SPACE, "local": {"running": [{"model": "big"}]}})
    assert swapped["vram_gib"] == 15.0 and swapped["loaded"] == ["big"]  # the loaded one would unload
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
    # llama-swap v270 keeps its groups under routing: (29.1) - a member there is as much in a group
    routed = new + 'routing:\n  router:\n    use: group\n    settings:\n      groups:\n        "pair":\n' \
        '          members:\n            - big\n            - tiny-q8_0\n'  # fmt: skip
    with pytest.raises(lc.ConfigError, match="группу"):
        lc.remove_model(routed, "tiny-q8_0")
    assert "tiny-q8_0" not in lc.model_blocks(lc.remove_model(new + "routing:\n  router:\n    use: group\n",
                                                              "tiny-q8_0"))  # fmt: skip
    # a picture model (29.3, D55): sd-server with its set of files, ready when it lists its models
    pictures = lc.sd_block("flux2-klein-4b", name="FLUX.2 klein 4B", description="test", server="${sd_server}",
                           files={"diffusion-model": "F:\\pictures\\klein.gguf", "llm": "F:/pictures/qwen.gguf",
                                  "vae": "F:/pictures/vae.safetensors"},
                           flags=["--cfg-scale", "1", "--steps", "4"],
                           metadata={"source": "huggingface.co/leejet/FLUX.2-klein-4B-GGUF"})  # fmt: skip
    drawn = lc.add_model(CONFIG, "flux2-klein-4b", pictures)
    view = lc.model_view(drawn, "flux2-klein-4b")
    assert (view["engine"], view["editable"], view["name"]) == ("stable-diffusion.cpp", False, "FLUX.2 klein 4B")
    assert '--diffusion-model "F:/pictures/klein.gguf"' in view["command"] and "--listen-port ${PORT}" in pictures
    assert "checkEndpoint: /v1/models" in pictures and "engine: stable-diffusion.cpp" in pictures
    assert lc.remove_model(drawn, "flux2-klein-4b") == CONFIG
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
def pieces(monkeypatch):
    """A big file, as the hub sees it: pieces of 64 KB (64 MB in life), four connections at once (eight in life)."""
    monkeypatch.setattr(hf, "SEGMENT", 64 * 1024)
    monkeypatch.setattr(hf, "PARALLEL_MIN", 64 * 1024)
    monkeypatch.setattr(hf, "PARTS", 4)
    monkeypatch.setattr(hf, "RETRY_S", 0.01)


def test_a_big_file_comes_in_pieces_over_several_connections(fake_hub, pieces, tmp_path):
    """Live on 2026-11-05 one connection to Hugging Face's CDN gave 0.6-19 MB/s here and 4-16 gave 60-70 (the line's
    limit): a big file comes in pieces, several at once, each written at its place; the sha256 runs behind them."""
    name = "BF16/Tiny-BF16-00001-of-00002.gguf"  # 320 000 bytes: five pieces
    data = FILES[name]
    FakeHub.slow = 0.02  # long enough for the pieces to overlap, on a computer busy with the whole test run too
    seen = []
    dest = tmp_path / "Tiny-BF16-00001-of-00002.gguf"
    hf.download(hf.file_url("test/Tiny-GGUF", name), dest, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                token="hf_secret", report=seen.append, cancel=threading.Event())  # fmt: skip
    assert dest.read_bytes() == data and seen[-1] == len(data)
    assert sorted((s, e) for _, s, e in FakeHub.spans) == [(0, 65535), (65536, 131071), (131072, 196607),
                                                           (196608, 262143), (262144, 319999)]  # fmt: skip
    assert FakeHub.peak >= 2 and FakeHub.ranges == []  # at once, and none «to the end»
    assert set(FakeHub.cdn_auth) == {None}  # the token stays with huggingface.co
    assert sorted(p.name for p in tmp_path.iterdir()) == [dest.name]  # no part, no list of pieces left


def test_pieces_go_on_after_a_break_and_after_a_restart(fake_hub, pieces, tmp_path):
    name = "BF16/Tiny-BF16-00001-of-00002.gguf"
    data = FILES[name]
    url = hf.file_url("test/Tiny-GGUF", name)
    digest = hashlib.sha256(data).hexdigest()
    FakeHub.break_after[name] = 30_000  # one piece's connection breaks: that piece is asked again
    dest = tmp_path / "one.gguf"
    hf.download(url, dest, size=len(data), sha256=digest, token=None, report=lambda n: None,
                cancel=threading.Event())  # fmt: skip
    assert dest.read_bytes() == data and len(FakeHub.spans) == 6  # five pieces, one of them twice

    FakeHub.spans.clear()
    FakeHub.slow = 0.05  # a piece ~0.2 s: the fifth starts only when one of the first four is done
    cancel, later = threading.Event(), tmp_path / "two.gguf"
    marks = tmp_path / "two.gguf.parts"

    def stop_once_a_piece_is_done(_n):  # the user stops it (or the hub goes down) half way
        with contextlib.suppress(OSError, ValueError):  # the list is there from the start (32.1), a piece in it later
            if json.loads(marks.read_text(encoding="utf-8")):
                cancel.set()

    with pytest.raises(hf.Cancelled):
        hf.download(url, later, size=len(data), sha256=digest, token=None, report=stop_once_a_piece_is_done,
                    cancel=cancel)  # fmt: skip
    done = json.loads(marks.read_text(encoding="utf-8"))
    assert (tmp_path / "two.gguf.part").stat().st_size == len(data) and 0 < len(done) < 5
    FakeHub.spans.clear()
    FakeHub.slow = 0.0
    hf.download(url, later, size=len(data), sha256=digest, token=None, report=lambda n: None,
                cancel=threading.Event())  # a new hub: a new Progress, the pieces done read from the list  # fmt: skip
    assert later.read_bytes() == data and not marks.exists()
    asked = {s // (64 * 1024) for _, s, _ in FakeHub.spans}
    assert asked == set(range(5)) - set(done)  # only the pieces that were not there


def test_pieces_stopped_before_the_first_one_is_whole_go_on_from_the_start(fake_hub, pieces, tmp_path):
    """32.1: a stop before any piece was whole (a pause in the first seconds, the hub going down) left the part as
    long as the file and no list of pieces - and the next try took it for a part that came over one connection: its
    zeros as pieces done, the progress near the end, the sha256 wrong and the whole file again."""
    name = "BF16/Tiny-BF16-00001-of-00002.gguf"
    data = FILES[name]
    url = hf.file_url("test/Tiny-GGUF", name)
    digest = hashlib.sha256(data).hexdigest()
    FakeHub.slow = 0.05  # a piece ~0.2 s
    cancel, dest = threading.Event(), tmp_path / "early.gguf"
    with pytest.raises(hf.Cancelled):
        hf.download(url, dest, size=len(data), sha256=digest, token=None, report=lambda _n: cancel.set(),
                    cancel=cancel)  # fmt: skip
    FakeHub.spans.clear()
    FakeHub.slow = 0.0
    seen = []
    hf.download(url, dest, size=len(data), sha256=digest, token=None, report=seen.append,
                cancel=threading.Event())  # fmt: skip
    assert dest.read_bytes() == data and seen[0] < 64 * 1024  # it starts from nothing, as it is
    assert sorted(s // (64 * 1024) for _, s, _ in FakeHub.spans) == [0, 1, 2, 3, 4]


def test_a_piece_takes_only_its_own_bytes(fake_hub, pieces, tmp_path, monkeypatch):
    """32.1: a server that answers a piece with more than was asked - what comes past its end is not written over the
    next piece."""
    name = "BF16/Tiny-BF16-00001-of-00002.gguf"
    data = FILES[name]
    dest = tmp_path / "long.gguf"
    (tmp_path / "long.gguf.part").write_bytes(b"\0" * 65536 + data[65536:])  # all but the first piece on disk
    (tmp_path / "long.gguf.parts").write_text("[1, 2, 3, 4]", encoding="utf-8")
    monkeypatch.setattr(hf, "CHUNK", 20_000)  # chunks that do not end where the piece does
    FakeHub.overlong = 30_000
    hf.download(hf.file_url("test/Tiny-GGUF", name), dest, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                token=None, report=lambda _n: None, cancel=threading.Event())  # fmt: skip
    assert dest.read_bytes() == data


def test_pieces_fall_back_to_one_connection_and_a_wrong_file_is_thrown_away(fake_hub, pieces, tmp_path):
    name = "BF16/Tiny-BF16-00001-of-00002.gguf"
    data = FILES[name]
    url = hf.file_url("test/Tiny-GGUF", name)
    digest = hashlib.sha256(data).hexdigest()
    FakeHub.no_ranges = True  # a server that sends the whole file whatever is asked
    plain = tmp_path / "plain.gguf"
    hf.download(url, plain, size=len(data), sha256=digest, token=None, report=lambda n: None,
                cancel=threading.Event())  # fmt: skip
    assert plain.read_bytes() == data and sorted(p.name for p in tmp_path.iterdir()) == ["plain.gguf"]

    FakeHub.no_ranges = False
    FakeHub.corrupt.add(name)
    broken = tmp_path / "broken.gguf"
    with pytest.raises(hf.HubError, match="повреждённым") as caught:
        hf.download(url, broken, size=len(data), sha256=digest, token=None, report=lambda n: None,
                    cancel=threading.Event())  # fmt: skip
    assert caught.value.status == 409
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.gguf"]  # the part and its list went too


@pytest.fixture
def hub(tmp_path, monkeypatch, fake_hub):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    FakeLlamaSwap.config, FakeLlamaSwap.frozen, FakeLlamaSwap.fail, FakeLlamaSwap.asked = config, None, False, []
    FakeLlamaSwap.blank = False
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
    assert system["server"] == "${llama_server}" and system["space"]["vram_gib"] == 15.0
    home = hub.tmp / "localchat-model"  # LocalChat's own `model` folder (conftest points it at the test's folder)
    assert system["folder"] == str(home) and system["home"]["folder"] == str(home)
    assert system["drives"][0]["folder"] == str(Path(hub.tmp / "T" / "AFO" / "models"))
    shown = hub.get("/v1/install/repo/test/Tiny-GGUF").json()
    assert [(v["quant"], v["bits"], v["verdict"]) for v in shown["variants"]] == [
        ("Q4_K_M", 4.5, "gpu"), ("Q8_0", 8.5, "gpu"), ("BF16", 16.0, "gpu")]  # fmt: skip
    assert shown["recommended"] == "Q8_0" and shown["projector"]["file"] == "mmproj-Tiny-Q8_0.gguf"
    assert shown["header"]["layers"] == 8 and shown["license"] == "mit"
    listed = hub.get("/v1/install/catalog").json()["models"]
    assert listed[0]["recommended"]["quant"] == "Q8_0"
    assert listed[0]["purpose_words"] == ["Разговоры", "Понимать картинки"]
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


DRAW = {"id": "draw", "name": "Draw", "purposes": ["image"], "tier": "main", "license": "apache-2.0",
        "original": "test/Draw", "size": 1024, "flags": ["--cfg-scale", "1", "--steps", "4"], "about": {"ru": "Рисует"},
        "files": [{"role": "diffusion-model", "repo": "test/Draw-GGUF", "path": "draw-Q4_0.gguf"},
                  {"role": "llm", "repo": "test/Words-GGUF", "path": "words-Q4_K_M.gguf"},
                  {"role": "vae", "repo": "test/Vae", "path": "split_files/vae/vae.safetensors"}]}  # fmt: skip
CLIP = {"id": "clip", "name": "Clip", "kind": "video", "purposes": ["video"], "tier": "video", "license": "apache-2.0",
        "original": "test/Clip", "size": 832, "about": {"ru": "Снимает"},
        "flags": ["--cfg-scale", "1", "--sampling-method", "euler", "--steps", "4", "--flow-shift", "5"],
        "files": [{"role": "diffusion-model", "repo": "test/Clip-GGUF", "path": "clip-Q4_K_M.gguf"},
                  {"role": "t5xxl", "repo": "test/Umt5-GGUF", "path": "umt5-Q4_K_M.gguf"},
                  {"role": "vae", "repo": "test/Vae", "path": "split_files/vae/vae.safetensors"}]}  # fmt: skip
MACHINE = {"build": "cuda12", "card": "RTX", "memory_gib": 15.6, "pictures": "main", "quality": False,
           "video": "local", "tiles": False, "why": "RTX: картинки 1024 × 1024 за секунды"}  # fmt: skip


def test_a_picture_model_installed_with_its_engine_tried_and_removed(hub, monkeypatch):
    """29.3 (D55): a picture model - files from three repositories - and, with the first one, the picture engine's
    pinned build from GitHub; the trial is a picture through llama-swap, a blank one does not count. 29.4в: beside
    the card's build, the processor's - and each model a second block on it, the ladder's last rung before the
    cloud; a model whose trial does not come out on the card is tried on the processor."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    catalog["pictures"] = [DRAW, {**DRAW, "id": "best", "tier": "quality", "name": "Best"}]
    mi.CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
    installer = hub.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: MACHINE)
    monkeypatch.setattr(installer, "token", lambda: "hf_good")  # Hugging Face's token: never sent to GitHub

    listed = hub.get("/v1/install/pictures").json()
    assert listed["machine"]["build"] == "cuda12"
    draw, best = listed["models"]
    assert (draw["size"], draw["verdict"], draw["recommended"], draw["purpose_words"]) == (160_000, "fast", True,
                                                                                           ["Рисовать"])  # fmt: skip
    assert best["verdict"] == "slow" and not best["recommended"]  # a quality model wants 6 GB: minutes here
    assert mi.picture_offer(draw) == {  # what the first-run wizard offers for «Рисовать»
        "id": "draw", "name": "Draw", "repo": "pictures:draw", "purposes": ["image"], "installed": [],
        "recommended": {"quant": "", "size": 160_000, "verdict": "gpu", "words": "быстро"}}  # fmt: skip

    job = hub.post("/v1/install/pictures", json={"id": "draw"}).json()["job"]
    assert [(f["role"], f.get("build")) for f in job["files"]] == [
        ("engine", "cuda12"), ("engine", "cuda12"), ("engine", "cpu"), ("diffusion-model", None), ("llm", None),
        ("vae", None)]  # fmt: skip
    assert job["total_bytes"] == 160_000 + sum(len(d) for d in ENGINE.values())
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert done["trial"]["size"] == 512 and done["percent"] == 100.0
    text = hub.config.read_text(encoding="utf-8")
    server = engines_home() / "stable-diffusion.cpp" / f"{engines.SD_TAG}-cuda12" / "sd-server.exe"
    assert server.read_bytes() == b"MZ server" and lc.macros(text)["sd_server"] == f'"{server.as_posix()}"'
    assert (server.parent / "cudart64_12.dll").exists()  # the CUDA runtime beside it
    cpu = engines_home() / "stable-diffusion.cpp" / f"{engines.SD_TAG}-cpu" / "sd-server.exe"
    assert cpu.read_bytes() == b"MZ cpu server" and lc.macros(text)["sd_server_cpu"] == f'"{cpu.as_posix()}"'
    assert done["plan"]["cpu"] == "draw-cpu" and done["trial"].get("where") != "cpu"  # the card drew it
    on_cpu = lc.model_view(text, "draw-cpu")
    assert on_cpu["engine"] == lc.SD_ENGINE and on_cpu["command"].startswith(f'"{cpu.as_posix()}"')
    view = lc.model_view(text, "draw")
    assert on_cpu["command"].replace(cpu.as_posix(), server.as_posix()) == view["command"]  # the same files
    folder = hub.tmp / "localchat-model" / "pictures" / "draw"
    assert view["engine"] == lc.SD_ENGINE and "--steps 4" in view["command"]
    assert f'--vae "{(folder / "vae.safetensors").as_posix()}"' in view["command"]
    assert (folder / "vae.safetensors").read_bytes() == PICTURES["test/Vae"]["split_files/vae/vae.safetensors"]
    assert FakeLlamaSwap.asked[-1] | {"prompt": ""} == {"model": "draw", "prompt": "", "size": "512x512", "n": 1,
                                                        "response_format": "b64_json"}  # fmt: skip
    assert FakeHub.picture_auth and set(FakeHub.picture_auth) == {"Bearer hf_good"}
    assert FakeHub.github_auth and not any(FakeHub.github_auth)
    chat = {m["id"] for m in hub.get("/v1/models").json()["models"]}
    assert "llama-swap/draw" not in chat and "llama-swap/draw-cpu" not in chat  # not chat models
    assert [(m["model_id"], m["cpu_id"]) for m in hub.get("/v1/visual/models").json()["models"]] == [
        ("draw", "draw-cpu")]  # fmt: skip
    assert hub.get("/v1/install/pictures").json()["models"][0]["installed"] == ["draw"]
    again = hub.post("/v1/install/pictures", json={"id": "draw"})
    assert again.status_code == 409 and "уже установлена" in again.json()["detail"]

    gone = hub.delete("/v1/install/models/draw").json()["job"]
    assert gone["status"] == "removed" and not folder.exists() and server.exists()  # the engine stays for others
    text = hub.config.read_text(encoding="utf-8")
    assert not {"draw", "draw-cpu"} & set(lc.model_blocks(text)) and "sd_server" in lc.macros(text)

    FakeLlamaSwap.blank = {"draw"}  # the next one: no engine to fetch; on the card its trial comes out flat grey
    job = hub.post("/v1/install/pictures", json={"id": "draw"}).json()["job"]
    assert [f["role"] for f in job["files"]] == ["diffusion-model", "llm", "vae"]
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done" and done["trial"]["where"] == "cpu", done["error"]  # the processor drew it
    assert [b["model"] for b in FakeLlamaSwap.asked[-2:]] == ["draw", "draw-cpu"]
    models = hub.get("/v1/visual/models").json()["models"]
    assert (models[0]["instead"], models[0]["cpu_id"]) == ("draw-cpu", "draw-cpu")  # pictures begin there
    hub.delete("/v1/install/models/draw")

    FakeLlamaSwap.blank = True  # grey on the processor too: it does not count
    job = hub.post("/v1/install/pictures", json={"id": "draw"}).json()["job"]
    failed = wait_for(hub, job["id"], "failed", "done")
    assert failed["status"] == "failed" and "пустой" in failed["error"] and "Повторить" in failed["error"]
    text = hub.config.read_text(encoding="utf-8")
    assert not {"draw", "draw-cpu"} & set(lc.model_blocks(text))  # withdrawn, both; its files stay
    assert (folder / "draw-Q4_0.gguf").exists()
    assert hub.post("/v1/install/pictures", json={"id": "nope"}).status_code == 404


def test_a_video_model_installed_and_tried_with_a_short_video(hub, monkeypatch):
    """29.8а: Wan2.2-TI2V-5B (here «Clip»): its files from three repositories, the same engine; offered where this
    computer makes videos (hardware.pictures_for: local, or slow); its VAE in tiles where the tier says (Vulkan on
    NVIDIA falls without, 29.1); the trial - a short video through vid_gen."""
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{FakeHub.port}")
    catalog = json.loads(mi.CATALOG.read_text(encoding="utf-8"))
    catalog["pictures"] = [DRAW, CLIP]
    mi.CATALOG.write_text(json.dumps(catalog), encoding="utf-8")
    installer = hub.app.state.installer
    monkeypatch.setattr(installer, "machine", lambda: {**MACHINE, "video": "slow", "tiles": True})
    listed = {m["id"]: m for m in hub.get("/v1/install/pictures").json()["models"]}
    assert (listed["clip"]["verdict"], listed["clip"]["kind"], listed["clip"]["purpose_words"]) == (
        "slow", "video", ["Видео"])  # fmt: skip

    job = hub.post("/v1/install/pictures", json={"id": "clip"}).json()["job"]
    assert [f["role"] for f in job["files"] if f["role"] != "engine"] == ["diffusion-model", "t5xxl", "vae"]
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert done["plan"]["kind"] == "video" and done["trial"]["frames"] == 9 and "cpu" not in done["plan"]
    view = lc.model_view(hub.config.read_text(encoding="utf-8"), "clip")
    assert "--vae-tiling" in view["command"] and "--t5xxl" in view["command"] and "--flow-shift 5" in view["command"]
    trial = FakeLlamaSwap.video[-1]
    assert trial["path"] == "/upstream/clip/sdcpp/v1/vid_gen" and trial["video_frames"] == 9
    assert [m["model_id"] for m in hub.get("/v1/visual/models").json()["videos"]] == ["clip"]
    hub.delete("/v1/install/models/clip")

    monkeypatch.setattr(installer, "machine", lambda: {**MACHINE, "video": "cloud"})
    assert hub.get("/v1/install/pictures").json()["models"][1]["verdict"] == "no"
    refused = hub.post("/v1/install/pictures", json={"id": "clip"})
    assert refused.status_code == 409 and "облак" in refused.json()["detail"]


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


def test_a_model_installed_while_the_engine_comes_waits_for_it(hub):
    """39.3 (2026-11-12): the scanner fetches the engine by itself (39.1) and the wizard offers a model at once -
    «Установить» said «нет конфига llama-swap» (409) while llama-swap was still on its way. Now the model downloads
    meanwhile and waits for the engine, «ждёт программу для локальных моделей»; once it is in, the model goes into
    its config and is tried. With no engine coming the refusal stays."""
    text = hub.config.read_text(encoding="utf-8")
    hub.config.unlink()  # no llama-swap here yet
    refused = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M"})
    assert refused.status_code == 409 and "нет конфига llama-swap" in refused.json()["detail"]

    setup_ = hub.app.state.setup
    setup_.job = {"status": "downloading", "auto": True, "build": "cuda-13", "done": 0, "total": 0}  # on its way
    sent = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M"})
    assert sent.status_code == 200, sent.text
    job = sent.json()["job"]
    deadline = time.monotonic() + 20
    while "ждёт программу для локальных моделей" not in (found := wait_for(hub, job["id"], "queued"))["error"]:
        assert time.monotonic() < deadline, found
        time.sleep(0.05)
    assert found["done_bytes"] == found["total_bytes"]  # downloaded meanwhile

    hub.config.write_text(text, encoding="utf-8")  # the engine is in
    setup_.job = {"status": "done", "auto": True}
    done = wait_for(hub, job["id"], "done", "failed")
    assert done["status"] == "done", done["error"]
    assert done["model_id"] in lc.model_blocks(hub.config.read_text(encoding="utf-8"))


def test_a_model_is_judged_by_this_computer_not_by_what_is_busy_now(hub):
    """38.1 (2026-11-12, a screenshot from the user's laptop): an RTX 3060 of 6 GB and 16 GB of RAM, Ollama holding a
    14B model. Ollama is not llama-swap, so the hub counted its memory as taken and judged every model «не поместится»,
    one of 3 GB too, and the wizard had nothing to offer. What fits is judged by the computer itself (computer.json):
    the video memory less 1 GB, the RAM less 3 GB; llama.cpp's buffers are one figure for the installer and tuning."""
    laptop = {
        "gpu": {"name": "RTX 3060 Laptop", "vram_total_mib": 6144, "vram_used_mib": 5734},
        "memory": {"ram_total_gib": 16.0, "ram_used_gib": 14.5},
        "local": {"reachable": True, "running": []},
    }
    hub.app.state.status.latest = laptop
    space = hub.get("/v1/install/system").json()["space"]
    assert (space["vram_gib"], space["ram_gib"]) == (5.0, 13.0)
    info = {"layers": 36, "kv_layers": 36, "kv_heads": 8, "key_length": 128, "value_length": 128,
            "context_trained": 40960}  # fmt: skip
    assert mi.verdict(3 * GIB, info, space) == "gpu"
    offered = hub.get("/v1/install/catalog").json()["models"][0]
    assert (offered.get("recommended") or {}).get("verdict") == "gpu"  # the wizard has a model to offer

    hub.app.state.status.latest = {**laptop, "local": {"reachable": True, "running": [{"model": "a"}]}}
    assert hub.get("/v1/install/system").json()["space"]["vram_gib"] == 5.0  # llama-swap's own model: the same room
    assert tuning.COMPUTE_GIB == llama_memory.COMPUTE_GIB  # one figure for llama.cpp's buffers


SMALL_BLOCK = """
  "{name}":
    name: "{name}"
    cmd: |
      ${{llama_server}}
      -m "{path}"
      -ngl 99 -c 4096
      --host 127.0.0.1 --port ${{PORT}}
"""


def test_a_model_loading_while_other_programs_hold_the_memory_says_so(hub, monkeypatch):
    """38.2 (2026-11-12): what fits is judged by the computer (38.1), so a model loads even while another program
    holds the memory - Ollama's 14B on the laptop - and the driver lends it shared memory, several times slower. Not
    a refusal: words while it loads, «сейчас свободно … — закройте лишние программы». None when the memory is free,
    and none for what llama-swap's own models hold: they unload for it."""
    model = hub.tmp / "small.gguf"
    model.write_bytes(tiny_gguf(180_000))
    blocks = "".join(SMALL_BLOCK.format(name=name, path=model.as_posix()) for name in ("small", "other"))
    hub.config.write_text(hub.config.read_text(encoding="utf-8") + blocks, encoding="utf-8")
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    monkeypatch.setattr(FakeLlamaSwap, "banner", ["llama-swap loading model: small\n", "Done! (0.4s)\n"])

    def loading(status):
        hub.app.state.status.latest = status
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        with hub.websocket_connect(
            "/v1/events", headers={"Authorization": hub.headers["Authorization"], **WS_HOST}
        ) as ws:
            ws.receive_json()
            sent = hub.post(
                f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "llama-swap/small"}
            )
            assert sent.status_code == 202
            seen = drain_until(ws, "chat.done")
        said = [m["payload"] for m in seen if m["type"] == "chat.loading"]
        assert said  # llama-swap's banner came as loading
        return {payload.get("memory", "") for payload in said}

    gpu, memory = {"name": "RTX 3060 Laptop", "vram_total_mib": 6144}, {"ram_total_gib": 16.0, "ram_used_gib": 8.0}
    ollama = {"gpu": {**gpu, "vram_used_mib": 5734}, "memory": memory, "local": {"reachable": True, "running": []}}
    (words,) = loading(ollama)
    assert words.startswith("сейчас свободно 0.4 ГБ видеопамяти из 6") and "закройте лишние программы" in words
    assert loading({**ollama, "gpu": {**gpu, "vram_used_mib": 600}}) == {""}  # the memory is free
    ours = {
        **ollama,
        "gpu": {**gpu, "vram_used_mib": 5632},
        "local": {"reachable": True, "running": [{"model": "other"}]},
    }
    assert loading(ours) == {""}  # 5.5 GB taken, 0.8 of it llama-swap's «other», which unloads: enough is free

    def busy(status):  # the install window's line: the same words, while llama-swap holds nothing
        hub.app.state.status.latest = status
        return hub.get("/v1/install/system").json()["space"]["busy"]

    assert busy(ollama).startswith("сейчас свободно 0.4 ГБ видеопамяти из 6")
    assert busy({**ollama, "gpu": {**gpu, "vram_used_mib": 1300}}) == ""  # the desktop's share, a little over
    assert busy(ours) == ""  # llama-swap's own model: the window says it unloads

    split = {"vram_gb": 3.0, "ram_gb": 6.0}  # a model partly in RAM, with 2 GB of RAM free now
    words = llama_memory.short_now(
        split, {**ollama, "gpu": {**gpu, "vram_used_mib": 0}, "memory": {**memory, "ram_used_gib": 14.0}}
    )
    assert words.startswith("сейчас свободно 2.0 ГБ ОЗУ") and "видеопамяти" not in words


def test_the_memory_reserves_are_the_users_to_change(hub):
    """38.3 (2026-11-12, the user's word: «запасы для опытных меняются в Настройки → Модели»): what a model may not
    count on of the computer's memory - 1 GB of the video memory, 3 GB of the RAM - is the user's to change. The
    installer, the wizard, the search, tuning and the window's estimate of a model's launch judge by it; it stays
    across a restart; what is out of bounds is refused in words."""
    system = hub.get("/v1/install/system").json()
    default = {"vram_gib": 1.0, "ram_gib": 3.0}
    assert system["reserve"] == {**default, "default": default}
    changed = hub.put("/v1/install/reserve", json={"vram_gib": 2.5, "ram_gib": 6}).json()
    assert changed["reserve"] == {"vram_gib": 2.5, "ram_gib": 6.0, "default": default}
    assert (changed["space"]["vram_gib"], changed["space"]["ram_gib"]) == (13.5, 58.0)  # 16 and 64 GB, less these
    assert changed["fits_params"] < system["fits_params"]  # the search's «поместится»
    assert hub.app.state.tuner._space()["vram_gib"] == 13.5  # tuning's room: the installer's
    assert hub.get("/v1/llama/models").json()["vram_reserve_gib"] == 2.5  # the window's estimate of a launch

    db, bus = hub.app.state.db, hub.app.state.bus
    started = mi.Installer(db, bus, None, None, llama_swap_url="", status=lambda: SPACE)  # the hub restarted
    assert started.space()["vram_gib"] == 15.0  # before it reads the database: the defaults
    hub.portal.call(started.load_reserve)
    assert (started.space()["vram_gib"], started.space()["ram_gib"]) == (13.5, 58.0)

    for wrong in ({"vram_gib": -1, "ram_gib": 3}, {"vram_gib": 1, "ram_gib": 100}, {"vram_gib": 9, "ram_gib": 3}):
        refused = hub.put("/v1/install/reserve", json=wrong)
        assert refused.status_code == 422 and "запас" in refused.json()["detail"]
    assert hub.get("/v1/install/system").json()["reserve"]["vram_gib"] == 2.5  # nothing changed by them
    back = hub.put("/v1/install/reserve", json=default).json()
    assert back["space"]["vram_gib"] == 15.0


def test_what_this_computer_is_stays_in_a_file_across_restarts(hub):
    """35.2 (2026-11-08, the user's word: «сделать документ для приложение чтобы каждый раз не провести замеры»): the
    graphics card and the memory go into computer.json in the data folder, which an update leaves alone - only when
    they are different ones, not with what changes by the minute. A hub just started, before its first sample, judges
    by it: in its first seconds it said there was no card and no memory."""
    installer, file = hub.app.state.installer, data_paths().computer
    assert not file.exists()
    installer.note_machine(SPACE)
    saved = json.loads(file.read_text(encoding="utf-8"))
    assert (saved["gpu"], saved["vram_total_gib"], saved["ram_total_gib"]) == ("RTX", 16.0, 64.0) and saved["at"]
    written = file.read_bytes()
    busy = {"gpu": {**SPACE["gpu"], "vram_used_mib": 9000}, "memory": {**SPACE["memory"], "ram_used_gib": 50.0}}
    file.write_bytes(written + b" ")  # a write would take this space away
    installer.note_machine(busy)
    assert file.read_bytes() == written + b" "

    hub.app.state.status.latest = None  # no sample yet
    space = hub.get("/v1/install/system").json()["space"]
    assert (space["gpu"], space["vram_total_gib"], space["vram_gib"], space["ram_gib"]) == ("RTX", 16.0, 15.0, 61.0)
    started = mi.Installer(None, None, None, None, llama_swap_url="", status=lambda: None, computer=file)  # restarted
    assert started.space()["gpu"] == "RTX" and started.space()["ram_total_gib"] == 64.0

    other = {**SPACE, "gpu": {**SPACE["gpu"], "name": "RTX 6090", "vram_total_mib": 32768}}
    installer.note_machine(other)  # another card: the file says so
    assert json.loads(file.read_text(encoding="utf-8"))["gpu"] == "RTX 6090"
    installer.note_machine({"gpu": None, "memory": None})  # nothing sampled: nothing forgotten
    assert json.loads(file.read_text(encoding="utf-8"))["gpu"] == "RTX 6090"
    file.write_text("{not json", encoding="utf-8")  # only a memory: never a reason to fail
    broken = mi.Installer(None, None, None, None, llama_swap_url="", status=lambda: None, computer=file)
    assert broken.space()["gpu"] == "" and broken.space()["vram_total_gib"] == 0


def test_what_hugging_face_said_is_kept_for_the_next_start(hub, monkeypatch):
    """35.3 (D58): what Hugging Face said about the models offered lived in the hub's memory - after each update every
    model was asked again over the network (2-3 s, without a network «Каталог не загрузился»). Now it is in a file: the
    list comes at once and without a network, judged against the computer as it is now; an answer older than an hour is
    shown at once, and a newer one comes in the background for the next look."""
    installer, file = hub.app.state.installer, data_paths().hf_answers
    first = hub.get("/v1/install/catalog").json()["models"]
    assert first[0]["recommended"]["quant"] == "Q8_0" and file.exists()

    def restart():
        installer._repos.clear()
        installer._headers.clear()
        installer._load_answers()

    asked, real = [], hf.repository

    async def no_network(http, repo, token):
        asked.append(repo)
        raise hf.HubError("no network")

    def no_header(*_args):
        raise AssertionError("the header is kept: not read again")

    monkeypatch.setattr(hf, "repository", no_network)
    monkeypatch.setattr(hf, "header", no_header)
    restart()
    again = hub.get("/v1/install/catalog").json()["models"]
    assert again[0]["recommended"]["quant"] == "Q8_0" and "error" not in again[0] and asked == []

    hub.app.state.status.latest = {**SPACE, "gpu": {**SPACE["gpu"], "vram_total_mib": 512}}  # judged as it is now
    busy = hub.get("/v1/install/catalog").json()["models"][0]
    assert (busy.get("recommended") or {}).get("verdict") != "gpu" or busy["recommended"]["quant"] != "Q8_0"
    hub.app.state.status.latest = SPACE

    def aged():
        old = json.loads(file.read_text(encoding="utf-8"))
        for entry in old["repos"].values():
            entry["at"] -= 7200
        file.write_text(json.dumps(old), encoding="utf-8")
        restart()

    def until(condition):
        deadline = time.monotonic() + 10
        while not condition():
            assert time.monotonic() < deadline
            time.sleep(0.02)

    aged()
    shown = hub.get("/v1/install/catalog").json()["models"]  # no network, an old answer: still the list
    assert shown[0]["recommended"]["quant"] == "Q8_0" and "error" not in shown[0]
    until(lambda: asked == ["test/Tiny-GGUF"])

    async def counted(http, repo, token):
        asked.append(repo)
        return await real(http, repo, token)

    before = json.loads(file.read_text(encoding="utf-8"))["repos"]["test/Tiny-GGUF"]["at"]
    monkeypatch.setattr(hf, "repository", counted)
    asked.clear()
    assert hub.get("/v1/install/catalog").json()["models"][0]["recommended"]["quant"] == "Q8_0"
    until(lambda: json.loads(file.read_text(encoding="utf-8"))["repos"]["test/Tiny-GGUF"]["at"] > before + 3600)
    assert asked == ["test/Tiny-GGUF"]  # once: the next look has the new answer
    hub.get("/v1/install/catalog")
    assert asked == ["test/Tiny-GGUF"]

    file.write_text('{"repos": {"x": 1}, "headers": [2]}', encoding="utf-8")  # only a memory
    restart()
    assert installer._repos == {} and installer._headers == {}
    file.write_text("{not json", encoding="utf-8")
    restart()
    assert installer._repos == {}


def test_an_install_waits_for_tuning_and_a_chat_before_its_trial(hub):
    """34.4 (2026-11-08): «Подбор параметров» measured one model with llama-bench (past llama-swap) while an install's
    trial loaded the next into llama-swap - two models at once filled the memory. The install waits, its files down,
    while tuning measures - «ждёт пробного запуска» - and, before its trial, while a chat answers (its model would be
    swapped out in the middle of an answer)."""
    installer, tuner = hub.app.state.installer, hub.app.state.tuner
    chatting = [False]
    installer.chat_busy = lambda: chatting[0]
    tuner.job = {"status": "measuring", "model_id": "big"}  # what the server's wiring tells the installer
    asked = len(FakeLlamaSwap.asked)
    job = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False}).json()["job"]
    waiting = wait_for(hub, job["id"], "trialwait")
    assert waiting["percent"] == 100.0 and waiting["files"][0]["done"]  # its files are down
    time.sleep(0.4)
    assert wait_for(hub, job["id"], "trialwait") and len(FakeLlamaSwap.asked) == asked  # nothing loaded meanwhile
    assert "tiny-q4_k_m" not in lc.model_blocks(hub.config.read_text(encoding="utf-8"))
    assert tuner._installing()  # and tuning, for its part, waits while the installs have work
    chatting[0] = True
    tuner.job = {"status": "idle"}
    time.sleep(0.4)
    assert wait_for(hub, job["id"], "trialwait") and len(FakeLlamaSwap.asked) == asked
    chatting[0] = False
    assert wait_for(hub, job["id"], "done", "failed")["status"] == "done"
    assert FakeLlamaSwap.asked[-1]["model"] == "tiny-q4_k_m"


def test_the_installs_table_takes_the_new_states_and_keeps_its_rows(tmp_path, monkeypatch):
    """Migration 052 (34.4): SQLite keeps a CHECK as it was made - the table is made again with «trialwait» and
    «added», its rows kept."""
    import sqlite3
    import sys

    from hub.db import Database, available_migrations, migrate

    migrating = sys.modules["hub.db.migrate"]  # hub.db re-exports the function `migrate`, hiding the module
    every = available_migrations()
    monkeypatch.setattr(migrating, "available_migrations", lambda: [m for m in every if m[0] < 52])
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    row = ("INSERT INTO model_installs (id, repo, quant, files, folder, model_id, name, status, own_files) "
           "VALUES ('j1', '', 'Q4_K_M', '[]', 'D:/m', 'mine', 'Mine', 'done', 0)")  # fmt: skip
    db.call(lambda c: c.execute(row))
    added = "UPDATE model_installs SET status = 'added' WHERE id = 'j1'"
    with pytest.raises(sqlite3.IntegrityError):
        db.call(lambda c: c.execute(added))
    monkeypatch.setattr(migrating, "available_migrations", lambda: every)
    assert 52 in db.call(migrate)
    kept = db.call(lambda c: dict(c.execute("SELECT * FROM model_installs WHERE id = 'j1'").fetchone()))
    assert (kept["model_id"], kept["status"], kept["own_files"], kept["folder"]) == ("mine", "done", 0, "D:/m")
    for status in ("added", "trialwait"):
        db.call(lambda c, s=status: c.execute("UPDATE model_installs SET status = ? WHERE id = 'j1'", (s,)))
    db.close()


def test_a_cancelled_install_takes_its_list_of_pieces_too(hub, pieces):
    """32.2: a big file's list of pieces (`.parts`) stayed after «Отменить» - and with it the model's folder."""
    FakeHub.slow = 0.05  # a piece ~0.2 s
    folder = hub.tmp / "cancel" / "test" / "Tiny-GGUF"
    job = hub.post("/v1/install", json={"repo": "test/Tiny-GGUF", "quant": "Q4_K_M", "projector": False,
                                        "folder": str(hub.tmp / "cancel")}).json()["job"]  # fmt: skip
    deadline = time.monotonic() + 10.0
    while not (folder / "Tiny-Q4_K_M.gguf.parts").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    hub.post(f"/v1/install/jobs/{job['id']}/cancel")
    assert wait_for(hub, job["id"], "cancelled")["status"] == "cancelled"
    assert not folder.exists()


def test_a_gated_model_wants_the_token_and_the_hub_checks_it(hub):
    refused = hub.get("/v1/install/repo/test/Gated-GGUF")
    assert refused.status_code == 403 and "лицензию" in refused.json()["detail"]
    hub.app.state.secrets.set(hf.TOKEN, "hf_good")
    assert hub.get("/v1/install/repo/test/Gated-GGUF").status_code == 200
    assert hub.get("/v1/install/system").json()["token"]


def test_the_search_narrowed_by_what_for_size_and_order(hub):
    """32.5: the search on Hugging Face was by name only. Now: what for (Hugging Face's tags), how big (its parameter
    count - and «fits», as much as this computer holds), in what order, without the models whose refusals were taken
    out; with no words - the most popular."""

    def look(**params):
        return [m["repo"] for m in hub.get("/v1/install/search", params=params).json()["models"]]

    tiny, big, free = "test/Tiny-GGUF", "someone/Big-27B-MTP-GGUF", "someone/Tiny-abliterated-GGUF"
    assert look() == [tiny, big, free]  # no words: the most downloaded
    asked = FakeHub.searches[-1]
    assert asked["filter"] == ["gguf"] and "search" not in asked and "gguf" in asked["expand[]"]
    assert look(task="code") == [tiny] and FakeHub.searches[-1]["filter"] == ["gguf", "code"]
    assert look(task="vision") == [big] and FakeHub.searches[-1]["filter"] == ["gguf", "image-text-to-text"]
    assert look(task="chat", sort="likes") == [free, tiny, big]
    assert (FakeHub.searches[-1]["filter"], FakeHub.searches[-1]["sort"]) == (["gguf", "conversational"], ["likes"])
    assert look(sort="trending") == [big, free, tiny] and FakeHub.searches[-1]["sort"] == ["trendingScore"]
    assert look(sort="updated") == [free, big, tiny] and FakeHub.searches[-1]["sort"] == ["lastModified"]

    # Hugging Face read the 27B model's count from its projector: it comes under «up to 4 billion», the hub drops it
    assert look(size="small") == [tiny, free]
    assert FakeHub.searches[-1]["num_parameters"] == ["min:0,max:4000000000"]
    assert look(size="medium") == [] and FakeHub.searches[-1]["num_parameters"] == ["min:4000000000,max:15000000000"]
    assert look(size="huge") == [] and FakeHub.searches[-1]["num_parameters"] == ["min:40000000000"]
    fits = hub.get("/v1/install/system").json()["fits_params"]
    assert 22e9 < fits < 24e9  # 15 GB of video memory, a model of about Q4_K_M with a context of 8K
    assert look(size="fits") == [tiny, free] and FakeHub.searches[-1]["num_parameters"] == [f"min:0,max:{fits}"]
    assert mi.fits_params(mi.room({"memory": SPACE["memory"]})) > fits  # no graphics card: the 61 GB of RAM

    assert look(plain="true") == [tiny, big]  # no models with their refusals taken out
    assert look(q="tiny", plain="true", task="chat") == [tiny] and FakeHub.searches[-1]["search"] == ["tiny"]
    shown = hub.get("/v1/install/search", params={"q": "big"}).json()["models"][0]
    assert (shown["params"], shown["pipeline"]) == (27_000_000_000, "")  # by its name (32.4)
    assert hub.get("/v1/install/search", params={"task": "pictures"}).status_code == 422
    assert hub.get("/v1/install/search", params={"sort": "name"}).status_code == 422


def test_each_found_model_says_whether_it_fits_this_computer(hub):
    """41.1 (2026-11-12): beside each repository the search lists - «≈ 16,5 ГБ в Q4_K_M: часть в ОЗУ», by the
    count of weights Hugging Face read, with no more requests: a model taken as it usually is (FIT_BITS), judged as a
    variant is (verdict, the reserves of stage 38). A mixture of experts - by its architecture or by a name like
    «30B-A3B», which not every repository tags - loses little speed with its experts in RAM; a count nobody read - no
    estimate."""
    moe = {"id": "someone/Coder-30B-A3B-GGUF", "downloads": 1, "likes": 0, "trendingScore": 0,
           "lastModified": "2026-10-04T00:00:00.000Z", "tags": ["gguf"],
           "gguf": {"total": 30_532_122_624, "architecture": "qwen3moe"}}  # fmt: skip
    named = {**moe, "id": "someone/Mix-26B-A4B-GGUF", "gguf": {"total": 25_800_000_000, "architecture": "mix"}}
    unread = {**moe, "id": "someone/Unread-GGUF", "gguf": {}}
    SEARCH.extend([moe, named, unread])
    try:
        found = {m["repo"]: m for m in hub.get("/v1/install/search").json()["models"]}
    finally:
        del SEARCH[-3:]
    big, free = found["someone/Big-27B-MTP-GGUF"]["fit"], found["someone/Tiny-abliterated-GGUF"]["fit"]
    assert (big["gb"], big["quant"], big["verdict"]) == (16.5, "Q4_K_M", "split")  # 15 GB of video memory: no room
    assert big["words"] == "часть в ОЗУ — медленнее" and not found["someone/Big-27B-MTP-GGUF"]["moe"]
    assert (free["gb"], free["verdict"]) == (1.2, "gpu")
    assert found["someone/Coder-30B-A3B-GGUF"]["moe"] and found["someone/Coder-30B-A3B-GGUF"]["fit"]["verdict"] == (
        "split_moe")  # fmt: skip
    assert found["someone/Mix-26B-A4B-GGUF"]["moe"]  # by its name alone
    assert found["someone/Unread-GGUF"]["fit"] is None


def test_licences_in_words():
    """41.2 (2026-11-13, the user's word: the person decides how strict - in the filter itself): a free licence lets one
    work with the model with no conditions; a company's own (Llama, Gemma, Qwen) - for work too, on its terms; a
    research or non-commercial one - not for work; one the hub cannot tell - the model's page says."""
    kinds = {"apache-2.0": "free", "MIT": "free", "bsd-3-clause": "free", "cc-by-4.0": "free", "cc0-1.0": "free",
             "llama3.1": "terms", "gemma": "terms", "qwen": "terms", "creativeml-openrail-m": "terms",
             "cc-by-nc-4.0": "closed", "qwen-research": "closed", "flux-1-dev-non-commercial-license": "closed",
             "other": "", "": ""}  # fmt: skip
    assert {name: hf.license_kind(name) for name in kinds} == kinds


def test_the_search_by_build_language_licence_author_and_newest(hub):
    """41.2а: more filters - how the model is built (a mixture of experts or not: the hub tells, Hugging Face has no
    such filter), its language and its author (Hugging Face's), its licence - free, or for work on its own terms too
    (the hub's: Hugging Face takes one licence at a time), the newest first; and models that draw or film: «Установить»
    only for those AFO puts together itself (the main repository of a picture set), the others - their page."""

    def found(repo: str, downloads: int, day: int, tags: list[str], **gguf) -> dict:
        made = f"2026-11-1{day}T00:00:00.000Z"
        return {"id": repo, "downloads": downloads, "likes": 0, "trendingScore": 0, "createdAt": made,
                "lastModified": made, "tags": ["gguf", *tags], "gguf": gguf}  # fmt: skip

    extra = [
        found("unsloth/Coder-30B-A3B-GGUF", 4, 0, ["conversational", "ru", "license:apache-2.0"],
              total=30_532_122_624, architecture="qwen3moe"),
        found("bartowski/Llama-8B-GGUF", 3, 2, ["conversational", "en", "license:llama3.1"], total=8_000_000_000,
              architecture="llama"),
        found("someone/Research-7B-GGUF", 2, 1, ["conversational", "license:cc-by-nc-4.0"], total=7_000_000_000),
        found("leejet/FLUX.2-klein-4B-GGUF", 1, 3, ["text-to-image", "license:apache-2.0"]),
        found("someone/Other-Image-GGUF", 0, 4, ["text-to-image"], total=12_000_000_000),
        found("hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF", 0, 5, ["text-to-video"]),
    ]  # fmt: skip
    coder, llama, research, flux, other, wan = (m["id"] for m in extra)
    tiny, big = "test/Tiny-GGUF", "someone/Big-27B-MTP-GGUF"

    def look(**params):
        return [m["repo"] for m in hub.get("/v1/install/search", params=params).json()["models"]]

    shipped = json.loads(Path(mi.__file__).with_name("model_catalog.json").read_text(encoding="utf-8"))["pictures"]
    kept = json.loads(mi.CATALOG.read_text(encoding="utf-8"))  # the test's catalog, with AFO's own picture sets
    mi.CATALOG.write_text(json.dumps({**kept, "pictures": shipped}), encoding="utf-8")
    SEARCH.extend(extra)
    try:
        assert look(build="moe") == [coder]
        assert coder not in look(build="dense") and tiny in look(build="dense")
        assert look(language="ru") == [coder] and FakeHub.searches[-1]["filter"] == ["gguf", "ru"]
        assert look(task="chat", language="en") == [llama]
        assert look(license="free") == [tiny, coder, flux]  # free: no conditions at all
        assert look(license="work") == [tiny, coder, llama, flux]  # and the companies' own, on their terms
        kinds = {m["repo"]: m["license_kind"] for m in hub.get("/v1/install/search").json()["models"]}
        assert (kinds[tiny], kinds[llama], kinds[research], kinds[big]) == ("free", "terms", "closed", "")
        assert look(author="unsloth") == [coder] and FakeHub.searches[-1]["author"] == ["unsloth"]
        assert look(sort="new")[:3] == [wan, other, flux] and FakeHub.searches[-1]["sort"] == ["createdAt"]
        drawn = {m["repo"]: m for m in hub.get("/v1/install/search", params={"task": "image"}).json()["models"]}
        assert FakeHub.searches[-1]["filter"] == ["gguf", "text-to-image"] and list(drawn) == [flux, other]
        assert (drawn[flux]["picture"], drawn[other]["picture"]) == ("flux2-klein-4b", "")  # AFO puts it together
        assert drawn[other]["fit"] is None  # not llama.cpp's: no estimate of its memory
        filmed = hub.get("/v1/install/search", params={"task": "video"}).json()["models"]
        assert [(m["repo"], m["picture"]) for m in filmed] == [(wan, "wan2.2-ti2v-5b-turbo")]
        for wrong in ({"build": "big"}, {"language": "zz"}, {"license": "any"}, {"author": "evil"}):
            assert hub.get("/v1/install/search", params=wrong).status_code == 422, wrong
    finally:
        del SEARCH[-len(extra) :]
