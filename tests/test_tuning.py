"""Launch settings by measurement (step 15.2, D49): llama-bench of the same llama.cpp build measures what a few
settings give on this computer - flash attention, the KV cache's type, ubatch, at a filled context - and the fastest
answer goes into llama-swap's config, checked by loading the model with its whole context (else the old config comes
back). Automatic tuning waits until no chat uses a model. No model is loaded here: llama-bench and llama-swap are
stand-ins."""

import asyncio
import contextlib
import json
import socket
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from common.llama_memory import estimate
from hub import llama_config as lc
from hub import model_settings, tuning
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
    # a quantized V cache: flash attention on only - without it llama.cpp makes no context, and llama-bench stops at
    # the first setting it cannot make (live on 2026-11-04: one setting of six measured)
    assert (asked["-fa"], asked["-ctk"], asked["-ctv"], asked["-ub"], asked["-d"], asked["-o"]) == (
        "on", "q8_0", "q8_0", "512,1024,2048", "4096", "jsonl")  # fmt: skip
    f16 = tuning.command(bench, lc.model_view(CONFIG, "big"), "f16", threads=16, depth=4096)
    assert f16[f16.index("-fa") + 1] == "on,off"
    fit = tuning.command(bench, lc.model_view(CONFIG, "huge"), "q8_0", threads=16, depth=0)
    assert "-fitt" in fit and fit[fit.index("-fitc") + 1] == "16384" and "-ngl" not in fit  # llama.cpp places it


def test_llama_bench_stopping_halfway_is_logged(tmp_path, caplog):
    """llama-bench quits at the first setting it cannot make: what it measured before counts, and why it stopped is
    in the hub's log; with nothing measured, the reason goes to the window."""
    bench = tmp_path / "bench.py"
    measured = [line(True, "q8_0", 512, 16, pp=900), line(True, "q8_0", 512, 16, tg=40)]
    bench.write_text(f"import sys\nfor text in {measured!r}:\n    print(text)\nsys.stdout.flush()\n"
                     "print('main: error: failed to create context with model', file=sys.stderr)\nsys.exit(1)\n",
                     encoding="utf-8")  # fmt: skip
    found = tuning.Tuner(None, Bus(), Chat(), url="http://127.0.0.1:9", space=dict)
    ticks = []
    with caplog.at_level("WARNING", logger="hub.tuning"):
        lines = found._bench([sys.executable, str(bench)], lambda: ticks.append(1))
    assert len(lines) == 2 and len(ticks) == 2
    assert "failed to create context" in caplog.text and "2" in caplog.text
    bench.write_text(
        "import sys\nprint('main: error: unknown model', file=sys.stderr)\nsys.exit(1)\n", encoding="utf-8"
    )
    with pytest.raises(tuning.TuneError, match="unknown model"):
        found._bench([sys.executable, str(bench)], lambda: None)


def test_the_check_asks_again_while_llama_swap_rereads_its_file(monkeypatch):
    """llama-swap answers «group is shutting down» while it rereads the file (live on 2026-11-04: the check came in
    the middle of it, and good settings were taken back); any other error is the answer."""
    answers = [(500, "unspecific error: group is shutting down")] * 2 + [(200, "")]
    asked = []

    def answer(request):
        asked.append(json.loads(request.content)["model"])
        code, error = answers[min(len(asked), len(answers)) - 1]
        body = {"choices": [{"message": {"content": "Hi"}}]} if code == 200 else {"error": {"message": error}}
        return httpx.Response(code, json=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(tuning.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    monkeypatch.setattr(tuning, "RETRY_S", 0.01)
    found = tuning.Tuner(None, Bus(), Chat(), url="http://127.0.0.1:9", space=dict)
    assert asyncio.run(found._load("big"))["ok"] and asked == ["big"] * 3
    answers[:] = [(500, "upstream command exited prematurely")]
    asked.clear()
    checked = asyncio.run(found._load("big"))
    assert not checked["ok"] and "exited prematurely" in checked["error"] and len(asked) == 1


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
    monkeypatch.setattr(tuning, "WATCH_S", 0.0)
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


def tuner(db_hub, *, measured, check_ok=True, asked=None, draft=None, refuse_draft=False, space=None):
    """A Tuner with llama-bench and the check as stand-ins: `measured` - llama-bench's numbers by KV cache type;
    `draft` - the draft model's file found for any model (42.4); `refuse_draft` - the check fails only for a model
    whose command has a draft model (llama-swap: «speculative decoding not supported by this context»); `space` - the
    memory a model may take (a 24 GB card)."""

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
        ok = check_ok
        if refuse_draft:
            ok = not lc.model_view(lc.config_path().read_text(encoding="utf-8"), model_id)["spec"]["draft"]
        return {"ok": ok, "error": "" if ok else "out of memory", "seconds": 3.0}

    async def find(model_id, file):
        return [draft] if isinstance(draft, Path) else list(draft or [])

    return tuning.Tuner(db_hub.app.state.db, Bus(), Chat(), url="http://127.0.0.1:9",
                        space=lambda: space or {"vram_gib": 24.0, "ram_gib": 64.0}, run=run, check=check,
                        drafts=find)  # fmt: skip


def test_the_fastest_goes_into_the_config_and_is_checked(hub, setup):  # noqa: F811
    asked = []
    found = tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)], "f16": [(512, 800, 45)]}, asked=asked)
    result = asyncio.run(found.tune("big"))
    assert result["status"] == "better", result
    assert [c[c.index("-ctk") + 1] for c in asked] == ["q8_0", "f16"]  # f16 too: the model keeps its context with it
    totals = {p["job"].get("total") for _, p in found._bus.said if p["job"].get("total")}
    assert totals == {(1 + 2) * 3 * 2}  # q8_0: flash attention on; f16: on and off - each ubatch, prompt and answer
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
    # within the measurement's noise is not faster (live on 2026-11-04: ubatch 1024 «won» by 0.5 % of the prompt)
    noise = asyncio.run(tuner(hub, measured={"q8_0": [(512, 2290, 59.3), (1024, 2301, 59.2)]}).tune("big"))
    assert noise["status"] == "same" and noise["after"]["ubatch"] == 512, noise
    assert setup.read_text(encoding="utf-8") == before
    prompt = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 50), (2048, 1500, 49.5)]}).tune("big"))
    assert prompt["status"] == "better" and prompt["settings"]["ubatch"] == 2048  # as fast an answer, a faster prompt
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


