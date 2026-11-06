"""Code completion for the window's editor (D47, step 14.6): fill in the middle through llama-swap, the next request
cancelling the one before all the way to llama.cpp, a model not loaded pausing it, the cloud and secrets (D10), the
usage - against a stand-in llama-swap with one llama.cpp slot behind it, a real HTTP server. The model's edit of
the selection (14.7)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.gateway.base import Model, StreamEvent
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_tools import BASE, ToolProvider

SECRET = "sk-ant-api03-" + "q" * 40


class FakeLlama:
    """llama-swap and its one llama.cpp: /v1/models, /running, the chat API, and /upstream/coder/infill streaming a
    piece every `delay` s - one slot, as llama.cpp started with one: a request waits for it while another streams;
    a closed connection is written down, and frees the slot."""

    def __init__(self, *, running: bool = True, fim: bool = True, pieces: int = 12, delay: float = 0.05):
        self.running, self.fim, self.pieces, self.delay = running, fim, pieces, delay
        self.script: list[str] = []  # the pieces to stream, when set; else "t0 ", "t1 ", ...
        self.events: list[tuple[str, int]] = []
        self.bodies: list[dict] = []
        self.slot = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # the end of a stream is the closed connection

            def log_message(self, *_args) -> None:
                pass

            def _json(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/v1/models":
                    self._json(200, {"data": [{"id": "coder"}]})
                elif self.path == "/running":
                    self._json(200, {"running": [{"model": "coder", "state": "ready"}] if fake.running else []})
                else:
                    self._json(404, {})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.bodies.append({"path": self.path, **body})
                if self.path == "/upstream/coder/infill" and not fake.fim:
                    self._json(501, {"error": "this model has no fill-in-the-middle tokens"})
                    return
                number = len(fake.bodies)
                if not fake.slot.acquire(blocking=False):
                    fake.events.append(("waits", number))  # the slot is still taken by the one before
                    fake.slot.acquire()
                fake.events.append(("start", number))
                chat = self.path == "/v1/chat/completions"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for text in fake.script or [f"t{n} " for n in range(fake.pieces)]:
                        piece = {"choices": [{"delta": {"content": text}}]} if chat else {"content": text}
                        self.wfile.write(f"data: {json.dumps(piece)}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(fake.delay)
                    last = ({"choices": [{"delta": {}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 50, "completion_tokens": fake.pieces}} if chat
                            else {"content": "", "stop": True, "tokens_evaluated": 50,
                                  "tokens_predicted": fake.pieces})  # fmt: skip
                    self.wfile.write(f"data: {json.dumps(last)}\n\n".encode())
                    if chat:
                        self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    fake.events.append(("done", number))
                except ConnectionError, OSError:
                    fake.events.append(("closed", number))  # the hub let go: the slot is free again
                finally:
                    fake.slot.release()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def llama():
    fake = FakeLlama()
    yield fake
    fake.close()


@pytest.fixture
def hub(llama):
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url=llama.url, status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.portal.call(app.state.gateway.sync_catalog)
        yield client


def choose(hub, model_id: str) -> None:
    saved = hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": {"complete": model_id}}})
    assert saved.status_code == 200, saved.text


def ask(hub, pid: str, **extra) -> list[dict]:
    body = {"project_id": pid, "path": "src/app.py", "before": "def total(items):\n    return ", "after": "\n",
            **extra}  # fmt: skip
    with hub.stream("POST", "/v1/editor/complete", json=body) as response:
        assert response.status_code == 200, response.read()
        return [json.loads(line) for line in response.iter_lines() if line]


def test_the_next_request_cancels_the_one_before_all_the_way_to_llama_cpp(hub, llama, repo):
    """Fill in the middle: llama.cpp's /infill through llama-swap, the code around the cursor, `cache_prompt`, a short
    `n_predict`. A second request cancels the first: its connection to llama.cpp is closed (the stand-in sees it at
    its next piece, as llama.cpp does at its next token), its slot is free, and the second one streams then - not
    after the first's 400 pieces. Both are in the usage, the first as cancelled."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    choose(hub, "llama-swap/coder")
    llama.pieces, llama.delay = 400, 0.02  # longer than the hub's deadline unless cancelled sooner
    first: list[dict] = []
    worker = threading.Thread(target=lambda: first.extend(ask(hub, pid)))
    worker.start()
    deadline = time.monotonic() + 10
    while ("start", 1) not in llama.events:
        assert time.monotonic() < deadline, llama.events
        time.sleep(0.01)
    llama.pieces, llama.delay = 5, 0.01
    began = time.monotonic()
    second = ask(hub, pid, before="x = ")
    worker.join(10)
    assert time.monotonic() - began < 2  # the first's 400 pieces would take 8 s
    assert ("closed", 1) in llama.events and ("done", 1) not in llama.events
    assert llama.events.index(("closed", 1)) < llama.events.index(("start", 2))  # its slot, freed by the cut
    assert second[0] == {"model": "llama-swap/coder", "how": "infill"} and second[-1] == {"done": True}
    assert "".join(p.get("text", "") for p in second) == "t0 t1 t2 t3 t4 "
    infill = llama.bodies[0]
    assert infill["path"] == "/upstream/coder/infill" and infill["cache_prompt"] is True
    assert infill["input_prefix"].endswith("return ") and infill["input_suffix"] == "\n" and infill["n_predict"] <= 128
    assert first and first[0]["how"] == "infill" and len(first) < 400  # cut short

    rows = hub.app.state.db.call(lambda conn: conn.execute(
        "SELECT model_id, output_tokens, meta FROM usage ORDER BY rowid").fetchall())  # fmt: skip
    metas = [json.loads(r["meta"]) for r in rows]
    assert [m["kind"] for m in metas] == ["complete", "complete"] and [m["cancelled"] for m in metas] == [True, False]
    assert rows[1]["output_tokens"] == 5 and metas[1]["stats"]["total_s"] > 0 and "ttft_s" in metas[1]["stats"]


