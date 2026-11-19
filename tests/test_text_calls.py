"""A tool call a local model wrote as text (stage 61.6; the user's complaint of 2026-11-17): what is read as a call and
what stays text, and the whole way through the hub - a real HTTP server in llama-server's place streams the answer."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hub.gateway.openai_compat import OpenAICompatProvider
from hub.gateway.text_calls import found
from tests.test_changes import finished, new_chat
from tests.test_tools import hub  # noqa: F401 - the hub fixture

GIVEN = {"read_file", "web_fetch"}
READ = {"project": "proj", "path": "src/app.py"}


def calls(text, names=GIVEN):
    return [(c["name"], c["input"]) for c in found(text, names)]


def test_a_call_written_as_text_is_read_and_anything_else_stays_text():
    call = json.dumps({"name": "read_file", "arguments": READ})
    fenced = f"Сначала прочту файл.\n```json\n{call}\n```\nPlease confirm."
    assert calls(fenced) == [("read_file", READ)]  # the user's case: steps, a json block, a question
    fetch = {"url": "https://example.org", "start_char": 0}
    marked = f"<tool_call>\n{json.dumps({'name': 'web_fetch', 'arguments': fetch})}\n</tool_call>"
    assert calls(marked) == [("web_fetch", fetch)]
    bare = f"Вызываю {json.dumps({'name': 'read_file', 'parameters': READ})} сейчас."
    assert calls(bare) == [("read_file", READ)]
    as_openai = {"type": "function", "function": {"name": "read_file", "arguments": json.dumps(READ)}}
    openai = json.dumps([as_openai, {"name": "web_fetch", "arguments": {"url": "https://a.b"}}])
    assert calls(openai) == [("read_file", READ), ("web_fetch", {"url": "https://a.b"})]
    twice = f"```json\n{call}\n```\n" * 2
    assert calls(twice) == [("read_file", READ)]  # one call, not two
    assert calls('```json\n{"name": "rm_rf", "arguments": {"path": "/"}}\n```') == []  # a tool it was not given
    assert calls('{"name": "read_file", "arguments": "not json"}') == []
    assert calls('{"name": "read_file", "arguments": ["src/app.py"]}') == []  # its arguments are not an object
    assert calls('Пример конфига: {"name": "proj", "version": 2} и словарь {1: 2}.') == []
    assert calls("Обычный ответ без вызовов [1], {скобки}.") == []
    assert calls(fenced, names=set()) == []
    assert calls(fenced + " " * 60_000) == []  # a long answer is prose
    ids = [c["id"] for c in found(twice + bare.replace("read_file", "web_fetch"), GIVEN)]
    assert len(ids) == len(set(ids)) and all(i.startswith("text_call_") for i in ids)


class TextualServer:
    """llama-server's place: a model that writes its call as a json block in its first answer - no `tool_calls` -
    and answers after it got the result. Each request's body is kept."""

    FIRST = f"Прочитаю файл.\n```json\n{json.dumps({'name': 'read_file', 'arguments': READ})}\n```\nPlease confirm."

    def __init__(self):
        self.bodies = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args) -> None:
                pass

            def do_GET(self) -> None:
                data = json.dumps({"data": [{"id": "coder"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.bodies.append(body)
                answered = any(m["role"] == "tool" for m in body["messages"])
                text = "Файл прочитан." if answered else TextualServer.FIRST
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
                    chunk = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def textual():
    server = TextualServer()
    yield server
    server.close()


def test_a_local_models_call_written_as_text_is_run_by_the_hub(hub, repo, textual):  # noqa: F811
    """The complaint: a local model wrote its call as text and asked to confirm - nothing ran. The hub reads the call
    from the text, runs it under the usual rights (reading a file is not asked), and the model gets the result."""
    hub.app.state.gateway.register(OpenAICompatProvider("textual", textual.url))
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    conv = new_chat(hub, pid)
    asking = {"content": "прочитай app.py", "model_id": "textual/coder"}
    sent = hub.post(f"/v1/conversations/{conv}/messages", json=asking)
    assert sent.status_code == 202
    finished(hub, conv)
    assert len(textual.bodies) == 2  # the call ran, and the model answered after its result
    told = json.dumps(textual.bodies[0]["messages"], ensure_ascii=False)  # the context joins a user turn here
    assert "do not ask the user in words to confirm it" in told  # the model is told not to ask in words
    second = textual.bodies[1]["messages"]
    asked = next(m for m in second if m["role"] == "assistant")
    assert [c["function"]["name"] for c in asked["tool_calls"]] == ["read_file"]
    result = next(m for m in second if m["role"] == "tool")
    assert "print('hi')" in result["content"] and result["tool_call_id"] == asked["tool_calls"][0]["id"]
    messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
    assert [b["name"] for m in messages for b in m["content"] if b.get("type") == "tool_call"] == ["read_file"]
    assert hub.get("/v1/approvals").json()["approvals"] == []