def test_automatic_tuning_waits_while_the_graphics_card_works(hub, setup, monkeypatch):  # noqa: F811
    """Live (stage 28): after llama.cpp's update, tuning measured while the hub's «Проверить» and other programs
    used the card - they answered at 11 tok/s instead of 89, and the numbers it wrote the config by were theirs too.
    Not only chats keep it waiting: the card itself must have been quiet for IDLE_S."""
    monkeypatch.setattr(tuning, "IDLE_S", 0.3)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    found = tuner(hub, measured={"q8_0": [(2048, 1500, 52)]})
    card = {"util_pct": 90}
    found._gpu = lambda: card

    async def scenario():
        found.request("big", auto=True)
        await asyncio.sleep(0.6)
        assert found.job["status"] == "waiting" and "big" not in found.results  # no chat, but the card works
        card["util_pct"] = 3
        started = time.monotonic()
        while "big" not in found.results:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.05)
        assert time.monotonic() - started >= 0.25

    asyncio.run(scenario())
    assert found.results["big"]["status"] == "better"


def test_a_chat_stops_automatic_measuring_and_it_is_measured_again(hub, setup, monkeypatch):  # noqa: F811
    """A chat that starts while automatic tuning measures: the measuring stops (llama-bench let go), nothing is
    written by its numbers, and the model waits its turn again - measured once all is quiet."""
    monkeypatch.setattr(tuning, "IDLE_S", 0.2)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    before = setup.read_text(encoding="utf-8")
    runs = []

    def run(command, progress):
        runs.append(command)
        if len(runs) == 1:  # the first: a long one, until it is stopped
            deadline = time.monotonic() + 10
            while not found._halt.is_set():
                assert time.monotonic() < deadline, "the measuring was not stopped"
                time.sleep(0.02)
            return [line(True, "q8_0", 2048, 16, pp=1500)]  # what it measured before: half a pair
        return [line(True, "q8_0", 2048, 16, pp=1500), line(True, "q8_0", 2048, 16, tg=52)]

    found = tuner(hub, measured={})
    found._run = run

    async def scenario():
        found.request("big", auto=True)
        started = time.monotonic()
        while not runs:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.02)
        found._chat.busy = ["c1"]  # a chat starts while it measures
        while found.job.get("status") != "waiting":
            assert time.monotonic() - started < 10, found.job
            await asyncio.sleep(0.02)
        assert setup.read_text(encoding="utf-8") == before and "big" not in found.results  # nothing by its numbers
        found._chat.busy = []
        while "big" not in found.results:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert found.results["big"]["status"] == "better" and len(runs) >= 2


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


def test_what_an_install_added_while_it_measured_stays(hub, setup):  # noqa: F811
    """34.4 (2026-11-08, the user's config): tuning read the config before measuring and wrote its change over that
    text minutes later - three models an install had added meanwhile were gone from it (gemma4:12b, deepseek-r1:8b,
    qwen3.5:2b-bf16). It writes the file as it is when the measuring ends; a check that fails takes back only its own
    model's lines."""
    from tests.test_llama_config import ADDED

    def add(model_id):
        text = setup.read_text(encoding="utf-8")
        setup.write_text(lc.add_model(text, model_id, ADDED.replace("added", model_id)), encoding="utf-8")

    found = tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)]})
    measure = found._run
    found._run = lambda command, progress: (add("during") if "-ctk" in command and not found.job.get("done")
                                            else None, measure(command, progress))[1]  # fmt: skip
    assert asyncio.run(found.tune("big"))["status"] == "better"
    text = setup.read_text(encoding="utf-8")
    assert "during" in lc.model_blocks(text) and lc.model_view(text, "big")["ubatch"] == 2048

    before = setup.read_text(encoding="utf-8")  # ubatch 2048 now: 512 measures faster this time
    found = tuner(hub, measured={"q8_0": [(512, 1500, 60), (2048, 900, 40)]}, check_ok=False)
    check = found._check

    async def late(model_id):  # an install writes while the new settings are being checked
        add("late")
        return await check(model_id)

    found._check = late
    assert asyncio.run(found.tune("big"))["status"] == "failed"
    assert setup.read_text(encoding="utf-8") == lc.add_model(before, "late", ADDED.replace("added", "late"))


def test_tuning_waits_while_installs_have_work_and_gives_way_to_one(hub, setup, monkeypatch):  # noqa: F811
    """34.4 (2026-11-08): llama-bench runs past llama-swap - it measured one model while an install's trial loaded the
    next one into llama-swap, and the memory filled up. Tuning, by hand too, waits while the installs have work;
    automatic measuring gives way to an install that begins, as it does to a chat, and is measured again later."""
    monkeypatch.setattr(tuning, "IDLE_S", 0.2)
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    installing = [True]
    found = tuner(hub, measured={"q8_0": [(2048, 1500, 52)]})
    found._installing = lambda: installing[0]

    async def by_hand():
        found.request("big")
        await asyncio.sleep(0.4)
        assert found.job["status"] == "waiting" and "big" not in found.results
        installing[0] = False
        started = time.monotonic()
        while "big" not in found.results:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.05)

    asyncio.run(by_hand())
    assert found.results["big"]["status"] == "better"

    runs = []

    def run(command, progress):
        runs.append(command)
        if len(runs) == 1:  # the first: until an install begins and it is stopped
            installing[0] = True
            deadline = time.monotonic() + 10
            while not found._halt.is_set():
                assert time.monotonic() < deadline, "the measuring did not give way"
                time.sleep(0.02)
            return []
        return [line(True, "q8_0", 512, 16, pp=1500), line(True, "q8_0", 512, 16, tg=60),  # 2048 is set by now
                line(True, "q8_0", 2048, 16, pp=900), line(True, "q8_0", 2048, 16, tg=40)]  # fmt: skip

    found = tuner(hub, measured={})
    found._installing, found._run = (lambda: installing[0]), run
    installing[0] = False

    async def automatic():
        found.request("big", auto=True)
        started = time.monotonic()
        while found.job.get("status") != "waiting" or not runs:
            assert time.monotonic() - started < 10, found.job
            await asyncio.sleep(0.02)
        assert "big" not in found.results  # nothing by its numbers
        installing[0] = False
        while "big" not in found.results:
            assert time.monotonic() - started < 10
            await asyncio.sleep(0.05)

    asyncio.run(automatic())
    assert found.results["big"]["status"] == "better" and len(runs) >= 2


def test_a_renamed_model_keeps_what_was_measured(hub, setup):  # noqa: F811
    """36.1: tuning's results are by a model's id, in memory and in the database."""
    found = tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)]})
    assert asyncio.run(found.tune("big"))["status"] == "better"

    async def renamed():
        await found.rename("big", "bigger")
        await found.rename("nothing", "else")  # nothing measured: nothing to move

    asyncio.run(renamed())
    assert list(found.results) == ["bigger"] and list(hub.app.state.db.call(tuning.load_results)) == ["bigger"]


