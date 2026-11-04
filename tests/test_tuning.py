"""Launch settings by measurement (step 15.2, D49): llama-bench of the same llama.cpp build measures what a few
settings give on this computer - flash attention, the KV cache's type, ubatch, at a filled context - and the fastest
answer goes into llama-swap's config, checked by loading the model with its whole context (else the old config comes
back). Automatic tuning waits until no chat uses a model. No model is loaded here: llama-bench and llama-swap are
stand-ins."""

import asyncio
import json
import time

import pytest

from hub import llama_config as lc
from hub import tuning
from tests.test_llama_config import CONFIG, gguf
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def test_batch_ubatch_threads_and_moe_experts_are_settings():
    big = lc.model_view(CONFIG, "big")
    assert (big["batch"], big["ubatch"], big["threads"], big["n_cpu_moe"]) == (0, 0, 0, 0)  # llama.cpp's defaults
    new = lc.edit(CONFIG, "big", {"batch": 2048, "ubatch": 1024, "threads": 16, "n_cpu_moe": 10})
    view = lc.model_view(new, "big")
    assert (view["batch"], view["ubatch"], view["threads"], view["n_cpu_moe"]) == (2048, 1024, 16, 10)
    assert "-b 2048" in new and "-ub 1024" in new and "-t 16" in new and "-ncmoe 10" in new
    back = lc.edit(new, "big", {"batch": 0, "ubatch": 0, "threads": 0, "n_cpu_moe": 0})  # 0: llama.cpp's own again
    assert back == CONFIG
    with pytest.raises(lc.ConfigError):
        lc.edit(CONFIG, "big", {"ubatch": 3})


def line(fa, cache, ub, threads, pp=0.0, tg=0.0):
    return json.dumps({"flash_attn": fa, "type_k": cache, "type_v": cache, "n_ubatch": ub, "n_threads": threads,
                       "n_prompt": 512 if pp else 0, "n_gen": 128 if tg else 0, "n_depth": 4096,
                       "avg_ts": pp or tg})  # fmt: skip


def test_the_measurements_and_the_fastest():
    lines = [line(True, "q8_0", 512, 16, pp=900), line(True, "q8_0", 512, 16, tg=40),
             line(True, "q8_0", 2048, 16, pp=1400), line(True, "q8_0", 2048, 16, tg=40.5),
             line(False, "q8_0", 512, 16, pp=700), line(False, "q8_0", 512, 16, tg=35),
             "llama-bench: a line that is not a measurement"]  # fmt: skip
    measured = tuning.results(lines)
    assert len(measured) == 3 and measured[0] == {"flash_attn": "on", "cache_type": "q8_0", "ubatch": 512,
                                                  "threads": 16, "pp": 900.0, "tg": 40.0}  # fmt: skip
    # answers within 3 % are as fast: the faster prompt decides
    assert tuning.best(measured)["ubatch"] == 2048
    assert tuning.best([*measured, {"flash_attn": "on", "cache_type": "f16", "ubatch": 512, "threads": 16,
                                    "pp": 100.0, "tg": 50.0}])["cache_type"] == "f16"  # fmt: skip


