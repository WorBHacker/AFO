"""A conversation longer than the model's window still gets an answer (D14, D22): the request is shortened - stale
looks at a window always, then the oldest tool results, then long old texts - and the last messages, the system
prompt and the stored conversation stay whole; the model's own count corrects the guess, and a request it still
refuses as too long goes again, shorter. An agent's runs looking at Telegram outgrew Flash-Next's 64K twice
(2026-10-28: 66 855 and 69 627 tokens)."""

from hub import fit, store
from hub.gateway.base import Model, ProviderError, StreamEvent
from tests.test_chat import WS_HOST, auth, drain_until, hub, register_fake  # noqa: F401 - the hub fixture


def long_run(results: int, size: int, name: str = "read_file") -> list[dict]:
    prompt = [{"role": "system", "content": "Ты агент."}, {"role": "user", "content": "найди чат"}]
    for n in range(results):
        prompt.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "name": name,
                                                                           "input": {}}]})  # fmt: skip
        prompt.append({"role": "tool", "tool_call_id": f"c{n}", "name": name, "content": "x" * size})
    return prompt


def test_stale_looks_at_a_window_always_give_way():
    prompt = long_run(6, 20000, "screen_look")  # six looks at Telegram, 20 000 characters each
    out, shortened = fit.fit(prompt, window=None)
    looks = [e["content"] for e in out if e["role"] == "tool"]
    assert shortened == 4
    assert all(c.startswith("[screen_look: прежний осмотр окна (20000 знаков) убран") for c in looks[:4])
    assert looks[4:] == ["x" * 20000] * 2  # the last two looks go whole
    assert fit.estimate(out) < fit.estimate(prompt) / 2 and prompt[3]["content"] == "x" * 20000  # the caller's: as is


def test_the_oldest_tool_results_give_way_first():
    prompt = long_run(38, 6000)  # some 76 000 "tokens" of results
    assert fit.fit(prompt, window=None) == (prompt, 0)  # no looks, a window not known: as it is
    assert fit.fit(prompt[:4], window=65536) == (prompt[:4], 0)  # it fits: as it is
    out, shortened = fit.fit(prompt, window=65536)
    assert sum(fit.tokens(e) for e in out) <= fit.room(65536) and shortened > 0
    assert len(out) == len(prompt) and [e["role"] for e in out] == [e["role"] for e in prompt]  # pairs kept
    assert out[3]["content"].startswith("[read_file: прежний результат (6000 знаков) убран")
    assert out[-fit.KEEP_TAIL :] == prompt[-fit.KEEP_TAIL :] and out[0] == prompt[0]  # the last ones, the system
    assert prompt[3]["content"] == "x" * 6000  # the caller's prompt is not changed
    tighter, more = fit.fit(prompt, window=65536, scale=1.5)  # the model counts 1.5 times what the guess says
    assert more > shortened and sum(fit.tokens(e) for e in tighter) * 1.5 <= fit.room(65536)

    talk = [{"role": "system", "content": "s"}] + [
        {"role": r, "content": "слово " * 3000, "provider": "anthropic", "provider_raw": {"x": 1}}
        for r in ("user", "assistant") * 6
    ]
    out, shortened = fit.fit(talk, window=20000)
    first = out[1]
    assert "[…]" in first["content"] and "provider_raw" not in first and shortened >= 1
    assert out[-1] == talk[-1]