# --- guessing ahead, measured by llama-server itself (42.4) ---

SERVER = r'''"""A stand-in llama-server (42.4): says its --help; else serves /health (503 while «loading») and
answers with llama.cpp's timings - its speed by --spec-type, and by -ngl when it is not all the layers; by where the
experts are (44.2: «now», «layersN», «all», «cache») when speeds.json says. Each launch and each request are written
down beside it."""
import json, pathlib, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

here = pathlib.Path(__file__).parent
args = sys.argv[1:]
if "--help" in args:
    sys.stdout.write((here / "help.txt").read_text(encoding="utf-8"))
    sys.exit(0)
with open(here / "launches.txt", "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
how = json.loads((here / "speeds.json").read_text(encoding="utf-8"))


def flag(name, default=None):
    return args[args.index(name) + 1] if name in args else default


kind = flag("--spec-type", "none")
if kind in how.get("crash", []):
    print("common_speculative_init: speculative decoding not supported by this context", flush=True)
    sys.exit(1)
speed = how["fewer"] if flag("-ngl", "99") != "99" else how["speeds"][kind]
reads = 1000.0
experts = ("cache" if flag("--moe-cache-mib") else "all") if "--cpu-moe" in args else \
    "layers" + flag("-ncmoe") if "-ncmoe" in args else "now"
if experts in how.get("moe", {}):
    speed, reads = how["moe"][experts]
rejected = kind in how.get("rejects", [])  # as llama.cpp with a draft of another vocabulary: on without guessing
if rejected:
    print("0.05.726.435 E spec common_specu: the target and draft vocabs are not compatible", flush=True)
started = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        ready = time.monotonic() - started > how.get("load_s", 0.3)
        self._send(200 if ready else 503, {"status": "ok"} if ready else {"error": {"message": "Loading model"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        with open(here / "asked.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(body) + "\n")
        drafted = 0 if kind == "none" or rejected else 100
        self._send(200, {"choices": [{"message": {"content": "ok"}}],
                         "timings": {"predicted_per_second": speed, "prompt_per_second": reads, "draft_n": drafted,
                                     "draft_n_accepted": int(drafted * how.get("accepted", 0.7))}})


HTTPServer(("127.0.0.1", int(flag("--port"))), Handler).serve_forever()
'''


@pytest.fixture
def spec_server(setup, tmp_path):
    """The config's llama-server is a stand-in that runs (a .cmd: Windows starts it as a program) with b11899's
    --help; a draft model's file beside the model's."""
    from tests.test_llama_config import HELP

    folder = tmp_path / "llama.cpp"
    (folder / "server.py").write_text(SERVER, encoding="utf-8")
    (folder / "help.txt").write_text(HELP, encoding="utf-8")
    speeds(folder, {"none": 40.0, "ngram-mod": 44.0, "draft-simple": 60.0}, fewer=50.0)
    server = folder / "llama-server.cmd"
    server.write_text(f'@"{sys.executable}" "%~dp0server.py" %*\n', encoding="utf-8")
    text = setup.read_text(encoding="utf-8").replace(
        f'"{(folder / "llama-server.exe").as_posix()}"', f'"{server.as_posix()}"')  # fmt: skip
    setup.write_text(text, encoding="utf-8")
    keys = {"general.architecture": "tiny", "tiny.block_count": 4, "tiny.context_length": 65536,
            "tiny.embedding_length": 128, "tiny.attention.head_count": 2,
            "tiny.attention.head_count_kv": 1}  # fmt: skip
    gguf(tmp_path / "draft.gguf", keys)
    return folder


def speeds(folder, by_kind, *, fewer=0.0, crash=(), rejects=(), load_s=0.3, moe=None):
    """How fast the stand-in llama-server answers: by --spec-type; with fewer layers on the card; which kinds of
    guessing it cannot start with; which it starts with but guesses nothing (llama.cpp with a draft of another
    vocabulary); how long it «loads the model»; `moe` - by where the experts are, [answer, reading] (44.2)."""
    (folder / "speeds.json").write_text(json.dumps({"speeds": by_kind, "fewer": fewer, "crash": list(crash),
                                                    "rejects": list(rejects), "load_s": load_s, "moe": moe or {}}),
                                        encoding="utf-8")  # fmt: skip


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def test_guessing_ahead_is_measured_by_llama_server_itself(hub, setup, spec_server):  # noqa: F811
    """42.4: llama-bench has no draft model, so guessing ahead is measured by llama-server of the config's build:
    the model's own command as the config would have it (its macros in, a port of the measuring's own, past
    llama-swap), each answer's tokens a second from llama.cpp's own timings, at the model's own sampling; then the
    server is let go - its port free, no program left (a .cmd's child too)."""
    text = setup.read_text(encoding="utf-8")
    build = lc.server_flags(spec_server / "llama-server.cmd")
    port = tuning.free_port()
    argv = tuning.spec_command(text, "big", {"spec_type": "ngram-mod"}, build, port)
    assert argv[0] == (spec_server / "llama-server.cmd").as_posix() and argv[argv.index("-m") + 1].endswith("big.gguf")
    assert argv[argv.index("--port") + 1] == str(port) and argv[argv.index("--spec-type") + 1] == "ngram-mod"
    assert "-c" in argv and "--mmproj" in argv  # the model as it runs: its context, its vision projector

    found = tuner(hub, measured={})
    measured = found._serve(argv, port, {"temperature": 0.3, "top_k": 20})
    assert measured == {"tg": 44.0, "accepted": 0.7}
    asked = [json.loads(line) for line in (spec_server / "asked.txt").read_text(encoding="utf-8").splitlines()]
    assert len(asked) == 1 + len(tuning.SPEC_PROMPTS)  # a short warm-up first: not counted
    assert all(a["temperature"] == 0.3 and a["top_k"] == 20 and a["seed"] == tuning.SEED for a in asked)
    assert {a["max_tokens"] for a in asked[1:]} == {tuning.SPEC_TOKENS} and not any(a.get("stream") for a in asked)
    assert not listening(port)  # let go

    speeds(spec_server, {}, crash=["draft-simple"])
    port = tuning.free_port()
    crashed = tuning.spec_command(text, "big", {"spec_type": "draft-simple", "draft_model": "D:/d.gguf"}, build, port)
    with pytest.raises(tuning.TuneError, match="not supported by this context"):  # its own words, from its log
        found._serve(crashed, port, {})