def test_a_model_not_loaded_pauses_completion(hub, llama, repo):
    """The completion model never pushes the chat's out of the graphics card: not loaded - paused, and llama.cpp is
    not asked; the person's «загрузить» (`load`) lets it start. No model chosen, a secret file: paused too."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    assert ask(hub, pid)[0]["paused"] == "no_model"
    choose(hub, "llama-swap/coder")
    llama.running = False
    (paused,) = ask(hub, pid)
    assert paused["paused"] == "not_loaded" and "не загружена" in paused["message"] and llama.bodies == []
    assert ask(hub, pid, load=True)[-1] == {"done": True} and len(llama.bodies) == 1
    assert ask(hub, pid, path=".env")[0]["paused"] == "secret"
    assert hub.post("/v1/editor/complete", json={"project_id": "nope", "path": "a.py"}).status_code == 404


def test_the_grey_text_keeps_the_typed_indent_and_ends_with_its_block(hub, llama, repo):
    """Live on qwen27b (stage 28): after `def mul(a, b):` and the indent the person typed, /infill answered «return a *
    b» and went on into the next function; twice of three it indented the line again - Tab would have doubled the
    indent. The indent typed stays the line's only one; the grey text ends at the first line left of its block, and
    llama.cpp is let go there."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    choose(hub, "llama-swap/coder")
    llama.delay = 0.01

    def grey(before: str, pieces: list[str]) -> str:
        llama.script = pieces
        return "".join(p.get("text", "") for p in ask(hub, pid, before=before))

    tail = [f"\ndef f{n}():\n    pass\n" for n in range(60)]  # what the model would go on with: cut, not waited for
    assert grey("def mul(a, b):\n    ", ["    ret", "urn a * b\n", "\n\ndef div(a, b):\n", "    return a / b\n",
                                         *tail]) == "return a * b"  # fmt: skip
    deadline = time.monotonic() + 5  # the stand-in sees the closed connection at its next piece
    while ("closed", 1) not in llama.events:
        assert time.monotonic() < deadline and ("done", 1) not in llama.events, llama.events
        time.sleep(0.01)
    indented = "def div(a, b):\n    if b == 0:\n        "
    raised = 'raise ZeroDivisionError("b")'
    assert grey(indented, [f"        {raised}\n", "    return a / b\n"]) == raised
    assert grey("def total(items):", ["\n", "    return sum(items)\n", "\n", "\nprint(total([1]))\n"]) == (
        "\n    return sum(items)")  # the cursor at a line's end: the body under it is the block  # fmt: skip
    assert grey("def f(x):\n    ", ["if x:\n        return 1\n", "    return 0\n", "\nclass A:\n"]) == (
        "if x:\n        return 1\n    return 0")  # a block inside keeps its own indent  # fmt: skip
    assert grey("x = ", ["compute(", "1)\n"]) == "compute(1)"  # at the file's left edge only the end is trimmed


