"""A local model's launch settings in llama-swap's config.yaml (stage 8.5, D39, Q25): the text changed only where it
must, a model file's header, the memory estimate, and the API - a copy first, llama-swap reading the file again, the
copy back when it does not, or when the model does not start. A stand-in llama-swap reads the same file."""

import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from common.gguf import GGUFError, model_info, read_header
from common.llama_memory import estimate
from common.paths import data_paths
from hub import llama_config as lc
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings

CONFIG = """# llama-swap: one address for every local model.
healthCheckTimeout: 900
startPort: 10001

macros:
  llama_server: '"D:/llama.cpp/llama-server.exe"'
  strata_py: "C:/Strata/python.exe"

models:
  "fast":
    name: "Fast (Strata)"
    cmd: |
      ${strata_py} strata.py serve
      --profile C:/Strata/launch/fast.json
      --host 127.0.0.1 --port ${PORT}
    # unloaded after 15 minutes without requests
    ttl: 900
    metadata:
      context: 65536

  "big":
    name: "Big model"
    cmd: |
      ${llama_server}
      -m "F:/models/big.gguf"
      --mmproj "F:/models/mmproj.gguf" --no-mmproj-offload
      -ngl 99 -fa on -c 32768 -ctk q8_0 -ctv q8_0 -np 1
      --jinja --temp 1.0
      --host 127.0.0.1 --port ${PORT}
    checkEndpoint: /health
    metadata:
      engine: llama.cpp
      context: 32768   # what the window shows

  "huge":
    name: "Huge, half in RAM"
    cmd: |
      ${llama_server} -m "F:/models/huge.gguf"
      --fit on -fa on -c 16384 --no-mmap
      --host 127.0.0.1 --port ${PORT}
"""


def test_the_settings_as_read_and_changed_only_where_they_must():
    big = lc.model_view(CONFIG, "big")
    assert (big["engine"], big["editable"], big["context"], big["gpu_layers"], big["cache_type"], big["parallel"],
            big["flash_attn"], big["ttl"]) == ("llama.cpp", True, 32768, "all", "q8_0", 1, "on", 0)  # fmt: skip
    assert big["file"] == "F:/models/big.gguf" and big["mmproj"] == "F:/models/mmproj.gguf" and not big["mmproj_on_gpu"]
    assert lc.model_view(CONFIG, "huge")["gpu_layers"] == "auto"
    fast = lc.model_view(CONFIG, "fast")
    assert (fast["engine"], fast["editable"], fast["ttl"]) == ("strata", False, 900)
    assert fast["profile"] == "C:/Strata/launch/fast.json"

    new = lc.edit(CONFIG, "big", {"context": 49152, "cache_type": "q4_0", "gpu_layers": 40, "parallel": 2,
                                  "flash_attn": "auto", "ttl": 600})  # fmt: skip
    changed = [(a, b) for a, b in zip(CONFIG.splitlines(), new.splitlines(), strict=False) if a != b]
    assert "      -ngl 40 -fa auto -c 49152 -ctk q4_0 -ctv q4_0 -np 2" in new.splitlines()
    assert "      context: 49152   # what the window shows" in new.splitlines()  # its comment stays
    assert '    ttl: 600\n    cmd: |\n      ${llama_server}' in new and len(new.splitlines()) == len(
        CONFIG.splitlines()) + 1 and changed  # fmt: skip
    rest = new.split('  "big":')[0]
    assert rest == CONFIG.split('  "big":')[0]  # the other models, macros and comments as they were
    assert lc.model_view(new, "big")["context"] == 49152

    huge = lc.edit(CONFIG, "huge", {"gpu_layers": "all"})
    assert "      -fa on -c 16384 --no-mmap -ngl 99" in huge.splitlines() and "--fit" not in huge.split('"huge"')[1]
    back = lc.edit(huge, "huge", {"gpu_layers": "auto"})
    assert lc.model_view(back, "huge")["gpu_layers"] == "auto" and "-ngl" not in back.split('"huge"')[1]
    no_timer = lc.edit(CONFIG, "fast", {"ttl": 0})  # never unloaded by a timer: the line goes
    assert "ttl:" not in no_timer.split('"fast"')[1].split('"big"')[0]
    for model, changes, said in (("fast", {"context": 1000}, "Strata"), ("big", {"cache_type": "q3"}, "KV"),
                                 ("big", {"context": 100}, "от 512"), ("big", {"gpu_layers": "most"}, "auto, all"),
                                 ("big", {"seed": 1}, "не меняется"), ("nope", {}, "нет модели")):  # fmt: skip
        with pytest.raises(lc.ConfigError, match=said):
            lc.edit(CONFIG, model, changes)