def test_what_guessing_ahead_is_measured(tmp_path):
    """42.4 (the user's word, 2026-11-13): without guessing, ngram (it takes no memory), and the draft model -
    beside the model as it is, or, when it does not fit so, wholly on the graphics card and as few of the model's
    layers fewer as it needs; the context is not made smaller by itself - what the draft lacks and the context it
    would fit with go to the window."""
    from tests.test_llama_config import HELP

    build = lc.parse_help(HELP)
    info = {"architecture": "qwen3", "layers": 36, "kv_layers": 36, "kv_heads": 8, "key_length": 128,
            "value_length": 128, "context_trained": 40960, "size_bytes": int(4.7 * 1024**3)}  # fmt: skip
    draft = {"path": tmp_path / "draft.gguf", "info": {**info, "layers": 28, "kv_layers": 28,
                                                        "size_bytes": int(0.6 * 1024**3)}}  # fmt: skip
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0"}
    roomy, short = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [draft])
    assert [v["name"] for v in roomy] == ["none", "ngram", "draft"] and short == {}
    assert roomy[1]["changes"] == {"spec_type": "ngram-mod"} and roomy[0]["changes"] == {"spec_type": "none"}
    assert roomy[2]["changes"] == {"spec_type": "draft-simple", "draft_model": str(tmp_path / "draft.gguf"),
                                   "draft_gpu_layers": "all", "draft_cache_type": "q8_0"}  # fmt: skip
    tight, short = tuning.spec_variants(view, info, {"vram_gib": 10.2}, build, [draft])  # 7,9 + 2,8 > 10,2
    assert [v["name"] for v in tight] == ["none", "ngram", "draft_fewer"]
    fewer = tight[2]["changes"]["gpu_layers"]
    assert 30 <= fewer < 36 and tight[2]["changes"]["draft_gpu_layers"] == "all"
    assert short == {"short_gb": 0.4, "context": 16384}  # 16 thousand tokens and the draft fits
    alone, short = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [])  # no draft for it
    assert [v["name"] for v in alone] == ["none", "ngram"] and short == {}
    placed, _ = tuning.spec_variants({**view, "gpu_layers": "auto"}, info, {"vram_gib": 10.0}, build, [draft])
    assert [v["name"] for v in placed] == ["none", "ngram", "draft"]  # llama.cpp's --fit places them itself
    old = lc.parse_help(HELP.replace(",ngram-mod", ""))  # a build without ngram-mod
    assert tuning.spec_variants(view, info, {"vram_gib": 15.0}, old, [])[0][1]["changes"] == {
        "spec_type": "ngram-simple"}  # fmt: skip


def launched(folder):
    """The kinds of guessing each launch of the stand-in llama-server was started with."""
    launches = [json.loads(line) for line in (folder / "launches.txt").read_text(encoding="utf-8").splitlines()]
    return [a[a.index("--spec-type") + 1] if "--spec-type" in a else "none" for a in launches]


def test_the_fastest_guessing_ahead_goes_into_the_config_and_is_checked(hub, setup, spec_server):  # noqa: F811
    """42.4: after llama-bench's settings - guessing ahead: none, ngram and the draft model measured by llama-server
    at the model's own sampling; the fastest by more than 3 % is written in the build's names and checked by loading
    the model; measured again, it stays; within the noise - guessing goes off."""
    hub.app.state.db.call(lambda conn: model_settings.save(conn, "llama-swap/big", {"params": {"temperature": 0.2}},
                                                           by="test"))  # fmt: skip
    draft = setup.parent / "draft.gguf"
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    assert result["status"] == "same"  # llama-bench: the config's own settings are the fastest
    spec = result["spec"]
    assert (spec["status"], spec["kind"], spec["spec_type"], spec["before"], spec["after"], spec["accepted"]) == (
        "better", "draft", "draft-simple", 40.0, 60.0, 0.7)  # fmt: skip
    assert [m["name"] for m in spec["measured"]] == ["none", "ngram", "draft"] and spec["draft"] == "draft.gguf"
    assert launched(spec_server) == ["none", "ngram-mod", "draft-simple"] and spec["short"] == {}
    asked = [json.loads(line) for line in (spec_server / "asked.txt").read_text(encoding="utf-8").splitlines()]
    assert {a["temperature"] for a in asked} == {0.2}  # the model's own sampling
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"] == {
        "type": "draft-simple", "draft": draft.as_posix(), "draft_gpu_layers": "all", "draft_max": 0,
        "draft_cache_type": "q8_0"}  # fmt: skip
    assert hub.app.state.db.call(tuning.load_results)["big"]["spec"]["kind"] == "draft"  # kept

    written = setup.read_text(encoding="utf-8")
    again = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    assert again["spec"]["status"] == "same" and setup.read_text(encoding="utf-8") == written

    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-simple": 41.1})  # within the measurement's noise
    off = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    assert (off["spec"]["status"], off["spec"]["kind"]) == ("better", "none")
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]["type"] == ""
    assert "draft" not in setup.read_text(encoding="utf-8").split('"big"')[1].split('"huge"')[0]


def test_guessing_ahead_that_does_not_start_is_passed_over_or_taken_back(hub, setup, spec_server):  # noqa: F811
    """42.4: a kind of guessing llama-server cannot start with in the measuring («speculative decoding not supported
    by this context») is passed over; one that starts there but not in llama-swap - the check finds it, the model's
    lines come back, and what llama-bench wrote stays. A build that knows no guessing - nothing measured."""
    draft = setup.parent / "draft.gguf"
    speeds(spec_server, {"none": 40.0, "ngram-mod": 44.0}, crash=["draft-simple"])
    passed = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    spec = passed["spec"]
    assert spec["kind"] == "ngram" and "not supported" in spec["measured"][2]["error"]
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]["type"] == "ngram-mod"

    speeds(spec_server, {"none": 40.0, "ngram-mod": 40.0, "draft-simple": 60.0})
    before = setup.read_text(encoding="utf-8")
    refused = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40), (2048, 1500, 52)]}, draft=draft,
                                refuse_draft=True).tune("big"))  # fmt: skip
    assert refused["status"] == "better" and refused["spec"]["status"] == "failed"  # llama-bench's change stays
    assert "конфиг возвращён" in refused["spec"]["error"]
    now = lc.model_view(setup.read_text(encoding="utf-8"), "big")
    assert now["ubatch"] == 2048 and now["spec"] == lc.model_view(before, "big")["spec"]  # its own lines back

    knows = "-m,    --model FNAME                    model path to load\n"  # and no guessing ahead
    (spec_server / "help.txt").write_text(knows, encoding="utf-8")
    server = spec_server / "llama-server.cmd"  # another build: its --help is read anew
    server.write_text(server.read_text(encoding="utf-8") + "rem a build that knows no guessing\n", encoding="utf-8")
    old = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    assert old["spec"] == {"status": "unsupported"}