def test_what_llama_bench_is_asked(tmp_path):
    bench = tmp_path / "llama-bench.exe"
    whole = tuning.command(bench, lc.model_view(CONFIG, "big"), "q8_0", threads=16, depth=4096)
    assert whole[:3] == [str(bench), "-m", "F:/models/big.gguf"] and "-ngl" in whole and "99" in whole
    asked = dict(zip(whole[3::2], whole[4::2], strict=False))
    assert (asked["-fa"], asked["-ctk"], asked["-ctv"], asked["-ub"], asked["-d"], asked["-o"]) == (
        "on,off", "q8_0", "q8_0", "512,1024,2048", "4096", "jsonl")  # fmt: skip
    fit = tuning.command(bench, lc.model_view(CONFIG, "huge"), "q8_0", threads=16, depth=0)
    assert "-fitt" in fit and fit[fit.index("-fitc") + 1] == "16384" and "-ngl" not in fit  # llama.cpp places it


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A config whose llama-server has llama-bench beside it, and a model file with a real header."""
    server = tmp_path / "llama.cpp" / "llama-server.exe"
    server.parent.mkdir()
    server.write_bytes(b"MZ")
    (server.parent / tuning.BENCH).write_bytes(b"MZ")
    keys = {
        "general.architecture": "tiny",
        "tiny.block_count": 8,
        "tiny.context_length": 65536,
        "tiny.embedding_length": 256,
        "tiny.attention.head_count": 4,
        "tiny.attention.head_count_kv": 2,
    }
    model = gguf(tmp_path / "big.gguf", keys)
    text = CONFIG.replace('"D:/llama.cpp/llama-server.exe"', f'"{server.as_posix()}"').replace(
        "F:/models/big.gguf", model.as_posix())  # fmt: skip
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    monkeypatch.setattr(lc, "reloaded", _reloaded)
    return config


async def _reloaded(url, ids, context=None, **_):
    return True, ""


class Bus:
    def __init__(self):
        self.said = []

    async def publish(self, kind, payload, persist=True):
        self.said.append((kind, payload))


class Chat:
    def __init__(self):
        self.busy: list[str] = []


def tuner(db_hub, *, measured, check_ok=True, asked=None):
    def run(command, progress):
        asked is not None and asked.append(command)
        cache = command[command.index("-ctk") + 1]
        out = []
        for ub, p, t in measured.get(cache, []):  # llama-bench: a line for the prompt, one for the answer
            out += [line(True, cache, ub, 16, pp=p), line(True, cache, ub, 16, tg=t)]
        for _ in out:
            progress()
        return out

    async def check(model_id):
        return {"ok": check_ok, "error": "" if check_ok else "out of memory", "seconds": 3.0}

    return tuning.Tuner(db_hub.app.state.db, Bus(), Chat(), url="http://127.0.0.1:9",
                        space=lambda: {"vram_gib": 24.0, "ram_gib": 64.0}, run=run, check=check)  # fmt: skip


def test_the_fastest_goes_into_the_config_and_is_checked(hub, setup):  # noqa: F811
    asked = []
    found = tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)], "f16": [(512, 800, 45)]}, asked=asked)
    result = asyncio.run(found.tune("big"))
    assert result["status"] == "better", result
    assert [c[c.index("-ctk") + 1] for c in asked] == ["q8_0", "f16"]  # f16 too: the model keeps its context with it
    view = lc.model_view(setup.read_text(encoding="utf-8"), "big")
    assert (view["flash_attn"], view["cache_type"], view["ubatch"], view["batch"], view["threads"]) == (
        "on", "q8_0", 2048, 2048, 16)  # fmt: skip
    assert result["before"]["tg"] == 40.0 and result["after"]["tg"] == 52.0  # the config's settings, then the new
    assert lc.backups(setup) and result["settings"]["ubatch"] == 2048
    assert hub.app.state.db.call(tuning.load_results)["big"]["status"] == "better"  # kept


def test_settings_that_do_not_load_give_the_old_config_back(hub, setup):  # noqa: F811
    before = setup.read_text(encoding="utf-8")
    found = tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)]}, check_ok=False)
    result = asyncio.run(found.tune("big"))
    assert result["status"] == "failed" and "out of memory" in result["error"]
    assert setup.read_text(encoding="utf-8") == before


def test_the_config_already_fastest_is_left_as_it_is(hub, setup):  # noqa: F811
    before = setup.read_text(encoding="utf-8")
    found = tuner(hub, measured={"q8_0": [(512, 900, 50), (2048, 1000, 40)]})
    result = asyncio.run(found.tune("big"))
    assert result["status"] == "same" and setup.read_text(encoding="utf-8") == before
    refused = asyncio.run(tuner(hub, measured={}).tune("fast"))  # Strata's: nothing of llama.cpp to tune
    assert refused["status"] == "failed" and "llama.cpp" in refused["error"]


def test_automatic_tuning_waits_until_no_chat_uses_a_model(hub, setup, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tuning, "IDLE_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    found = tuner(hub, measured={"q8_0": [(2048, 1500, 52)]})
    found._chat.busy = ["c1"]

    async def scenario():
        found.request("big", auto=True)
        await asyncio.sleep(0.3)
        assert found.job["status"] == "waiting" and "big" not in found.results
        found._chat.busy = []
        started = time.monotonic()
        while "big" not in found.results:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.05)
        assert time.monotonic() - started >= 0.25  # quiet for IDLE_S first

    asyncio.run(scenario())
    assert found.results["big"]["status"] == "better"


def test_the_window_asks_and_reads(hub, setup, monkeypatch):  # noqa: F811
    state = hub.get("/v1/tune").json()
    assert state["auto"] is True and state["job"]["status"] == "idle" and state["results"] == {}
    assert hub.put("/v1/tune", json={"auto": False}).json()["auto"] is False
    calls = []
    monkeypatch.setattr(hub.app.state.tuner, "request", lambda model_id, auto=False: calls.append((model_id, auto)))
    hub.post("/v1/tune/big")
    hub.post("/v1/tune/all")
    assert calls[0] == ("big", False) and {c[0] for c in calls[1:]} == {"big", "huge"}  # llama.cpp's only
    assert hub.post("/v1/tune/nope").status_code == 404
