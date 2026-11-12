"""Launch settings by measurement (step 15.2, D49): llama-bench of the same llama.cpp build measures what a few
settings give on this computer - flash attention, the KV cache's type, ubatch, at a filled context - and the fastest
answer goes into llama-swap's config, checked by loading the model with its whole context (else the old config comes
back). Automatic tuning waits until no chat uses a model. No model is loaded here: llama-bench and llama-swap are
stand-ins."""

import asyncio
import json
import socket
import sys
import time

import httpx
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
answers with llama.cpp's timings - its speed by --spec-type, and by -ngl when it is not all the layers. Each launch
and each request are written down beside it."""
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
        drafted = 0 if kind == "none" else 100
        self._send(200, {"choices": [{"message": {"content": "ok"}}],
                         "timings": {"predicted_per_second": speed, "draft_n": drafted,
                                     "draft_n_accepted": int(drafted * how.get("accepted", 0.7))}})


HTTPServer(("127.0.0.1", int(flag("--port"))), Handler).serve_forever()
'''


@pytest.fixture
def served(setup, tmp_path):
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


def speeds(folder, by_kind, *, fewer=0.0, crash=()):
    """How fast the stand-in llama-server answers: by --spec-type; with fewer layers on the card; which kinds of
    guessing it cannot start with."""
    (folder / "speeds.json").write_text(json.dumps({"speeds": by_kind, "fewer": fewer, "crash": list(crash)}),
                                        encoding="utf-8")  # fmt: skip


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def test_guessing_ahead_is_measured_by_llama_server_itself(hub, setup, served):  # noqa: F811
    """42.4: llama-bench has no draft model, so guessing ahead is measured by llama-server of the config's build:
    the model's own command as the config would have it (its macros in, a port of the measuring's own, past
    llama-swap), each answer's tokens a second from llama.cpp's own timings, at the model's own sampling; then the
    server is let go - its port free, no program left (a .cmd's child too)."""
    text = setup.read_text(encoding="utf-8")
    build = lc.server_flags(served / "llama-server.cmd")
    port = tuning.free_port()
    argv = tuning.spec_command(text, "big", {"spec_type": "ngram-mod"}, build, port)
    assert argv[0] == (served / "llama-server.cmd").as_posix() and argv[argv.index("-m") + 1].endswith("big.gguf")
    assert argv[argv.index("--port") + 1] == str(port) and argv[argv.index("--spec-type") + 1] == "ngram-mod"
    assert "-c" in argv and "--mmproj" in argv  # the model as it runs: its context, its vision projector

    found = tuner(hub, measured={})
    measured = found._serve(argv, port, {"temperature": 0.3, "top_k": 20})
    assert measured == {"tg": 44.0, "accepted": 0.7}
    asked = [json.loads(line) for line in (served / "asked.txt").read_text(encoding="utf-8").splitlines()]
    assert len(asked) == 1 + len(tuning.SPEC_PROMPTS)  # a short warm-up first: not counted
    assert all(a["temperature"] == 0.3 and a["top_k"] == 20 and a["seed"] == tuning.SEED for a in asked)
    assert {a["max_tokens"] for a in asked[1:]} == {tuning.SPEC_TOKENS} and not any(a.get("stream") for a in asked)
    assert not listening(port)  # let go

    speeds(served, {}, crash=["draft-simple"])
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
    roomy, short = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, draft)
    assert [v["name"] for v in roomy] == ["none", "ngram", "draft"] and short == {}
    assert roomy[1]["changes"] == {"spec_type": "ngram-mod"} and roomy[0]["changes"] == {"spec_type": "none"}
    assert roomy[2]["changes"] == {"spec_type": "draft-simple", "draft_model": str(tmp_path / "draft.gguf"),
                                   "draft_gpu_layers": "all", "draft_cache_type": "q8_0"}  # fmt: skip
    tight, short = tuning.spec_variants(view, info, {"vram_gib": 10.2}, build, draft)  # 7,9 + 2,8 > 10,2
    assert [v["name"] for v in tight] == ["none", "ngram", "draft_fewer"]
    fewer = tight[2]["changes"]["gpu_layers"]
    assert 30 <= fewer < 36 and tight[2]["changes"]["draft_gpu_layers"] == "all"
    assert short == {"short_gb": 0.4, "context": 16384}  # 16 thousand tokens and the draft fits
    alone, short = tuning.spec_variants(view, info, {"vram_gib": 15.0}, build, None)  # no draft for it
    assert [v["name"] for v in alone] == ["none", "ngram"] and short == {}
    placed, _ = tuning.spec_variants({**view, "gpu_layers": "auto"}, info, {"vram_gib": 10.0}, build, draft)
    assert [v["name"] for v in placed] == ["none", "ngram", "draft"]  # llama.cpp's --fit places them itself
    old = lc.parse_help(HELP.replace(",ngram-mod", ""))  # a build without ngram-mod
    assert tuning.spec_variants(view, info, {"vram_gib": 15.0}, old, None)[0][1]["changes"] == {
        "spec_type": "ngram-simple"}  # fmt: skip