def test_a_draft_llama_cpp_does_not_take_is_not_measured(hub, setup, spec_server):  # noqa: F811
    """42.7 (the live check, 2026-11-13): llama-server with a draft of another vocabulary says so in its log («the
    target and draft vocabs are not compatible») and goes on without guessing - the draft held in the video memory all
    the same (0.9 GB for Qwen3.5 0.8B). Its speed is the model's own and the noise (+2.5 % that day, against 3 %):
    nothing guessed is not a measurement - passed over with llama.cpp's words, never written."""
    draft = setup.parent / "draft.gguf"
    before = lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]
    speeds(spec_server, {"none": 40.0, "ngram-mod": 40.5, "draft-simple": 43.0}, rejects=["draft-simple"])
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=draft).tune("big"))
    spec = result["spec"]
    assert spec["kind"] == "none" and [m["name"] for m in spec["measured"]] == ["none", "ngram", "draft"]
    assert "tg" not in spec["measured"][2] and "vocabs are not compatible" in spec["measured"][2]["error"]
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"] == before  # nothing written


def test_tuning_by_hand_waits_for_the_card_and_gives_way_to_a_chat(hub, setup, spec_server, monkeypatch):  # noqa: F811
    """42.4: «Подобрать» by hand measured beside a game or another program's work (their numbers in it), and a chat
    that began while llama-server measured put a second model on the card. By hand too, it waits for the card to be
    quiet, and a chat stops it - llama-server let go - and it is measured again after."""
    monkeypatch.setattr(tuning, "WAIT_S", 0.05)
    speeds(spec_server, {"none": 40.0, "ngram-mod": 44.0}, load_s=1.5)
    found = tuner(hub, measured={"q8_0": [(512, 900, 40)]})
    card = {"util_pct": 90}
    found._gpu = lambda: card

    async def scenario():
        found.request("big")
        await asyncio.sleep(0.4)
        assert found.job["status"] == "waiting" and not (spec_server / "launches.txt").exists()
        card["util_pct"] = 2
        started = time.monotonic()
        while not (spec_server / "launches.txt").exists():
            assert time.monotonic() - started < 20, found.job
            await asyncio.sleep(0.05)
        found._chat.busy = ["c1"]  # a chat begins while llama-server loads
        while found.job.get("status") != "waiting":
            assert time.monotonic() - started < 20, found.job
            await asyncio.sleep(0.05)
        assert "big" not in found.results and not stand_ins()  # nothing by its numbers, the server let go
        found._chat.busy = []
        while "big" not in found.results:
            assert time.monotonic() - started < 30, found.job
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert found.results["big"]["spec"]["kind"] == "ngram"


def stand_ins():
    """The stand-in llama-servers still running."""
    alive = []
    for process in psutil.process_iter(["cmdline"]):
        with contextlib.suppress(psutil.Error, TypeError):
            if any(part.endswith("server.py") for part in process.info["cmdline"] or []):
                alive.append(process.pid)
    return alive


# --- where a Mixture-of-Experts model's experts go, measured by llama-server itself (44.2) ---

ROOM = {"vram_gib": 15.0, "ram_gib": 60.0}  # a 16 GB card less the desktop's share
WHOLE = {"gpu_layers": "all", "cpu_moe": False, "n_cpu_moe": 0, "moe_cache_mib": 0}


def test_where_the_experts_are_measured(tmp_path):
    """44.2: a Mixture-of-Experts model that does not fit on the card whole - Qwen3-30B-A3B's 17,3 GB on a 16 GB
    card: its command's own placement first (llama.cpp's --fit after an install); the experts of the fewest first
    layers in RAM with which the rest fits, and of three layers more; every expert in RAM and a cache of them as large
    as the room leaves. Nothing for a model that fits whole or has no experts; no cache without a build that has it,
    nor experts past the RAM there is; a placement the command has already is not measured twice."""
    from tests.test_llama_config import HELP, MOE, MOE_CACHE_HELP, moe_tensors

    build = lc.parse_help(HELP)
    info = tuning.model_info(gguf(tmp_path / "moe.gguf", MOE, moe_tensors()))
    view = {"context": 32768, "gpu_layers": "auto", "cache_type": "q8_0", "cpu_moe": False, "n_cpu_moe": 0,
            "moe_cache_mib": 0}  # fmt: skip
    found = tuning.moe_variants(view, info, ROOM, build)
    assert [v["name"] for v in found] == ["now", "layers", "layers_more", "cache"] and found[0]["changes"] == {}
    assert [v["changes"] for v in found[1:]] == [{**WHOLE, "n_cpu_moe": 14}, {**WHOLE, "n_cpu_moe": 17},
                                                 {**WHOLE, "cpu_moe": True, "moe_cache_mib": 11776}]  # fmt: skip
    assert tuning.moe_variants(view, info, {"vram_gib": 24.0, "ram_gib": 60.0}, build) == []  # it fits whole
    held = {**view, "gpu_layers": "all", "cpu_moe": True}  # fits whole, its experts kept in RAM by hand
    assert [v["name"] for v in tuning.moe_variants(held, info, {"vram_gib": 24.0}, build)] == ["now", "card"]
    older = lc.parse_help(HELP.replace(MOE_CACHE_HELP, ""))
    assert [v["name"] for v in tuning.moe_variants(view, info, ROOM, older)] == ["now", "layers", "layers_more"]
    little = tuning.moe_variants(view, info, {**ROOM, "ram_gib": 8.0}, build)  # every expert: 16,3 GB of RAM
    assert [v["name"] for v in little] == ["now", "layers", "layers_more"]
    placed = {**view, "gpu_layers": "all", "n_cpu_moe": 14}  # tuned before
    assert [v["name"] for v in tuning.moe_variants(placed, info, ROOM, build)] == ["now", "layers_more", "cache"]
    assert tuning.moe_variants(view, {**info, "expert_bytes": []}, ROOM, build) == []

    # the pick: an answer faster by more than 3 %, its reading of a long question at most 10 % slower
    now = {"name": "now", "tg": 40.0, "pp": 1400.0}
    assert tuning.moe_choice([now, {"name": "layers", "tg": 41.0, "pp": 1500.0}])["name"] == "now"  # noise
    assert tuning.moe_choice([now, {"name": "layers", "tg": 44.0, "pp": 1300.0},
                              {"name": "cache", "tg": 46.0, "pp": 900.0}])["name"] == "layers"  # fmt: skip
    assert tuning.moe_choice([{"name": "now", "error": "out of memory"}, {"name": "layers", "tg": 30.0}])["name"] == (
        "layers")  # fmt: skip