def gguf(path, keys):
    """A GGUF file's header with these keys (strings, ints, a long token list), and no weights."""

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data

    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(keys) + 1)
    for key, value in keys.items():
        out += string(key)
        if isinstance(value, str):
            out += struct.pack("<I", 8) + string(value)
        else:
            out += struct.pack("<II", 4, value)
    out += string("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + b"".join(string(t) for t in "abc")
    path.write_bytes(out + b"\0" * 1000)
    return path


def test_a_model_files_header_and_the_memory_it_takes(tmp_path):
    big = gguf(tmp_path / "big.gguf", {"general.architecture": "qwen35", "qwen35.block_count": 64,
                                       "qwen35.attention.head_count": 24, "qwen35.attention.head_count_kv": 4,
                                       "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256,
                                       "qwen35.embedding_length": 5120, "qwen35.full_attention_interval": 4,
                                       "qwen35.context_length": 262144})  # fmt: skip
    info = model_info(big)
    assert (info["layers"], info["kv_layers"], info["kv_heads"], info["key_length"], info["context_trained"]) == (
        64, 16, 4, 256, 262144)  # hybrid: a KV cache in every 4th layer only  # fmt: skip
    assert "tokenizer.ggml.tokens" not in read_header(big) and read_header(big)["general.architecture"] == "qwen35"
    (tmp_path / "not.gguf").write_bytes(b"PK\3\4")
    with pytest.raises(GGUFError, match="not a GGUF"):
        model_info(tmp_path / "not.gguf")

    info = {**info, "size_bytes": 12 * 1024**3}
    whole = estimate(info, context=32768, cache_type="q8_0", gpu_layers="all", mmproj_bytes=600 * 1024**2,
                     mmproj_on_gpu=False, vram_total_mib=16 * 1024)  # fmt: skip
    assert whole["verdict"] == "fits" and whole["kv_gb"] == 1.06 and whole["ram_gb"] == 0.6  # the projector in RAM
    assert estimate(info, context=262144, cache_type="f16", vram_total_mib=16 * 1024)["verdict"] == "over"
    assert estimate(info, context=32768, gpu_layers=30, vram_total_mib=16 * 1024)["verdict"] == "split"
    auto = estimate({**info, "size_bytes": 28 * 1024**3}, context=32768, gpu_layers="auto", vram_total_mib=16 * 1024)
    assert auto["verdict"] == "auto" and auto["vram_gb"] < 16 and auto["ram_gb"] > 10
    assert "из 16" in whole["text"] and "целиком" in whole["text"]


class FakeLlamaSwap(BaseHTTPRequestHandler):
    """llama-swap as the hub sees it: the models of the config file as it is now (it rereads the file, like
    --watch-config), unless `frozen`; a chat completion that answers, or fails when the model does not start."""

    config = None
    frozen = None
    fail = False
    asked: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def _send(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        text = self.frozen or self.config.read_text(encoding="utf-8")
        data = []
        for model_id in lc.model_blocks(text):
            view = lc.model_view(text, model_id)
            data.append({"id": model_id, "name": view["name"],
                         "meta": {"llamaswap": {"context": view.get("context") or 65536}}})  # fmt: skip
        self._send(200, {"data": data})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeLlamaSwap.asked.append(body)
        if self.fail:
            self._send(502, {"error": "upstream command exited prematurely"})
            return
        chunks = [
            {"choices": [{"delta": {"content": "на связи"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 3}},
        ]
        self._send(200, ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode(),
                   "text/event-stream")  # fmt: skip


@pytest.fixture
def hub(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    gguf(tmp_path / "big.gguf", {"general.architecture": "x", "x.block_count": 8})
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    FakeLlamaSwap.config, FakeLlamaSwap.frozen, FakeLlamaSwap.fail, FakeLlamaSwap.asked = config, None, False, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlamaSwap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr("hub.api.llama.RELOAD_S", 2.0)
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url=f"http://127.0.0.1:{server.server_port}",
                                 status_interval_s=0, process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.portal.call(app.state.gateway.sync_catalog)
        client.config = config
        yield client
    server.shutdown()


def test_the_api_writes_with_a_copy_and_checks_llama_swap_read_it(hub):
    listed = hub.get("/v1/llama/models").json()
    assert [m["id"] for m in listed["models"]] == ["fast", "big", "huge"] and listed["backups"] == []
    assert listed["models"][1]["file_error"]  # big.gguf is not where the config says: the estimate waits
    done = hub.put("/v1/llama/models/big", json={"changes": {"context": 49152, "ttl": 300}}).json()
    assert done["changed"] and done["verified"] and done["backup"].startswith("config.yaml.bak-")
    assert (hub.config.parent / done["backup"]).read_text(encoding="utf-8") == CONFIG  # the file as it was
    assert lc.model_view(hub.config.read_text(encoding="utf-8"), "big")["context"] == 49152
    model = hub.get("/v1/models").json()["models"]
    assert next(m for m in model if m["id"] == "llama-swap/big")["capabilities"]["context"] == 49152  # re-synced
    assert hub.put("/v1/llama/models/fast", json={"changes": {"context": 1}}).status_code == 422

    FakeLlamaSwap.frozen = hub.config.read_text(encoding="utf-8")  # llama-swap stops reading the file
    before = hub.config.read_text(encoding="utf-8")
    refused = hub.put("/v1/llama/models/big", json={"changes": {"context": 65536}})
    assert refused.status_code == 502 and "конфиг возвращён" in refused.json()["detail"]
    assert hub.config.read_text(encoding="utf-8") == before  # the copy came back
    FakeLlamaSwap.frozen = None

    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.get("/v1/llama/models", headers={"Authorization": f"Bearer {other}"}).status_code == 403


def test_a_model_that_does_not_start_gets_its_old_settings_back(hub):
    assert hub.post("/v1/llama/models/big/trial").json()["ok"]  # the settings as they are: it answers
    asked = FakeLlamaSwap.asked[-1]
    assert asked["model"] == "big" and asked["messages"][-1]["content"].startswith("Ответь одним")
    hub.put("/v1/llama/models/big", json={"changes": {"gpu_layers": 120}})
    FakeLlamaSwap.fail = True
    trial = hub.post("/v1/llama/models/big/trial").json()
    assert not trial["ok"] and trial["restored"].startswith("config.yaml.bak-")
    assert hub.config.read_text(encoding="utf-8") == CONFIG  # back as before the change
    FakeLlamaSwap.fail = False
    copies = hub.get("/v1/llama/models").json()["backups"]
    back = hub.post("/v1/llama/restore", json={"backup": copies[0]}).json()
    assert back["verified"] and hub.config.read_text(encoding="utf-8") == CONFIG
    assert hub.post("/v1/llama/restore", json={"backup": "../hub.db"}).status_code == 404