def test_without_fill_in_the_middle_it_asks_in_words(hub, llama, repo):
    """A model whose llama.cpp has no fill-in-the-middle tokens: asked through the chat API to go on from the cursor
    with code only - and remembered, the next request asks so at once."""
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    choose(hub, "llama-swap/coder")
    llama.fim, llama.pieces = False, 3
    lines = ask(hub, pid)
    assert "".join(p.get("text", "") for p in lines) == "t0 t1 t2 "
    assert [b["path"] for b in llama.bodies] == ["/upstream/coder/infill", "/v1/chat/completions"]
    prompt = llama.bodies[1]["messages"][1]["content"]
    assert prompt.endswith("def total(items):\n    return <CURSOR>\n") and "File: src/app.py" in prompt
    assert ask(hub, pid)[0]["how"] == "chat"


def test_a_cloud_model_gets_code_only_of_a_project_that_allows_the_cloud(hub, llama, repo):
    """D10: a cloud model for a local-only project - paused, nothing sent; the project allowing the cloud - the code
    goes, through the secret filter."""
    provider = ToolProvider([], locality="cloud")
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    choose(hub, "fake/echo")
    assert ask(hub, pid)[0]["paused"] == "local_only" and provider.calls == []
    assert hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": True}).status_code == 200
    lines = ask(hub, pid, before=f"KEY = '{SECRET}'\nx = ")
    assert lines[0] == {"model": "fake/echo", "how": "chat"} and "".join(p.get("text", "") for p in lines) == "Готово"
    sent = json.dumps(provider.calls[-1][0], ensure_ascii=False)
    assert SECRET not in sent and "<CURSOR>" in sent


# --- the model edits the selection (14.7) ---


class EditProvider:
    """A model that answers an edit - in a Markdown fence, as models do though asked not to."""

    name = "editor"

    def __init__(self, answer: str, locality: str = "local"):
        self.answer = answer
        self.calls: list[list[dict]] = []
        self.models = [Model("editor/fix", "editor", "fix", "Fix", locality, {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.calls.append(messages)
        yield StreamEvent("content", self.answer)
        yield StreamEvent("end", usage={"input_tokens": 30, "output_tokens": 7}, stop_reason="end_turn")


def edit(hub, pid: str, **extra):
    body = {"project_id": pid, "path": "src/app.py", "before": "def total(items):\n",
            "selected": "    total = 0\n    for i in items:\n        total += i\n", "after": "    return total\n",
            "instruction": "через sum", **extra}  # fmt: skip
    return hub.post("/v1/editor/edit", json=body)


def test_the_model_edits_the_selection(hub, repo):
    """D47, step 14.7 (Ctrl+I): the role «Правка в редакторе» gets the selection between <SELECTION> marks, the code
    around it, the path and the person's words; its answer, a fence taken off, is the replacement. No model chosen, a
    secret file, a cloud model for a local-only project: 409 with why - and nothing sent. Recorded in the usage."""
    provider = EditProvider("```python\n    return sum(items)\n```")
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    refused = edit(hub, pid)
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "no_model"
    saved = hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": {"edit": "editor/fix"}}})
    assert saved.status_code == 200, saved.text
    done = edit(hub, pid)
    assert done.status_code == 200 and done.json() == {"replacement": "    return sum(items)", "model": "editor/fix"}
    sent = provider.calls[-1][1]["content"]
    assert "What to do: через sum" in sent and "<SELECTION>    total = 0" in sent and "File: src/app.py" in sent
    assert edit(hub, pid, path=".env").json()["detail"]["reason"] == "secret"
    meta = json.loads(hub.app.state.db.call(lambda c: c.execute("SELECT meta FROM usage").fetchone()[0]))
    assert meta["kind"] == "edit"

    provider.models[0] = Model("editor/fix", "editor", "fix", "Fix", "cloud", {}, {})
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    calls = len(provider.calls)
    assert edit(hub, pid).json()["detail"]["reason"] == "local_only" and len(provider.calls) == calls
    assert hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": True}).status_code == 200
    assert edit(hub, pid, selected=f"KEY = '{SECRET}'\n").status_code == 200
    assert SECRET not in json.dumps(provider.calls[-1], ensure_ascii=False)