def placements(folder):
    """Where each launch of the stand-in llama-server kept the experts (44.2)."""
    out = []
    for line in (folder / "launches.txt").read_text(encoding="utf-8").splitlines():
        args = json.loads(line)
        if "--cpu-moe" in args:
            out.append("cache" if "--moe-cache-mib" in args else "all")
        else:
            out.append("layers" + args[args.index("-ncmoe") + 1] if "-ncmoe" in args else "now")
    return out


def test_the_experts_go_where_the_model_answers_fastest(hub, setup, spec_server):  # noqa: F811
    """44.2: after llama-bench's settings, each placement of the experts is measured by llama-server itself - its
    answers and its reading of a long question; the fastest answer by more than 3 % is written and checked, unless it
    reads more than 10 % slower than the command's own; guessing ahead is measured after, with it. Within the noise
    nothing is written; a model without experts has no such step."""
    from tests.test_llama_config import MOE, moe_tensors

    text = setup.read_text(encoding="utf-8")
    dense = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}).tune("big"))
    assert "moe" not in dense and dense["spec"]["kind"] == "ngram"
    gguf(setup.parent / "big.gguf", MOE, moe_tensors())  # Qwen3-30B-A3B's header: 17,3 GB by its table
    fit = lc.edit(text, "big", {"gpu_layers": "auto"})  # as an install leaves it: llama.cpp's --fit

    def tune(by_place):
        setup.write_text(fit, encoding="utf-8")
        (spec_server / "launches.txt").unlink(missing_ok=True)
        (spec_server / "asked.txt").unlink(missing_ok=True)
        speeds(spec_server, {"none": 40.0, "ngram-mod": 40.0}, moe=by_place)
        return asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, space=ROOM).tune("big"))

    result = tune({"now": [40.0, 1400.0], "layers14": [44.0, 1380.0], "layers17": [42.0, 1300.0],
                   "cache": [36.0, 900.0]})  # fmt: skip
    moe = result["moe"]
    assert (moe["status"], moe["kind"], moe["before"], moe["after"]) == (
        "better", "layers", {"tg": 40.0, "pp": 1400.0}, {"tg": 44.0, "pp": 1380.0})  # fmt: skip
    assert [m["name"] for m in moe["measured"]] == ["now", "layers", "layers_more", "cache"]
    assert moe["placed"] == {**WHOLE, "n_cpu_moe": 14}  # 44.5: the window tells it while the command keeps it
    view = lc.model_view(setup.read_text(encoding="utf-8"), "big")
    assert (view["gpu_layers"], view["n_cpu_moe"], view["cpu_moe"], view["moe_cache_mib"]) == ("all", 14, False, 0)
    assert "--fit" not in setup.read_text(encoding="utf-8").split('"big"')[1].split('"huge"')[0]
    # guessing ahead after, with the experts where they were written
    assert placements(spec_server) == ["now", "layers14", "layers17", "cache", "layers14", "layers14"]
    asked = [json.loads(line) for line in (spec_server / "asked.txt").read_text(encoding="utf-8").splitlines()]
    long = [a for a in asked if a["messages"][0]["content"] == tuning.LONG_PROMPT]
    assert len(long) == 4 and {a["max_tokens"] for a in long} == {1}  # a long question read by each placement
    assert hub.app.state.db.call(tuning.load_results)["big"]["moe"]["kind"] == "layers"  # kept

    slower = tune({"now": [40.0, 1400.0], "layers14": [44.0, 1000.0], "layers17": [43.5, 1350.0],
                   "cache": [36.0, 900.0]})  # fmt: skip
    assert slower["moe"]["kind"] == "layers_more"  # 14 layers answer faster but read 29 % slower
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["n_cpu_moe"] == 17

    same = tune({"now": [40.0, 1400.0], "layers14": [41.0, 1400.0], "layers17": [40.5, 1400.0],
                 "cache": [30.0, 800.0]})  # fmt: skip
    assert (same["moe"]["status"], same["moe"]["kind"]) == ("same", "now")
    assert same["moe"]["placed"] == {**WHOLE, "gpu_layers": "auto"}
    assert lc.model_view(setup.read_text(encoding="utf-8"), "big")["gpu_layers"] == "auto"


def test_a_model_with_a_layer_of_its_own_guesses_ahead_with_it(hub, setup, spec_server):  # noqa: F811
    """44.3: a model trained with layers that guess the next words (MTP: the header's nextn_predict_layers - GLM,
    DeepSeek, Qwen3.5) guesses ahead with them - llama.cpp's draft-mtp, no file of its own: measured after ngram and
    before a draft model, written without -md. A build without draft-mtp, or a model without the layer - not
    measured; a layer llama.cpp starts with but guesses nothing with is not a measurement (42.7)."""
    from tests.test_llama_config import HELP

    build = lc.parse_help(HELP)
    info = {"architecture": "glm4moe", "layers": 47, "kv_layers": 47, "kv_heads": 8, "key_length": 128,
            "value_length": 128, "context_trained": 131072, "size_bytes": int(4.7 * 1024**3),
            "mtp_layers": 1}  # fmt: skip
    draft = {"path": setup.parent / "draft.gguf", "info": {**info, "layers": 28, "kv_layers": 28, "mtp_layers": 0,
                                                            "size_bytes": int(0.6 * 1024**3)}}  # fmt: skip
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0"}
    found, _ = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [draft])
    assert [v["name"] for v in found] == ["none", "ngram", "mtp", "draft"]
    assert found[2]["changes"] == {"spec_type": "draft-mtp"}
    old = lc.parse_help(HELP.replace(",draft-mtp", ""))
    assert [v["name"] for v in tuning.spec_variants(view, info, {"vram_gib": 15.0}, old, [])[0]] == ["none", "ngram"]
    plain = tuning.spec_variants(view, {**info, "mtp_layers": 0}, {"vram_gib": 15.0}, build, [])[0]
    assert [v["name"] for v in plain] == ["none", "ngram"]

    keys = {"general.architecture": "tiny", "tiny.block_count": 8, "tiny.context_length": 65536,
            "tiny.embedding_length": 256, "tiny.attention.head_count": 4, "tiny.attention.head_count_kv": 2,
            "tiny.nextn_predict_layers": 1}  # fmt: skip
    gguf(setup.parent / "big.gguf", keys)
    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-mtp": 52.0})
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}).tune("big"))
    spec = result["spec"]
    assert (spec["status"], spec["kind"], spec["spec_type"], spec["after"]) == ("better", "mtp", "draft-mtp", 52.0)
    assert launched(spec_server) == ["none", "ngram-mod", "draft-mtp"]
    written = lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]
    assert (written["type"], written["draft"]) == ("draft-mtp", "")  # the model's own layer: no file

    speeds(spec_server, {"none": 40.0, "ngram-mod": 40.5, "draft-mtp": 43.0}, rejects=["draft-mtp"])
    nothing = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}).tune("big"))["spec"]
    assert nothing["kind"] == "none" and "tg" not in nothing["measured"][2]
    assert "MTP" in nothing["measured"][2]["error"]