def test_the_models_own_count_and_its_refusal():
    prompt = long_run(3, 3000)
    guessed = fit.estimate(prompt)
    assert fit.calibrate(guessed * 2, prompt) == 2.0 and fit.calibrate(guessed // 2, prompt) == 1.0
    assert fit.calibrate(0, prompt) == 1.0
    said = ('llama-swap: HTTP 400: {"error": {"type": "invalid_request_error", "message": "request exceeds the '
            'context window: prompt (69627 tokens) leaves no room to answer in the context (65536)"}}')  # fmt: skip
    assert fit.overflow(said) == 69627
    assert fit.overflow("anthropic: prompt is too long: 210000 tokens > 200000 maximum") == 210000
    assert fit.overflow("openai: This model's maximum context length is 128000 tokens") == 0  # said, not counted
    assert fit.overflow("llama-swap: 502 upstream command exited prematurely") is None
    llama_cpp = ('llama-swap: HTTP 400: {"error":{"code":400,"message":"request (15961 tokens) exceeds the available '
                 'context size (8192 tokens), try increasing it","type":"exceed_context_size_error",'
                 '"n_prompt_tokens":15961,"n_ctx":8192}}')  # fmt: skip
    assert fit.overflow(llama_cpp) == 15961  # its own words (2026-10-28): the request goes again, shorter


def test_a_small_window_cuts_the_last_result_too():
    """2026-10-28: an agent on an 8K model looked at a whole Telegram chat (250 elements, ~6800 tokens) right after
    a few short steps; nothing older was long enough to give way, so the last look keeps its beginning and end."""
    prompt = [
        *long_run(6, 200),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "look", "name": "screen_look", "input": {}}]},
        {"role": "tool", "tool_call_id": "look", "name": "screen_look",
         "content": "начало осмотра " + "элемент " * 2500 + " конец осмотра"},
    ]  # fmt: skip
    out, shortened = fit.fit(prompt, window=8192)
    last = out[-1]["content"]
    assert shortened >= 1 and fit.estimate(out) <= fit.room(8192)
    assert last.startswith("начало осмотра") and last.endswith("конец осмотра") and "урезано" in last
    assert "screen_look с part= или find=" in last and prompt[-1]["content"].count("элемент") == 2500
    starved, _ = fit.fit(prompt, window=8192, tools=[{"description": "x" * 21000}])  # tools take the whole room
    assert len(starved[-1]["content"]) >= fit.LAST_MIN  # the last result never goes below its minimum


def test_a_chat_past_its_window_still_asks_the_model(hub):  # noqa: F811
    provider = register_fake(hub, models=[Model("fake/echo", "fake", "echo", "Echo", "local", {"context": 12000}, {})])
    conv = hub.post("/v1/conversations", headers=auth(hub), json={"title": "t"}).json()["id"]

    def grow(conn):
        for n in range(20):
            store.add_message(conn, conv, "assistant", [{"type": "tool_call", "id": f"c{n}", "name": "read_file",
                                                         "input": {"path": "a.py"}}], model_id="fake/echo")  # fmt: skip
            store.add_message(conn, conv, "tool", [{"type": "tool_result", "id": f"c{n}", "name": "read_file",
                                                    "output": "строка кода\n" * 300}])  # fmt: skip

    hub.app.state.db.call(grow)
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "и?", "model_id": "fake/echo"}
        )
        drain_until(ws, "chat.done")
    sent = provider.last_prompt
    assert sum(fit.tokens(m) for m in sent) <= fit.room(12000)  # with the tool definitions: within the window
    assert sent[-2]["content"].startswith("строка кода")  # the very last result stays whole
    assert any("убран, чтобы разговор поместился" in str(m.get("content")) for m in sent)
    stored = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert any(b.get("output", "").startswith("строка кода") for m in stored for b in m["content"])  # kept whole


class Strict:
    """A model whose tokenizer counts more than the guess: it refuses a request it finds too long, as llama.cpp does."""

    name = "strict"

    def __init__(self, window: int):
        self.window = window
        self.sent: list[int] = []

    async def list_models(self):
        return [Model("strict/m", "strict", "m", "Strict", "local", {"context": self.window}, {})]

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        counted = int(fit.estimate(messages, tools) * 1.6)  # its own count: 1.6 times the guess
        self.sent.append(counted)
        if counted > fit.room(self.window):
            raise ProviderError(f"strict: request exceeds the context window: prompt ({counted} tokens) leaves no "
                                f"room to answer in the context ({self.window})")  # fmt: skip
        yield StreamEvent("content", "готово")
        yield StreamEvent("end", usage={"input_tokens": counted, "output_tokens": 2})


def test_a_request_refused_as_too_long_goes_again_shorter(hub):  # noqa: F811
    provider = Strict(16000)
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={"title": "t"}).json()["id"]

    def grow(conn):
        for n in range(14):
            store.add_message(conn, conv, "assistant", [{"type": "tool_call", "id": f"c{n}", "name": "read_file",
                                                         "input": {}}], model_id="strict/m")  # fmt: skip
            store.add_message(conn, conv, "tool", [{"type": "tool_result", "id": f"c{n}", "name": "read_file",
                                                    "output": "строка кода\n" * 300}])  # fmt: skip

    hub.app.state.db.call(grow)
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "и?", "model_id": "strict/m"}
        )
        drain_until(ws, "chat.done")  # no chat.error: the second request fitted
    assert len(provider.sent) == 2 and provider.sent[1] < provider.sent[0]
    assert provider.sent[1] <= fit.room(16000)
