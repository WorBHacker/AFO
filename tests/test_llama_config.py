"""A local model's launch settings in llama-swap's config.yaml (stage 8.5, D39, Q25): the text changed only where it
must, a model file's header, the memory estimate, and the API - a copy first, llama-swap reading the file again, the
copy back when it does not, or when the model does not start. A stand-in llama-swap reads the same file."""

import asyncio
import base64
import contextlib
import json
import random
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from app.ui import model_setup as ms
from common.gguf import GGUFError, model_info, read_header
from common.llama_memory import estimate, moe_load
from common.paths import data_paths
from hub import llama_config as lc
from hub import model_install as mi
from hub.api.llama import _files as lc_files
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_visual import png

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


def gguf(path, keys, tensors=()):
    """A GGUF file's header with these keys (strings, ints, a long token list) and the table of these tensors (name,
    dimensions, ggml's type), and no weights."""

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data

    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(keys) + 1)
    for key, value in keys.items():
        out += string(key)
        if isinstance(value, str):
            out += struct.pack("<I", 8) + string(value)
        else:
            out += struct.pack("<II", 4, value)
    out += string("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + b"".join(string(t) for t in "abc")
    for name, dims, kind in tensors:
        out += string(name) + struct.pack(f"<I{len(dims)}QIQ", len(dims), *dims, kind, 0)
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


def test_the_draft_model_takes_its_share_of_the_memory(tmp_path):
    """42.2: a draft model for guessing ahead sits in the video memory beside the model - its weights, and a KV cache
    of its own for the model's whole context (b11899 has no context of the draft's own): Qwen3-0.6B's in f16 for 32
    thousand tokens is 3,5 GB, more than its weights. The estimate counts it, with the draft's cache type."""
    main = {"architecture": "qwen3", "layers": 36, "kv_layers": 36, "kv_heads": 8, "key_length": 128,
            "value_length": 128, "context_trained": 40960, "size_bytes": int(4.7 * 1024**3)}  # fmt: skip
    small = {**main, "layers": 28, "kv_layers": 28, "size_bytes": int(0.6 * 1024**3)}
    card = 12 * 1024
    alone = estimate(main, context=32768, cache_type="q8_0", vram_total_mib=card)
    assert alone["verdict"] == "fits" and "draft_gb" not in alone
    f16 = estimate(main, context=32768, cache_type="q8_0", vram_total_mib=card,
                   draft={"info": small, "gpu_layers": "auto", "cache_type": "f16"})  # fmt: skip
    q8 = estimate(main, context=32768, cache_type="q8_0", vram_total_mib=card,
                  draft={"info": small, "gpu_layers": "all", "cache_type": "q8_0"})  # fmt: skip
    assert f16["verdict"] == "over" and f16["draft_gb"] == 4.4  # 0,6 + 3,5 of its KV + its buffers
    assert q8["verdict"] == "fits" and q8["draft_gb"] == 2.8 and abs(q8["vram_gb"] - alone["vram_gb"] - 2.8) < 0.11
    assert "черновая модель 2.8 ГБ" in q8["text"]
    in_ram = estimate(main, context=32768, cache_type="q8_0", vram_total_mib=card,
                      draft={"info": small, "gpu_layers": 0, "cache_type": "q8_0"})  # fmt: skip
    assert in_ram["verdict"] == "split" and in_ram["vram_gb"] == alone["vram_gb"] and in_ram["ram_gb"] == 2.5
    placed = estimate(main, context=32768, cache_type="q8_0", gpu_layers="auto", vram_total_mib=card,
                      draft={"info": small, "gpu_layers": "auto", "cache_type": "f16"})  # fmt: skip
    assert placed["verdict"] == "auto" and placed["vram_gb"] == 11.0 and placed["ram_gb"] == 1.3  # the model's layers

    # the hub reads the draft's header beside the model's; the window and the words at loading count it
    fields = {"general.architecture": "qwen3", "qwen3.attention.head_count": 32, "qwen3.attention.head_count_kv": 8,
              "qwen3.attention.key_length": 128, "qwen3.attention.value_length": 128}  # fmt: skip
    gguf(tmp_path / "big.gguf", {**fields, "qwen3.block_count": 36})
    gguf(tmp_path / "draft.gguf", {**fields, "qwen3.block_count": 28})
    (tmp_path / "mmproj.gguf").write_bytes(b"\0" * 100)
    plain = CONFIG.replace("F:/models/", tmp_path.as_posix() + "/")
    draft_flags = f'--spec-type draft-simple -md "{tmp_path.as_posix()}/draft.gguf" -ctkd q8_0'
    text = plain.replace("-np 1\n", f"-np 1 {draft_flags}\n")
    view = {**lc.model_view(text, "big"), **lc_files(lc.model_view(text, "big"))}
    assert view["draft_info"]["layers"] == 28 and view["draft_info"]["size_bytes"] > 0
    form = ms.launch_draft(view)
    assert "черновая модель" in ms.launch_estimate(form, view, card)
    with_draft = mi._estimated(text, "big", card)
    assert with_draft["draft_gb"] > 0 and with_draft["vram_gb"] > mi._estimated(plain, "big", card)["vram_gb"]
    missing = text.replace("draft.gguf", "gone.gguf")  # its file away: the model's own estimate still
    assert "draft_gb" not in mi._estimated(missing, "big", card)
    assert lc_files(lc.model_view(missing, "big"))["draft_error"]


F32, Q4_K, Q6_K = 0, 12, 14  # ggml's types
MOE = {"general.architecture": "qwen3moe", "qwen3moe.block_count": 48, "qwen3moe.attention.head_count": 32,
       "qwen3moe.attention.head_count_kv": 4, "qwen3moe.attention.key_length": 128,
       "qwen3moe.attention.value_length": 128, "qwen3moe.embedding_length": 2048, "qwen3moe.context_length": 40960,
       "qwen3moe.expert_count": 128, "qwen3moe.expert_used_count": 8,
       "qwen3moe.expert_feed_forward_length": 768}  # fmt: skip
NOT_EXPERTS = 1_003_206_656  # what moe_tensors() has besides the experts, in bytes


def moe_tensors(layers=range(48), *, head=True, down=None):
    """Qwen3-30B-A3B's tensors as its Q4_K_M has them, near enough: in each layer the attention, the router, and its
    128 experts (gate, up, down) - most of the weights."""
    out = [("token_embd.weight", (2048, 151936), Q4_K), ("output_norm.weight", (2048,), F32),
           ("output.weight", (2048, 151936), Q6_K)] if head else []  # fmt: skip
    for i in layers:
        out += [(f"blk.{i}.attn_q.weight", (2048, 4096), Q4_K), (f"blk.{i}.attn_k.weight", (2048, 512), Q4_K),
                (f"blk.{i}.attn_v.weight", (2048, 512), Q6_K), (f"blk.{i}.attn_output.weight", (4096, 2048), Q4_K),
                (f"blk.{i}.ffn_gate_inp.weight", (2048, 128), F32),
                (f"blk.{i}.ffn_gate_exps.weight", (2048, 768, 128), Q4_K),
                (f"blk.{i}.ffn_up_exps.weight", (2048, 768, 128), Q4_K),
                (f"blk.{i}.ffn_down_exps.weight", (768, 2048, 128), down or (Q6_K if i % 2 else Q4_K))]  # fmt: skip
    return out


def test_moe_experts_in_ram_and_their_cache_on_the_card(tmp_path):
    """44.1: a Mixture-of-Experts model's experts are most of its weights, and each word uses few of them: llama.cpp
    keeps them in RAM (--cpu-moe, or those of the first N layers: --n-cpu-moe) and the rest on the graphics card -
    and a cache of the experts used most there too (--moe-cache-mib). The header's table of tensors says what the
    experts weigh in each layer; the estimate counts them where the launch keeps them: a model twice the card fits."""
    info = model_info(gguf(tmp_path / "moe.gguf", MOE, moe_tensors()))
    assert (info["experts"], info["experts_used"], info["mtp_layers"]) == (128, 8, 0)
    assert len(info["expert_bytes"]) == 48 and info["expert_bytes"][:2] == [339_738_624, 391_643_136]  # Q4_K, Q6_K
    experts = sum(info["expert_bytes"])
    header = info["size_bytes"] - experts - NOT_EXPERTS  # 44.2: what its table says it weighs - the file has none
    assert 0 < header < 64 * 1024 < (tmp_path / "moe.gguf").stat().st_size + 64 * 1024 < info["size_bytes"]
    card = 8 * 1024
    assert info["size_bytes"] > 2 * card * 1024**2

    def on(moe, **kw):
        return estimate(info, context=16384, cache_type="q8_0", vram_total_mib=card, moe=moe, **kw)

    assert on(None)["verdict"] == "over"
    out = on({"all": True, "layers": 0, "cache_mib": 0})
    assert (out["verdict"], out["vram_gb"], out["ram_gb"], out["experts_gb"]) == ("fits", 2.5, 16.3, 16.3)
    assert "эксперты MoE 16.3 ГБ в ОЗУ" in out["text"] and "целиком" not in out["text"]
    assert on({"all": True, "layers": 0, "cache_mib": 0}, gpu_layers="auto")["verdict"] == "fits"
    by_layers = {n: on({"all": False, "layers": n, "cache_mib": 0})["verdict"] for n in (30, 36, 99)}
    assert by_layers == {30: "over", 36: "fits", 99: "fits"}  # those of the first 36 layers: the rest fits
    cached = on({"all": True, "layers": 0, "cache_mib": 4096})
    assert (cached["verdict"], cached["vram_gb"]) == ("fits", 6.5) and "кэш экспертов 4.0 ГБ" in cached["text"]
    assert on({"all": True, "layers": 0, "cache_mib": 6144})["verdict"] == "over"

    # a dense model has no experts; a type ggml's table here does not know - its share of the file's bytes
    assert "expert_bytes" not in model_info(gguf(tmp_path / "dense.gguf", {**MOE, "qwen3moe.expert_count": 0}))
    odd = model_info(gguf(tmp_path / "odd.gguf", MOE, moe_tensors(down=99)))
    assert len(odd["expert_bytes"]) == 48 and odd["expert_bytes"][0] >= 2 * 113_246_208

    # a model split into files (`-00001-of-00002.gguf`): its size and its experts are those of every part
    gguf(tmp_path / "moe-00001-of-00002.gguf", {**MOE, "split.count": 2}, moe_tensors(range(24)))
    gguf(tmp_path / "moe-00002-of-00002.gguf", {"split.no": 1, "split.count": 2},
         moe_tensors(range(24, 48), head=False))  # fmt: skip
    split = model_info(tmp_path / "moe-00001-of-00002.gguf")
    assert split["expert_bytes"] == model_info(tmp_path / "moe.gguf")["expert_bytes"]
    assert 0 < split["size_bytes"] - model_info(tmp_path / "moe.gguf")["size_bytes"] < 64 * 1024  # a header more
    dense = {**MOE, "qwen3moe.expert_count": 0, "split.count": 2}
    gguf(tmp_path / "dense-00001-of-00002.gguf", dense)
    gguf(tmp_path / "dense-00002-of-00002.gguf", {"split.no": 1, "split.count": 2})
    assert model_info(tmp_path / "dense-00001-of-00002.gguf")["size_bytes"] == sum(
        p.stat().st_size for p in tmp_path.glob("dense-0000*-of-00002.gguf"))  # fmt: skip

    # the launch's flags: the hub, the installer and the window count the experts where the command keeps them
    gguf(tmp_path / "big.gguf", MOE, moe_tensors())
    (tmp_path / "mmproj.gguf").write_bytes(b"\0" * 100)
    text = CONFIG.replace("F:/models/", tmp_path.as_posix() + "/").replace(
        "-ngl 99 -fa on", "-ngl 99 --cpu-moe --moe-cache-mib 512 -fa on")  # fmt: skip
    view = {**lc.model_view(text, "big"), **lc_files(lc.model_view(text, "big"))}
    assert moe_load(view) == {"all": True, "layers": 0, "cache_mib": 512}
    assert moe_load(lc.model_view(CONFIG, "big")) is None
    assert "experts_gb" in mi._estimated(text, "big", card)
    assert "experts_gb" not in mi._estimated(text.replace("--cpu-moe ", ""), "big", card)
    assert "эксперты MoE" in ms.launch_estimate(ms.launch_draft(view), view, card)


def test_the_experts_flags_in_the_config():
    """44.1: --cpu-moe has no value - written once, read, and taken off alone: not with the word after it, as a flag
    with a value is. The experts' cache only for a build that has --moe-cache-mib."""
    build = lc.parse_help(HELP)
    new = lc.edit(CONFIG, "big", {"cpu_moe": True, "moe_cache_mib": 4096}, build=build)
    assert "      -ngl 99 -fa on -c 32768 -ctk q8_0 -ctv q8_0 -np 1 --cpu-moe --moe-cache-mib 4096" in new.splitlines()
    view = lc.model_view(new, "big")
    assert (view["cpu_moe"], view["n_cpu_moe"], view["moe_cache_mib"], view["flash_attn"]) == (True, 0, 4096, "on")
    assert lc.edit(new, "big", {"cpu_moe": True}, build=build) == new  # once
    assert lc.edit(new, "big", {"cpu_moe": False, "moe_cache_mib": 0}) == CONFIG  # off: needs no build
    first = CONFIG.replace("-ngl 99 -fa on", "-ngl 99 --cpu-moe -fa on")
    assert lc.model_view(first, "big")["flash_attn"] == "on"
    assert lc.edit(first, "big", {"cpu_moe": False}) == CONFIG  # «-fa on» after it stays
    by_layers = lc.edit(CONFIG, "big", {"n_cpu_moe": 12})
    assert lc.model_view(by_layers, "big")["n_cpu_moe"] == 12 and not lc.model_view(by_layers, "big")["cpu_moe"]
    older = lc.parse_help(HELP.replace(MOE_CACHE_HELP, ""))
    for changes, how, said in (({"cpu_moe": 1}, build, "да или нет"), ({"moe_cache_mib": -1}, build, "от 0"),
                               ({"moe_cache_mib": 4096}, None, "кэша экспертов"),
                               ({"moe_cache_mib": 4096}, older, "кэша экспертов")):  # fmt: skip
        with pytest.raises(lc.ConfigError, match=said):
            lc.edit(CONFIG, "big", changes, build=how)
    # a build without the cache: llama-swap would not start the model with it - its flag goes, the rest stays
    with_cache = CONFIG.replace("-ngl 99 -fa on", "-ngl 99 --cpu-moe --moe-cache-mib 4096 -ncmoe 4 -fa on")
    assert lc.match_build(with_cache, build) == with_cache
    assert lc.match_build(with_cache, older) == CONFIG.replace("-ngl 99 -fa on", "-ngl 99 --cpu-moe -ncmoe 4 -fa on")


_noise = random.Random(7)
PICTURE = png(32, 32, lambda x, y: (_noise.randrange(256), _noise.randrange(256), _noise.randrange(256)))
GREY = png(32, 32, lambda x, y: (128, 128, 128))


class FakeLlamaSwap(BaseHTTPRequestHandler):
    """llama-swap as the hub sees it: the models of the config file as it is now (it rereads the file, like
    --watch-config), unless `frozen`; a chat completion that answers, or fails when the model does not start."""

    config = None
    frozen = None
    fail = False
    blank: ClassVar = False  # a picture model answers «success» with a flat grey picture (29.3): all, or those named
    asked: ClassVar[list] = []
    video: ClassVar[list] = []  # a video model's trial (29.8): vid_gen, then its job
    banner: ClassVar[list] = []  # the pieces of llama-swap's «loading model» lines before an answer (38.2)

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
        if "/sdcpp/v1/jobs/" in self.path:
            from tests.test_visual import WEBP

            self._send(200, {"id": "job_v", "status": "completed", "result": {
                "b64_json": base64.b64encode(WEBP).decode(), "frame_count": 9, "fps": 24}})  # fmt: skip
            return
        text = self.frozen or self.config.read_text(encoding="utf-8")
        data = []
        for model_id in lc.model_blocks(text):
            view = lc.model_view(text, model_id)
            meta = {"context": view.get("context") or 65536}
            if view["engine"] == lc.SD_ENGINE:  # what the config's metadata says, as llama-swap passes it on
                meta = {"engine": lc.SD_ENGINE}
            data.append({"id": model_id, "name": view["name"], "meta": {"llamaswap": meta}})
        self._send(200, {"data": data})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path.endswith("/sdcpp/v1/vid_gen"):
            FakeLlamaSwap.video.append({**body, "path": self.path})
            self._send(202, {"id": "job_v", "status": "queued"})
            return
        FakeLlamaSwap.asked.append(body)
        if self.fail:
            self._send(502, {"error": "upstream command exited prematurely"})
            return
        if self.path == "/v1/images/generations":  # a picture model's trial (29.3)
            blank = self.blank is True or (isinstance(self.blank, set) and body.get("model") in self.blank)
            picture = GREY if blank else PICTURE
            self._send(200, {"data": [{"b64_json": base64.b64encode(picture).decode()}]})
            return
        chunks = [
            *({"choices": [{"delta": {"reasoning_content": piece}}]} for piece in self.banner),
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
    FakeLlamaSwap.blank, FakeLlamaSwap.video = False, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlamaSwap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr("hub.llama_config.RELOAD_S", 2.0)
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


def test_a_trial_past_the_video_memory_says_so(hub, tmp_path):
    """Past the video memory a model still starts on Windows - the NVIDIA driver lends it shared memory - and answers
    several times slower: live (stage 28) an 8B at 262144 tokens answered «на связи» at 16 tok/s instead of 83, and the
    trial said only that. Now it says what the window's estimate says, too."""
    model = gguf(tmp_path / "eight.gguf", {"general.architecture": "x", "x.block_count": 8, "x.attention.head_count": 8,
                                          "x.attention.head_count_kv": 8, "x.attention.key_length": 128,
                                          "x.attention.value_length": 128, "x.embedding_length": 1024,
                                          "x.context_length": 262144})  # fmt: skip
    hub.config.write_text(CONFIG.replace("F:/models/big.gguf", model.as_posix()), encoding="utf-8")
    hub.app.state.status.latest = {"gpu": {"vram_total_mib": 4096}}
    fits = hub.post("/v1/llama/models/big/trial").json()  # 32768 tokens: its KV cache ≈ 0.5 GB
    assert fits["ok"] and "over" not in fits
    hub.put("/v1/llama/models/big", json={"changes": {"context": 196608}})  # ≈ 3.2 GB, with the buffers past 4 GB
    over = hub.post("/v1/llama/models/big/trial").json()
    assert over["ok"] and "не поместится в видеопамять" in over["over"] and "restored" not in over  # it does answer
    hub.app.state.status.latest = None  # no video card known: nothing to say
    assert "over" not in hub.post("/v1/llama/models/big/trial").json()


def test_a_one_line_command_is_read_not_edited(tmp_path, monkeypatch):
    """2026-10-28: llama-swap takes `cmd: <one line>` too; such a model shows in the window (not editable, said why)
    and a block the hub cannot read at all no longer makes the whole list of launch settings fail."""
    short = '  "short":\n    name: "Short"\n    cmd: ${llama_server} -m "F:/models/s.gguf" -c 4096 --port ${PORT}\n'
    text = CONFIG + "\n" + short
    view = lc.model_view(text, "short")
    assert view["engine"] == "llama.cpp" and view["editable"] is False and "одну строку" in view["problem"]
    assert '"D:/llama.cpp/llama-server.exe" -m "F:/models/s.gguf" -c 4096' in view["command"]
    assert lc.model_view(text, "big")["editable"] is True  # the others as before
    broken = text + '\n  "odd":\n    name: "Odd"\n    proxy: http://127.0.0.1:9999\n'
    config = tmp_path / "config.yaml"
    config.write_text(broken, encoding="utf-8")
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        listed = {m["id"]: m for m in client.get("/v1/llama/models").json()["models"]}
    assert list(listed) == ["fast", "big", "huge", "short", "odd"]
    assert listed["big"]["editable"] and not listed["short"]["editable"]
    assert listed["odd"]["editable"] is False and "cmd" in listed["odd"]["problem"]
    odd_first = (
        'models:\n  "odd":\n    proxy: http://127.0.0.1:9999\n\n'
        '  "m":\n    cmd: |\n      "D:/llama.cpp/llama-server.exe" -m "F:/m.gguf" --port ${PORT}\n'
    )
    assert lc.server_command(odd_first) == '"D:/llama.cpp/llama-server.exe"'  # no macro: a model's, past "odd"


ADDED = '  "added":\n    cmd: |\n      ${llama_server} -m "F:/models/added.gguf"\n      --host 127.0.0.1 --port ${PORT}'


def test_one_models_lines_put_back_and_what_others_changed_stays():
    """34.4 (2026-11-08, the user's config): «Подбор параметров» took its change back by copying the whole file as it
    was before its write - and an install's model, added meanwhile, was gone with it. Now only its model's lines go
    back; a model that is not in the config any more stays out."""
    changed = lc.edit(CONFIG, "big", {"ubatch": 1024, "threads": 16})
    assert lc.with_block(changed, "big", CONFIG) == CONFIG  # nothing else changed: the file as it was
    added = lc.add_model(changed, "added", ADDED)
    back = lc.with_block(added, "big", CONFIG)
    assert back == lc.add_model(CONFIG, "added", ADDED)  # its own lines as they were, the new model kept
    gone = lc.remove_model(changed, "big")
    assert lc.with_block(gone, "big", CONFIG) == gone


def test_the_config_is_changed_by_one_writer_at_a_time():
    """34.4: an install and «Подбор параметров» each read the file, changed it and wrote it - the second over the
    first. One lock in the hub (per event loop: tests run several) - the second waits until the first is done."""
    order = []

    async def writer(name, hold):
        async with lc.writing():
            order.append(f"{name} in")
            await asyncio.sleep(hold)
            order.append(f"{name} out")

    async def both():
        assert lc.writing() is lc.writing()
        await asyncio.gather(writer("install", 0.1), writer("tuning", 0))

    asyncio.run(both())
    assert order == ["install in", "install out", "tuning in", "tuning out"]
    asyncio.run(both())  # another loop: a lock of its own


def test_a_write_waits_out_a_reader_of_the_file(tmp_path, monkeypatch):
    """2026-11-12 (found by a test that failed once): Windows refuses os.replace while another program has the file
    open for reading - llama-swap rereads the config right after the hub writes it, and an install failed with
    «[WinError 5] Отказано в доступе». The write and the copy back wait the reader out; one that never lets go
    leaves the file as it was and no part beside it."""
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def held(seconds):
        opened = threading.Event()

        def reader():
            with path.open(encoding="utf-8") as file:
                file.read()
                opened.set()
                time.sleep(seconds)

        thread = threading.Thread(target=reader)
        thread.start()
        assert opened.wait(5)
        return thread

    new = lc.edit(CONFIG, "big", {"context": 49152})
    reading = held(0.4)
    backup = lc.write(path, new)
    reading.join()
    written = path.read_text(encoding="utf-8")
    assert written == new and lc.model_view(written, "big")["context"] == 49152 and list(lc.model_blocks(written)) == [
        "fast", "big", "huge"]  # fmt: skip
    assert backup.read_text(encoding="utf-8") == CONFIG
    reading = held(0.4)
    lc.restore(path, backup)
    reading.join()
    assert path.read_text(encoding="utf-8") == CONFIG

    monkeypatch.setattr(lc, "REPLACE_WAITS_S", (0.01, 0.01))
    reading = held(1.0)
    with pytest.raises(PermissionError):
        lc.write(path, new)
    reading.join()
    assert path.read_text(encoding="utf-8") == CONFIG and not path.with_name("config.yaml.part").exists()


# llama-server --help of b11899 (2026-11-13) - its own lines, those of the options a launch with speculative decoding
# takes, out of 750
KINDS = ("none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-simple,ngram-map-k,"
         "ngram-map-k4v,ngram-mod,ngram-cache")  # fmt: skip
MOE_CACHE_HELP = """\
--moe-cache-mib N                       GPU cache size in MiB for the MoE experts kept in the CPU. with
                                        multiple GPUs, it is split among them like the layers (--tensor-split)
                                        (default: 0, disabled)
                                        (env: LLAMA_ARG_MOE_CACHE_MIB)
"""
HELP = f"""----- common params -----

-h,    --help, --usage                  print usage and exit
-c,    --ctx-size N                     size of the prompt context (default: 0, 0 = loaded from model)
                                        (env: LLAMA_ARG_CTX_SIZE)
-fa,   --flash-attn [on|off|auto]       set Flash Attention use ('on', 'off', or 'auto', default: 'auto')
                                        (env: LLAMA_ARG_FLASH_ATTN)
-cmoe, --cpu-moe                        keep all Mixture of Experts (MoE) weights in the CPU
                                        (env: LLAMA_ARG_CPU_MOE)
-ncmoe, --n-cpu-moe N                   keep the Mixture of Experts (MoE) weights of the first N layers in the
                                        CPU
                                        (env: LLAMA_ARG_N_CPU_MOE)
{MOE_CACHE_HELP}-ngl,  --gpu-layers, --n-gpu-layers N   max. number of layers to store in VRAM, either an exact number,
                                        'auto', or 'all' (default: auto)
                                        (env: LLAMA_ARG_N_GPU_LAYERS)
-m,    --model FNAME                    model path to load
                                        (env: LLAMA_ARG_MODEL)
--spec-draft-type-k, -ctkd, --cache-type-k-draft TYPE
                                        KV cache data type for K for the draft model
                                        allowed values: f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1
                                        (default: f16)
--spec-draft-type-v, -ctvd, --cache-type-v-draft TYPE
                                        KV cache data type for V for the draft model
                                        allowed values: f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1
                                        (default: f16)

----- speculative params -----

--spec-draft-n-max N                    number of tokens to draft for speculative decoding (default: 3)
                                        (env: LLAMA_ARG_SPEC_DRAFT_N_MAX)
--spec-draft-n-min N                    minimum number of draft tokens to use for speculative decoding
                                        (default: 0)
                                        (env: LLAMA_ARG_SPEC_DRAFT_N_MIN)
--spec-draft-ngl, -ngld, --gpu-layers-draft, --n-gpu-layers-draft N
                                        max. number of draft model layers to store in VRAM, either an exact
                                        number, 'auto', or 'all' (default: auto)
                                        (env: LLAMA_ARG_N_GPU_LAYERS_DRAFT)
--spec-draft-model, -md, --model-draft FNAME
                                        draft model for speculative decoding (default: unused)
                                        (env: LLAMA_ARG_SPEC_DRAFT_MODEL)
--spec-type {KINDS}
                                        comma-separated list of types of speculative decoding to use (default:
                                        none)

--draft, --draft-n, --draft-max N       the argument has been removed. use --spec-draft-n-max or
                                        --spec-ngram-mod-n-max
                                        (env: LLAMA_ARG_DRAFT_MAX)
--draft-min, --draft-n-min N            the argument has been removed. use --spec-draft-n-min or
                                        --spec-ngram-mod-n-min
                                        (env: LLAMA_ARG_DRAFT_MIN)
--spec-ngram-size-n N                   the argument has been removed. use the respective
                                        --spec-ngram-*-size-n or --spec-ngram-mod-n-match

----- example-specific params -----
"""


def help_server(folder):
    """A llama-server that only says its --help - a real process (a .cmd: Windows starts it as a program); each run
    leaves a line in runs.txt."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "help.py").write_text("import pathlib, sys\n"
                                    "with open(pathlib.Path(__file__).with_name('runs.txt'), 'a') as f:\n"
                                    "    f.write('run\\n')\n"
                                    f"sys.stdout.write({HELP!r})\n", encoding="utf-8")  # fmt: skip
    server = folder / "llama-server.cmd"
    server.write_text(f'@"{sys.executable}" "%~dp0help.py" %*\n', encoding="utf-8")
    return server


def test_the_speculative_flags_are_the_builds_own(tmp_path):
    """42.1: guessing ahead (speculative decoding) is told by flags whose names change between llama.cpp's builds -
    b11899 took away --draft-max, and without `--spec-type` a draft model is loaded and not used. The hub reads them
    from the --help of the build in the config, once per file, and writes each as that build spells it."""
    server = help_server(tmp_path / "b11899")
    build = lc.server_flags(server)
    assert build["spec_types"][:2] == ("none", "draft-simple") and "ngram-mod" in build["spec_types"]
    assert {"--draft", "--draft-max", "--draft-min"} <= build["removed"]
    assert "--spec-draft-n-max" not in build["removed"]
    assert {"-md", "--model-draft", "-ngld", "-ctkd"} <= build["known"]
    assert lc.server_flags(server) is build and (tmp_path / "b11899" / "runs.txt").read_text().count("run") == 1
    assert lc.server_flags(tmp_path / "nowhere" / "llama-server.exe") is None
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "llama-server.exe").write_bytes(b"MZ")  # not a program that runs
    assert lc.server_flags(tmp_path / "broken" / "llama-server.exe") is None

    assert lc.model_view(CONFIG, "big")["spec"] == {"type": "", "draft": "", "draft_gpu_layers": "auto",
                                                    "draft_max": 0, "draft_cache_type": ""}  # fmt: skip
    draft = {"spec_type": "draft-simple", "draft_model": "F:/models/small draft.gguf", "draft_gpu_layers": "all",
             "draft_max": 8, "draft_cache_type": "q8_0"}  # fmt: skip
    new = lc.edit(CONFIG, "big", draft, build=build)
    assert ('      -ngl 99 -fa on -c 32768 -ctk q8_0 -ctv q8_0 -np 1 --spec-type draft-simple'
            ' -md "F:/models/small draft.gguf" -ngld 99 --spec-draft-n-max 8 -ctkd q8_0 -ctvd q8_0'
            in new.splitlines())  # fmt: skip
    assert lc.model_view(new, "big")["spec"] == {"type": "draft-simple", "draft": "F:/models/small draft.gguf",
                                                 "draft_gpu_layers": "all", "draft_max": 8,
                                                 "draft_cache_type": "q8_0"}  # fmt: skip
    assert new.split('  "big":')[0] == CONFIG.split('  "big":')[0]  # the other models as they were
    ngram = lc.model_view(lc.edit(new, "big", {"spec_type": "ngram-mod"}, build=build), "big")["spec"]
    assert ngram == {"type": "ngram-mod", "draft": "", "draft_gpu_layers": "auto", "draft_max": 0,
                     "draft_cache_type": ""}  # fmt: skip
    assert lc.edit(new, "big", {"spec_type": "none"}, build=build) == CONFIG  # off: each of its flags away
    for changes, how, said in ((draft, None, "не умеет"), ({**draft, "spec_type": "draft-magic"}, build, "none или"),
                               ({"spec_type": "draft-simple"}, build, "файл черновой"),
                               ({**draft, "draft_max": 0}, build, "от 1 до 64"),
                               ({**draft, "draft_cache_type": "q3"}, build, "KV")):  # fmt: skip
        with pytest.raises(lc.ConfigError, match=said):
            lc.edit(CONFIG, "big", changes, build=how)


def test_a_new_llama_cpp_gets_the_flags_it_takes_while_llama_swap_reads_the_file(tmp_path):
    """42.1: after the wizard updates llama.cpp, a flag the new build took away would stop llama-swap from starting
    the model. Under the lock the config's commands are written as the build spells them - the old name under its
    new one, the kinds of guessing it does not know and the flags it says were removed taken off - while llama-swap
    rereads the file the whole time (--watch-config): it sees the old file or the new one, never a part."""
    old = CONFIG.replace("      --jinja --temp 1.0\n",
                         "      --spec-type draft-simple,ngram-lookup --model-draft F:/models/draft.gguf\n"
                         "      --draft-max 8 --spec-ngram-size-n 4\n"
                         "      --draft-min 2 --jinja --temp 1.0\n")  # fmt: skip
    path = tmp_path / "config.yaml"
    path.write_text(old, encoding="utf-8")
    new = lc.match_build(old, lc.parse_help(HELP))
    seen, stop = [], threading.Event()

    def llama_swap():
        while not stop.is_set():
            with contextlib.suppress(OSError):  # the moment the file is replaced
                seen.append(path.read_text(encoding="utf-8"))
            time.sleep(0.002)

    reader = threading.Thread(target=llama_swap)
    reader.start()
    try:
        time.sleep(0.05)
        lc.write(path, new)
        deadline = time.monotonic() + 5
        while new not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        reader.join()
    assert set(seen) == {old, new}
    lines = new.split('  "big":')[1].split('  "huge":')[0].splitlines()
    assert "      --spec-type draft-simple --model-draft F:/models/draft.gguf" in lines  # a name the build takes stays
    # the old names under the new ones; a flag it took away that has none - off
    assert "      --spec-draft-n-max 8" in lines and "      --spec-draft-n-min 2 --jinja --temp 1.0" in lines
    assert lc.model_view(new, "big")["spec"] == {"type": "draft-simple", "draft": "F:/models/draft.gguf",
                                                 "draft_gpu_layers": "auto", "draft_max": 8,
                                                 "draft_cache_type": ""}  # fmt: skip

    def outside(text):
        head, rest = text.split('  "big":')
        return head + rest.split('  "huge":')[1]

    assert outside(new) == outside(old)  # the other models as they were
    assert lc.match_build(new, lc.parse_help(HELP)) == new  # the second time: nothing to change
    # a build that knows no guessing ahead: each of its flags off, lines left empty away, the rest as it was
    bare = lc.match_build(old, lc.parse_help(HELP.split("----- speculative params -----")[0]))
    assert lc.model_view(bare, "big")["spec"]["type"] == "" and "draft" not in bare.split('  "big":')[1]
    assert lc.model_view(bare, "big")["command"] == lc.model_view(CONFIG, "big")["command"]
    assert outside(bare) == outside(old)


def test_a_model_under_a_new_id_and_name():
    """36.1 (2026-11-08): a model added under its file's name gets its own: the block's key, its name and the name in
    its description; every other line as it was."""
    block = ('  "sha256-aa":\n    name: "Source"\n    description: "Source: файл на этом компьютере, добавлен AFO."\n'
             '    cmd: |\n      ${llama_server} -m "C:/b/sha256-aa"\n'
             '      --host 127.0.0.1 --port ${PORT}')  # fmt: skip
    text = lc.add_model(CONFIG, "sha256-aa", block)
    new = lc.rename_model(text, "sha256-aa", "gemma4-12b", "gemma4:12b")
    assert new == text.replace('"sha256-aa":', '"gemma4-12b":').replace('name: "Source"', 'name: "gemma4:12b"').replace(
        '"Source: файл', '"gemma4:12b: файл')  # fmt: skip
    assert lc.model_view(new, "gemma4-12b")["file"] == "C:/b/sha256-aa"
    with pytest.raises(lc.ConfigError):
        lc.rename_model(text, "nope", "x", "x")
    with pytest.raises(lc.ConfigError):
        lc.rename_model(text, "sha256-aa", "big", "x")  # taken
    grouped = text + 'groups:\n  main:\n    members:\n      - "sha256-aa"\n'
    with pytest.raises(lc.ConfigError):
        lc.rename_model(grouped, "sha256-aa", "x", "x")  # llama-swap would refuse a group with a member gone