def test_a_draft_made_for_its_model_guesses_its_own_way(hub, setup, spec_server):  # noqa: F811
    """44.4: a DFlash or EAGLE-3 draft is made for one model - converted with its tokenizer and embeddings - and
    guesses its own way: llama.cpp's draft-dflash and draft-eagle3, the kind by the draft's header (its
    general.architecture, as b11899 names them); a build that does not know the kind - not measured. Written with
    its kind and its file."""
    from tests.test_llama_config import HELP

    build = lc.parse_help(HELP)
    info = {"architecture": "qwen35moe", "layers": 40, "kv_layers": 10, "kv_heads": 2, "key_length": 256,
            "value_length": 256, "context_trained": 262144, "size_bytes": 9 * 1024**3}  # fmt: skip
    small = {**info, "layers": 5, "kv_layers": 5, "size_bytes": int(0.9 * 1024**3)}
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0"}
    for arch, kind in (("dflash", "draft-dflash"), ("eagle3", "draft-eagle3"), ("qwen3", "draft-simple")):
        draft = {"path": setup.parent / "draft.gguf", "info": {**small, "architecture": arch}}
        found, _ = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [draft])
        assert [v["name"] for v in found] == ["none", "ngram", "draft"] and found[2]["changes"]["spec_type"] == kind
    old = lc.parse_help(HELP.replace(",draft-dflash", ""))
    dflash = {"path": setup.parent / "draft.gguf", "info": {**small, "architecture": "dflash"}}
    assert [v["name"] for v in tuning.spec_variants(view, info, {"vram_gib": 15.0}, old, [dflash])[0]] == [
        "none", "ngram"]  # fmt: skip

    keys = {"general.architecture": "dflash", "dflash.block_count": 5, "dflash.context_length": 65536,
            "dflash.embedding_length": 128, "dflash.attention.head_count": 2,
            "dflash.attention.head_count_kv": 1}  # fmt: skip
    gguf(setup.parent / "draft.gguf", keys)
    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-dflash": 70.0})
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=setup.parent / "draft.gguf").tune("big"))
    spec = result["spec"]
    assert (spec["status"], spec["kind"], spec["spec_type"], spec["after"]) == ("better", "draft", "draft-dflash", 70.0)
    assert launched(spec_server) == ["none", "ngram-mod", "draft-dflash"]
    written = lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]
    assert written["type"] == "draft-dflash" and written["draft"].endswith("draft.gguf")


def test_an_mtp_head_of_its_own_file_guesses_ahead(hub, setup, spec_server):  # noqa: F811
    """44.7: a model without layers that guess ahead may have them in a file of their own (ggml-org's
    `mtp-<model>.gguf`, its header with one block more and only that block's tensors) - its draft for draft-mtp:
    measured with `-md`, the head's memory its one block and its one layer's KV cache; written with its file. A whole
    small model with such a layer of its own (a Qwen3.5 family's draft) stays draft-simple."""
    from tests.test_llama_config import F32, HELP, gguf

    build = lc.parse_help(HELP)
    info = {"architecture": "qwen35moe", "layers": 40, "kv_layers": 10, "kv_heads": 2, "key_length": 256,
            "value_length": 256, "context_trained": 262144, "size_bytes": 9 * 1024**3, "mtp_layers": 0}  # fmt: skip
    head = {**info, "layers": 41, "kv_layers": 1, "mtp_layers": 1, "mtp_head": True, "size_bytes": 2 * 1024**3}
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0"}
    found, _ = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [{"path": setup.parent / "h.gguf",
                                                                             "info": head}])  # fmt: skip
    assert [v["name"] for v in found] == ["none", "ngram", "draft"]
    assert found[2]["changes"]["spec_type"] == "draft-mtp" and found[2]["changes"]["draft_model"].endswith("h.gguf")
    small = {**info, "layers": 24, "kv_layers": 6, "mtp_layers": 1, "size_bytes": 1024**3}  # a whole model
    plain = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [{"path": setup.parent / "s.gguf",
                                                                          "info": small}])[0]  # fmt: skip
    assert plain[2]["changes"]["spec_type"] == "draft-simple"
    old = lc.parse_help(HELP.replace(",draft-mtp", ""))
    assert [v["name"] for v in tuning.spec_variants(view, info, {"vram_gib": 15.0}, old, [{
        "path": setup.parent / "h.gguf", "info": head}])[0]] == ["none", "ngram"]  # fmt: skip

    keys = {"general.architecture": "tiny", "tiny.block_count": 9, "tiny.context_length": 65536,
            "tiny.embedding_length": 256, "tiny.attention.head_count": 4, "tiny.attention.head_count_kv": 2,
            "tiny.nextn_predict_layers": 1}  # fmt: skip
    file = gguf(setup.parent / "mtp-big.gguf", keys, [("token_embd.weight", (256, 3), F32),
                                                      ("blk.8.nextn.eh_proj.weight", (512, 256), F32)])  # fmt: skip
    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-mtp": 58.0})
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=file).tune("big"))
    spec = result["spec"]
    assert (spec["status"], spec["kind"], spec["spec_type"], spec["after"]) == ("better", "draft", "draft-mtp", 58.0)
    assert launched(spec_server) == ["none", "ngram-mod", "draft-mtp"] and spec["draft"] == "mtp-big.gguf"
    written = lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]
    assert written["type"] == "draft-mtp" and written["draft"].endswith("mtp-big.gguf")


def test_a_draft_beside_a_moe_model_takes_the_room_of_its_experts():
    """44.9 (the live check 44.6, Qwen3.6-35B-A3B on 16 GB): a draft that does not fit beside a Mixture-of-Experts
    model as it is placed gets its room from the experts of a few more layers in RAM, not from whole layers of the
    model - the MTP head 120 tok/s so against 70 with five layers off the card, DFlash 128 against 118. The fewest
    more that let it fit; all the experts in RAM already, or a model without experts - fewer layers, as before."""
    from tests.test_llama_config import HELP

    build = lc.parse_help(HELP)
    gib = 1024**3
    info = {"architecture": "qwen35moe", "layers": 40, "kv_layers": 10, "kv_heads": 2, "key_length": 256,
            "value_length": 256, "context_trained": 262144, "size_bytes": int(19.1 * gib), "mtp_layers": 0,
            "experts": 256, "experts_used": 8, "expert_bytes": [int(0.45 * gib)] * 40}  # fmt: skip
    small = {**info, "architecture": "dflash", "layers": 6, "kv_layers": 6, "size_bytes": int(0.4 * gib)}
    small.pop("expert_bytes")
    flash = {"path": Path("dflash-big.gguf"), "info": small}
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0", "n_cpu_moe": 13, "cpu_moe": False}

    def need(n, draft=None):
        load = {"info": small, "gpu_layers": "all", "cache_type": "q8_0"} if draft else None
        return estimate(info, context=32768, cache_type="q8_0", gpu_layers="all", draft=load,
                        moe={"all": False, "layers": n, "cache_mib": 0})["vram_gb"]  # fmt: skip

    room = need(13) + 0.3  # the model fits as it is; with the draft it does not
    found, short = tuning.spec_variants(view, info, {"vram_gib": room}, build, [flash])
    fewest = next(n for n in range(14, 40) if need(n, flash) <= room)
    assert [v["name"] for v in found] == ["none", "ngram", "draft_experts"] and fewest > 14
    assert found[2]["changes"]["n_cpu_moe"] == fewest and "gpu_layers" not in found[2]["changes"]
    assert found[2]["draft"] == "dflash-big.gguf" and short["short_gb"] > 0  # as the model is now, it lacks room
    held = {**view, "cpu_moe": True, "n_cpu_moe": 0}  # all the experts in RAM already: fewer layers
    assert tuning.spec_variants(held, info, {"vram_gib": need(40) + 0.3}, build, [flash])[0][2]["name"] == (
        "draft_fewer")  # fmt: skip
    dense = {key: value for key, value in info.items() if key not in ("experts", "expert_bytes")}
    assert tuning.spec_variants({**view, "n_cpu_moe": 0}, dense, {"vram_gib": 17.0}, build, [flash])[0][2]["name"] == (
        "draft_fewer")  # fmt: skip


def test_each_draft_made_for_the_model_is_measured(hub, setup, spec_server):  # noqa: F811
    """44.8: ggml-org puts a DFlash draft and an MTP head beside the model - which guesses better on this computer
    only measuring says: each one is measured, the smaller first (a costlier one wins only by more than CLOSE), and
    the fastest is written. A draft that does not fit as the model is placed - the least it lacks goes to the window,
    while none fits."""
    from tests.test_llama_config import F32, HELP

    build = lc.parse_help(HELP)
    info = {"architecture": "qwen35moe", "layers": 40, "kv_layers": 10, "kv_heads": 2, "key_length": 256,
            "value_length": 256, "context_trained": 262144, "size_bytes": 9 * 1024**3, "mtp_layers": 0}  # fmt: skip
    head = {"path": setup.parent / "mtp-big.gguf", "info": {**info, "layers": 41, "kv_layers": 1, "mtp_layers": 1,
                                                             "mtp_head": True, "size_bytes": 2 * 1024**3}}  # fmt: skip
    small = {**info, "architecture": "dflash", "layers": 6, "kv_layers": 6, "size_bytes": 1024**3 // 2}
    flash = {"path": setup.parent / "dflash-big.gguf", "info": small}
    view = {"context": 32768, "gpu_layers": "all", "cache_type": "q8_0"}
    found, short = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, [head, flash])
    assert [(v["name"], v["changes"]["spec_type"], v.get("draft")) for v in found] == [
        ("none", "none", None), ("ngram", "ngram-mod", None), ("draft", "draft-dflash", "dflash-big.gguf"),
        ("draft", "draft-mtp", "mtp-big.gguf")] and short == {}  # fmt: skip
    tight, short = tuning.spec_variants(view, info, {"vram_gib": 11.5}, build, [head, flash])
    placed = [(v["name"], v.get("draft")) for v in tight][2:]
    assert placed == [("draft", "dflash-big.gguf"), ("draft_fewer", "mtp-big.gguf")] and short == {}
    none_fit, short = tuning.spec_variants(view, info, {"vram_gib": 10.2}, build, [head, flash])
    assert [v["name"] for v in none_fit] == ["none", "ngram", "draft_fewer", "draft_fewer"]
    alone = tuning.spec_variants(view, info, {"vram_gib": 10.2}, build, [flash])[1]
    assert short == alone and alone["short_gb"] > 0  # DFlash's: it lacks the least

    keys = {"general.architecture": "tiny", "tiny.block_count": 9, "tiny.context_length": 65536,
            "tiny.embedding_length": 256, "tiny.attention.head_count": 4,
            "tiny.attention.head_count_kv": 2}  # fmt: skip
    gguf(setup.parent / "dflash-big.gguf", {**keys, "general.architecture": "dflash", "dflash.block_count": 5})
    gguf(setup.parent / "mtp-big.gguf", {**keys, "tiny.nextn_predict_layers": 1}, [
        ("token_embd.weight", (256, 3), F32), ("blk.8.nextn.eh_proj.weight", (512, 256), F32)])  # fmt: skip
    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-dflash": 66.0, "draft-mtp": 58.0})
    files = [setup.parent / "mtp-big.gguf", setup.parent / "dflash-big.gguf"]
    result = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=files).tune("big"))
    spec = result["spec"]
    assert launched(spec_server) == ["none", "ngram-mod", "draft-dflash", "draft-mtp"]
    assert (spec["kind"], spec["spec_type"], spec["draft"], spec["after"]) == ("draft", "draft-dflash",
                                                                               "dflash-big.gguf", 66.0)  # fmt: skip
    assert [m.get("draft") for m in spec["measured"]] == [None, None, "dflash-big.gguf", "mtp-big.gguf"]
    written = lc.model_view(setup.read_text(encoding="utf-8"), "big")["spec"]
    assert written["type"] == "draft-dflash" and written["draft"].endswith("dflash-big.gguf")

    # measured again with nothing found: the command's own draft, as before 44.8; with the head found - both
    speeds(spec_server, {"none": 40.0, "ngram-mod": 41.0, "draft-dflash": 66.0, "draft-mtp": 75.0})
    again = asyncio.run(tuner(hub, measured={"q8_0": [(512, 900, 40)]}, draft=files[:1]).tune("big"))["spec"]
    assert [m.get("draft") for m in again["measured"]][2:] == ["dflash-big.gguf", "mtp-big.gguf"]
    assert (again["spec_type"], again["draft"]) == ("draft-mtp", "mtp-big.gguf")
