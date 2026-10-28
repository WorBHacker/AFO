"""Models set in the window (stage 8, D39): what is checked, versions, the catalog as the user set it (names, hidden,
prices that the cost follows), each provider taking only its own parameters into its own request, the chat and the
OpenAI proxy sending them (the client's over the model's), and the API - the desktop app's alone to change."""

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import model_settings, store
from hub.auth import create_client
from hub.db import Database, migrate
from hub.gateway.base import Model, ProviderError, StreamEvent
from hub.gateway.cloud_openai import DeepSeekProvider, OpenRouterProvider
from hub.gateway.openai_compat import OpenAICompatProvider
from hub.model_settings import SettingsError
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
WS_HOST = {"host": "127.0.0.1:8765"}


def test_what_is_checked():
    params = {"temperature": 0.3, "top_k": 40, "stop": ["</end>"], "thinking": False, "top_p": None}
    good = model_settings.check({"hidden": False, "name": "  Мой   кодер ", "capabilities": {"context": 65536,
                                 "tools": True}, "prices": {"input": 1, "output": 2.5}, "params": params,
                                 "preset": "precise", "effort": "high",
                                 "system_prompt": " Отвечай по-русски. "})  # fmt: skip
    assert good["name"] == "Мой кодер" and "hidden" not in good and good["system_prompt"] == "Отвечай по-русски."
    assert good["params"] == {"temperature": 0.3, "top_k": 40, "stop": ["</end>"], "thinking": False}
    assert good["prices"] == {"input": 1.0, "output": 2.5} and len(good["prices_asof"]) == 10  # dated today
    for bad, said in (({"params": {"temperature": 3}}, "от 0 до 2"), ({"params": {"top_k": 2.5}}, "целое"),
                      ({"params": {"seed": 1}}, "неизвестный"), ({"params": {"stop": ["a"] * 5}}, "не больше 4"),
                      ({"hidden": "yes"}, "да или нет"), ({"preset": "wild"}, "заготовка"),
                      ({"capabilities": {"context": 10}}, "от 512"), ({"colour": "red"}, "неизвестная настройка"),
                      ({"prices": {"input": -1}}, "от 0")):  # fmt: skip
        with pytest.raises(SettingsError, match=said):
            model_settings.check(bad)
    # a preset fills only what the provider takes; what goes out is cut to it too
    assert model_settings.preset_params("precise", {"temperature", "max_tokens"}) == {"temperature": 0.2}
    assert model_settings.clean_params({"temperature": 0.2, "top_k": 9}, {"max_tokens", "stop"}) == {}


def test_versions_and_the_catalog_as_the_user_set_it(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    paid = Model("x/paid", "x", "paid", "Paid", "cloud", {"context": 8000, "vision": False}, {"input": 1.0,
                 "output": 2.0})  # fmt: skip
    db.call(lambda c: store.upsert_model(c, paid))
    assert db.call(lambda c: model_settings.current(c, "x/paid")) == (0, {})
    first = db.call(lambda c: model_settings.save(c, "x/paid", {"name": "Дорогая", "hidden": True}, by="app"))
    again = db.call(lambda c: model_settings.save(c, "x/paid", {"name": "Дорогая", "hidden": True}, by="app"))
    second = db.call(lambda c: model_settings.save(c, "x/paid", {"name": "Дорогая", "capabilities": {"vision": True},
                                                                   "prices": {"output": 4.0}}, by="app"))  # fmt: skip
    assert (first, again, second) == (1, 1, 2)  # the same settings again make no version
    row = db.call(lambda c: store.get_model(c, "x/paid"))
    assert (row["name"], row["provider_name"], row["hidden"], row["settings_version"]) == ("Дорогая", "Paid", False, 2)
    assert row["capabilities"]["vision"] is True and row["capabilities"]["context"] == 8000  # over, not instead
    assert row["prices"] == {"input": 1.0, "output": 4.0} and row["capabilities"]["prices_asof"]
    assert db.call(lambda c: store.list_models(c))[0]["name"] == "Дорогая"
    past = db.call(lambda c: model_settings.history(c, "x/paid"))
    assert [p["version"] for p in past] == [2, 1] and past[1]["settings"]["hidden"] is True
    assert db.call(lambda c: model_settings.version(c, "x/paid", 1)) == {"name": "Дорогая", "hidden": True}
    db.close()


def test_each_provider_takes_its_own_parameters():
    llama = OpenAICompatProvider("llama-swap", "http://127.0.0.1:9")
    params = {"temperature": 0.2, "top_p": 0.9, "top_k": 20, "min_p": 0.05, "repeat_penalty": 1.1, "max_tokens": 512,
              "stop": ["###"], "thinking": False}  # fmt: skip
    assert llama.accepts("coder") == frozenset(params)
    body = llama.request_body("coder", [{"role": "user", "content": "hi"}], "low", None, params)
    assert {k: body[k] for k in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens", "stop")} == {
        k: v for k, v in params.items() if k != "thinking"
    }
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    plain = llama.request_body("coder", [{"role": "user", "content": "hi"}], "low", None)
    assert "temperature" not in plain and "chat_template_kwargs" not in plain  # nothing set: the server's own

    deepseek = DeepSeekProvider(lambda: "sk")
    assert deepseek.accepts("deepseek-chat") == {"temperature", "top_p", "max_tokens", "stop", "thinking"}
    off = deepseek.request_body("deepseek-chat", [{"role": "user", "content": "hi"}], "high", None,
                                {"temperature": 0.5, "thinking": False})  # fmt: skip
    assert off["thinking"] == {"type": "disabled"} and off["temperature"] == 0.5 and "reasoning_effort" not in off
    on = deepseek.request_body("deepseek-chat", [{"role": "user", "content": "hi"}], "high", None)
    assert on["thinking"] == {"type": "enabled"}

    router = OpenRouterProvider(lambda: "sk", lambda: asyncio.sleep(0, []))
    assert "thinking" not in router.accepts("x/y")
    body = router.request_body("x/y", [{"role": "user", "content": "hi"}], "high", None, {"repeat_penalty": 1.2})
    assert body["repetition_penalty"] == 1.2 and "repeat_penalty" not in body


def test_stratas_refusal_vector_is_switched_only_where_it_runs_as_a_vector():
    """2026-10-28 (the user's word): a Strata model whose abliteration is a vector applied at runtime (llama-swap's
    metadata: engine strata, abliteration runtime ...) takes «refusal_vector» - sent as Strata's per-request
    experimental_speed_projection; one abliterated in its weights does not (nothing to switch)."""
    import httpx

    listed = {"data": [
        {"id": "coder", "name": "Coder", "meta": {"llamaswap": {
            "engine": "strata", "abliteration": "runtime refusal-vector projection (layers 4-44)", "context": 65536}}},
        {"id": "flash-next", "name": "Flash", "meta": {"llamaswap": {
            "engine": "strata", "abliteration": "weights (SC117 GSQ-RCO transplant)"}}},
        {"id": "own", "name": "Own", "meta": {"llamaswap": {"refusal_vector": True}}},
        {"id": "plain", "name": "Plain", "meta": {"llamaswap": {"engine": "llama.cpp"}}}]}  # fmt: skip
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=listed))
    llama = OpenAICompatProvider("llama-swap", "http://127.0.0.1:9", transport=transport)
    assert "refusal_vector" not in llama.accepts("coder")  # not listed yet: not known
    models = {m.model_ref: m for m in asyncio.run(llama.list_models())}
    assert models["coder"].capabilities["refusal_vector"] is True and models["own"].capabilities["refusal_vector"]
    assert (
        "refusal_vector" not in models["flash-next"].capabilities
        and "refusal_vector" not in models["plain"].capabilities
    )
    assert "refusal_vector" in llama.accepts("coder") and "refusal_vector" not in llama.accepts("flash-next")
    hi = [{"role": "user", "content": "hi"}]
    assert (
        llama.request_body("coder", hi, "low", None, {"refusal_vector": False})["experimental_speed_projection"]
        is False
    )
    assert (
        llama.request_body("coder", hi, "low", None, {"refusal_vector": True})["experimental_speed_projection"] is True
    )
    assert "experimental_speed_projection" not in llama.request_body("coder", hi, "low", None)  # not set: Strata's own
    assert "experimental_speed_projection" not in llama.request_body("flash-next", hi, "low", None,
                                                                     {"refusal_vector": False})  # fmt: skip
    assert model_settings.check_params({"refusal_vector": False}) == {"refusal_vector": False}
    with pytest.raises(SettingsError, match="снятие отказов"):
        model_settings.check_params({"refusal_vector": "off"})


def test_claude_openai_and_gemini_requests():
    from tests import test_anthropic as anth
    from tests import test_gemini as gem
    from tests import test_openai_responses as oai

    client = anth.FakeClient(stream=anth.FakeStream(anth.default_events(), anth.message([anth.SIGNED])))
    claude = anth.provider(client)
    assert claude.accepts("claude-opus-5-5") == {"max_tokens", "stop"}  # no temperature (D15)
    asyncio.run(_drain(claude.stream_chat("claude-opus-5-5", [{"role": "user", "content": "hi"}], "high",
                                          asyncio.Event(), params={"max_tokens": 900, "stop": ["END"]})))  # fmt: skip
    assert client.requests[0]["max_tokens"] == 900 and client.requests[0]["stop_sequences"] == ["END"]

    api = oai.Api(oai.events({"type": "response.completed", "response": {"status": "completed", "output": [],
                                                                         "usage": {}}}))  # fmt: skip
    openai = oai.provider(api)
    assert openai.accepts("gpt-5.5") == {"max_tokens"}
    oai.collect(openai.stream_chat("gpt-5.5", [{"role": "user", "content": "hi"}], "low", asyncio.Event(),
                                   params={"max_tokens": 300}))  # fmt: skip
    assert api.sent[0]["max_output_tokens"] == 300 and "temperature" not in api.sent[0]

    gapi = gem.Api(gem.sse({"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}))
    gemini = gem.provider(gapi)
    assert gemini.accepts("gemini-3-pro") == {"temperature", "top_p", "top_k", "max_tokens", "stop"}
    gem.collect(gemini.stream_chat("gemini-3-pro", [{"role": "user", "content": "hi"}], "low", asyncio.Event(),
                                   params={"temperature": 0.4, "top_k": 30, "stop": ["Z"]}))  # fmt: skip
    config = gapi.sent[0]["generationConfig"]
    assert (config["temperature"], config["topK"], config["stopSequences"]) == (0.4, 30, ["Z"])


async def _drain(gen):
    return [e async for e in gen]


# ---------------------------------------------------------------- through the hub


class Tunable:
    """A provider that takes a temperature, a length and stop sequences, and remembers what it was sent."""

    name = "tune"

    def __init__(self):
        self.sent = []
        self.models = [Model("tune/m", "tune", "m", "Tune", "cloud", {}, {"input": 1.0, "output": 2.0})]

    async def list_models(self):
        return self.models

    def accepts(self, model_ref):
        return frozenset({"temperature", "max_tokens", "stop"})

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None, params=None):
        self.sent.append({"messages": messages, "effort": effort, "params": params})
        yield StreamEvent("content", "да, на связи")
        yield StreamEvent(
            "end", usage={"input_tokens": 1_000_000, "output_tokens": 10}, stats={"tok_s": 99.0, "ttft_s": 0.2}
        )


@pytest.fixture
def hub():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url=BASE) as client:
        client.token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.headers["Authorization"] = f"Bearer {client.token}"
        provider = Tunable()
        app.state.gateway.register(provider)
        client.portal.call(app.state.gateway.sync_catalog)
        client.provider = provider
        yield client


def test_the_api_is_the_apps_to_change(hub):
    read = hub.get("/v1/model_settings", params={"model_id": "tune/m"}).json()
    assert (read["version"], read["settings"], read["accepts"]) == (0, {}, ["max_tokens", "stop", "temperature"])
    assert set(read["presets"]) == {"precise", "balanced", "creative"} and "thinking" in read["params"]
    saved = hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {
        "name": "Мой тюнер", "params": {"temperature": 0.1, "top_k": 5}, "system_prompt": "Кратко."}})  # fmt: skip
    assert saved.status_code == 200 and saved.json()["version"] == 1 and saved.json()["model"]["name"] == "Мой тюнер"
    assert hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {"params": {"temperature": 9}}}
                   ).status_code == 422  # fmt: skip
    assert hub.put("/v1/model_settings", json={"model_id": "nope/x", "settings": {}}).status_code == 404
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {"hidden": True}})
    listed = {m["id"]: m for m in hub.get("/v1/models").json()["models"]}
    assert listed["tune/m"]["hidden"] and listed["tune/m"]["accepts"] == ["max_tokens", "stop", "temperature"]
    back = hub.post("/v1/model_settings/revert", json={"model_id": "tune/m", "version": 1}).json()
    assert back["version"] == 3 and back["settings"]["name"] == "Мой тюнер" and len(back["history"]) == 3
    general = hub.put("/v1/model_settings", json={"model_id": "", "settings": {"system_prompt": "Ты помощник.",
                                                                                "name": "игнор"}}).json()  # fmt: skip
    assert general["settings"] == {"system_prompt": "Ты помощник."} and general["accepts"] == []

    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    headers = {"Authorization": f"Bearer {other}"}
    assert hub.get("/v1/model_settings", params={"model_id": "tune/m"}, headers=headers).status_code == 200
    assert hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {}}, headers=headers).status_code \
        == 403  # fmt: skip
    assert hub.post("/v1/models/check", json={"model_id": "tune/m"}, headers=headers).status_code == 403


def test_the_chat_and_the_check_send_the_settings_and_remember_them(hub):
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {
        "params": {"temperature": 0.3, "top_k": 7, "thinking": False}, "prices": {"input": 2.0}}})  # fmt: skip
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={"Authorization": f"Bearer {hub.token}", **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "tune/m"})
        while ws.receive_json()["type"] != "chat.done":
            pass
    assert hub.provider.sent[-1]["params"] == {"temperature": 0.3}  # only what it takes
    meta, cost = hub.app.state.db.call(lambda c: c.execute(
        "SELECT meta, cost_usd FROM usage WHERE conversation_id = ?", (conv,)).fetchone())  # fmt: skip
    assert json.loads(meta)["params"] == {"temperature": 0.3} and json.loads(meta)["settings_version"] == 1
    assert cost == pytest.approx(2.0 + 10 * 2.0 / 1e6)  # the user's input price, the provider's output price

    checked = hub.post("/v1/models/check", json={"model_id": "tune/m"}).json()
    assert checked["ok"] and checked["text"] == "да, на связи" and checked["tok_s"] == 99.0
    assert checked["params"] == {"temperature": 0.3} and checked["cost_usd"] > 0


def test_the_proxy_sends_the_clients_parameters_over_the_models(hub):
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {
        "params": {"temperature": 0.3, "max_tokens": 100}, "effort": "high"}})  # fmt: skip
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
    headers = {"Authorization": f"Bearer {cline}"}
    body = {"model": "tune/m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.9, "stop": "END"}
    assert hub.post("/v1/openai/chat/completions", json=body, headers=headers).status_code == 200
    asked = hub.provider.sent[-1]
    assert asked["params"] == {"temperature": 0.9, "max_tokens": 100, "stop": ["END"]} and asked["effort"] == "high"
    bad = hub.post("/v1/openai/chat/completions", json={**body, "temperature": 7}, headers=headers)
    assert bad.status_code == 400 and "от 0 до 2" in bad.json()["error"]["message"]


def test_system_prompts_in_order_and_the_chats_preset(hub):
    hub.put("/v1/model_settings", json={"model_id": "", "settings": {"system_prompt": "Общий."}})
    own = {"system_prompt": "Модели.", "params": {"temperature": 0.2}}
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": own})
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    context = hub.put(f"/v1/conversations/{conv}/context", json={"instructions": " Чата. ", "preset": "creative"})
    assert (context.json()["instructions"], context.json()["preset"]) == ("Чата.", "creative")
    assert hub.put(f"/v1/conversations/{conv}/context", json={"preset": "wild"}).status_code == 422
    with hub.websocket_connect("/v1/events", headers={"Authorization": f"Bearer {hub.token}", **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "tune/m"})
        while ws.receive_json()["type"] != "chat.done":
            pass
    sent = hub.provider.sent[-1]
    # in front of the conversation: general -> the model's -> the chat's (an agent's role comes later, D39)
    assert sent["messages"][0] == {"role": "system", "content": "Общий.\n\nМодели.\n\nЧата."}
    assert sent["messages"][1]["role"] == "user"
    assert sent["params"] == {"temperature": 1.0}  # the chat's preset over the model's, cut to what it takes
    hub.put(f"/v1/conversations/{conv}/context", json={"instructions": "", "preset": ""})
    read = hub.get(f"/v1/conversations/{conv}").json()["context"]
    assert (read["instructions"], read["preset"]) == ("", "")


class Broken:
    """A provider whose model fails - or declines - every time."""

    name = "broken"

    def __init__(self, refuse=False):
        self.refuse = refuse
        self.models = [Model("broken/m", "broken", "m", "Сломанная", "local", {}, {})]

    async def list_models(self):
        return self.models

    def accepts(self, model_ref):
        return frozenset()

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None, params=None):
        if not self.refuse:
            raise ProviderError("broken: 503 the server is overloaded")
        yield StreamEvent("end", usage={"input_tokens": 3, "output_tokens": 0}, refusal="не могу об этом говорить")


def send_and_wait(hub, conv, model_id):
    with hub.websocket_connect("/v1/events", headers={"Authorization": f"Bearer {hub.token}", **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": model_id})
        seen = []
        while not seen or seen[-1]["type"] not in ("chat.done", "chat.error"):
            seen.append(ws.receive_json())
    return seen


def test_roles_and_the_spare_model(hub):
    for refuse in (False, True):
        hub.app.state.gateway.register(Broken(refuse))
        hub.portal.call(hub.app.state.gateway.sync_catalog)
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        failed = send_and_wait(hub, conv, "broken/m")  # no spare yet: the error as before
        assert failed[-1]["type"] == "chat.error"
    assert hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": {"fallback": "nope/x"}}}
                   ).status_code == 422  # fmt: skip
    assert hub.put("/v1/model_settings", json={"model_id": "", "settings": {"roles": {"boss": "tune/m"}}}
                   ).status_code == 422  # fmt: skip
    saved = hub.put("/v1/model_settings", json={"model_id": "", "settings": {
        "system_prompt": "Общий.", "roles": {"fallback": "tune/m", "chat": "tune/m", "cards": ""}}}).json()  # fmt: skip
    assert saved["settings"] == {"system_prompt": "Общий.", "roles": {"fallback": "tune/m", "chat": "tune/m"}}
    assert "fallback" in saved["roles"]

    for refuse in (False, True):
        hub.app.state.gateway.register(Broken(refuse))
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        seen = send_and_wait(hub, conv, "broken/m")
        assert seen[-1]["type"] == "chat.done", seen[-1]["payload"]
        switched = next(e["payload"] for e in seen if e["type"] == "chat.fallback")
        assert (switched["model_id"], switched["from"]) == ("tune/m", "broken/m")
        assert ("не могу об этом говорить" if refuse else "503") in switched["reason"]
        assert ("usage" in switched) is refuse  # a declined request still cost its tokens: the window counts them
        answer = hub.get(f"/v1/conversations/{conv}").json()["messages"][-1]
        assert answer["model_id"] == "tune/m" and answer["usage"]["fallback"]["from_name"] == "Сломанная"
        assert hub.provider.sent[-1]["messages"][0] == {"role": "system", "content": "Общий."}  # its own setup
    # the advice counts the user's chat model a little, with a reason
    advice = hub.get("/v1/advice").json()
    tune = next(o for o in advice["options"] if o["model_id"] == "tune/m")
    assert "ваша модель для чатов" in tune["reasons"]


def test_a_models_own_context_budgets_and_tool_portions(hub):
    from hub import limits

    assert limits.limit("map") == 2000 and limits.limit("file_lines") == 300  # the defaults (D22)
    bad = hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {"context_budget": {"map": 10}}})
    assert bad.status_code == 422 and "от 200" in bad.json()["detail"]
    assert hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {"context_budget": {"x": 1}}}
                   ).status_code == 422  # fmt: skip
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {
        "context_budget": {"map": 500, "terminal_lines": 20}}})  # fmt: skip
    read = hub.get("/v1/model_settings", params={"model_id": "tune/m"}).json()
    assert read["limits"]["map"] == {"default": 2000, "low": 200, "high": 200000}

    seen = {}

    async def look():
        await hub.app.state.chat._use_limits("tune/m")
        seen["own"] = (limits.limit("map"), limits.limit("terminal_lines"), limits.limit("live"))
        seen["thread"] = await asyncio.to_thread(lambda: limits.limit("map"))  # the database's threads see it too

    hub.portal.call(look)
    assert seen == {"own": (500, 20, 2000), "thread": 500}
    assert limits.limit("map") == 2000  # another task: the defaults


def test_settings_out_to_a_file_and_back(hub):
    hub.put("/v1/model_settings", json={"model_id": "", "settings": {"system_prompt": "Общий.",
                                                                      "roles": {"fallback": "tune/m"}}})  # fmt: skip
    hub.put("/v1/model_settings", json={"model_id": "tune/m", "settings": {"name": "Тюнер", "params": {
        "temperature": 0.3}}})  # fmt: skip
    data = hub.get("/v1/model_settings/export").json()
    assert data["kind"] == "localchat-model-settings" and data["general"]["roles"] == {"fallback": "tune/m"}
    assert data["models"] == {"tune/m": {"name": "Тюнер", "params": {"temperature": 0.3}}}
    data["models"]["gone/model"] = {"name": "Нет такой"}
    data["models"]["tune/m"]["params"]["temperature"] = 0.9
    data["general"]["roles"]["chat"] = "gone/model"
    data["models"]["tune/x"] = {"params": {"temperature": 9}}
    hub.app.state.db.call(lambda c: store.upsert_model(c, Model("tune/x", "tune", "x", "X", "local", {}, {})))
    report = hub.post("/v1/model_settings/import", json={"data": data}).json()
    assert report["saved"] == ["общие", "tune/m"] and "gone/model" in report["skipped"]
    assert "роль chat: gone/model" in report["skipped"] and "от 0 до 2" in report["errors"]["tune/x"]
    now = hub.get("/v1/model_settings", params={"model_id": "tune/m"}).json()
    assert now["settings"]["params"] == {"temperature": 0.9} and now["history"][0]["by"] == "app (импорт)"
    assert hub.post("/v1/model_settings/import", json={"data": {"models": {}}}).status_code == 422


class Vectored(Tunable):
    """A local model with Strata's refusal vector: it takes «refusal_vector»."""

    name = "vec"

    def __init__(self):
        super().__init__()
        self.models = [Model("vec/m", "vec", "m", "Coder", "local", {"refusal_vector": True}, {})]

    def accepts(self, model_ref):
        return frozenset({"temperature", "refusal_vector"})


def test_the_refusal_vector_set_for_the_model_and_chosen_for_a_task(hub):
    """The model's setting goes with every answer; an autonomous task's own choice wins over it; the window learns
    the setting from the catalog (a new task's switch starts from it)."""
    vec = Vectored()
    hub.app.state.gateway.register(vec)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    listed = {m["id"]: m for m in hub.get("/v1/models").json()["models"]}
    assert listed["vec/m"]["refusal_vector"] is True and "refusal_vector" not in listed["tune/m"]  # on unless set off
    hub.put("/v1/model_settings", json={"model_id": "vec/m", "settings": {"params": {"refusal_vector": False}}})
    assert {m["id"]: m for m in hub.get("/v1/models").json()["models"]}["vec/m"]["refusal_vector"] is False
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={"Authorization": f"Bearer {hub.token}", **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "vec/m"})
        while ws.receive_json()["type"] != "chat.done":
            pass
    assert vec.sent[-1]["params"] == {"refusal_vector": False}  # the model's setting
    body = {"goal": "Ответь одним словом", "model_id": "vec/m", "autonomy": "free"}
    assert hub.post("/v1/runs/autonomous", json={**body, "refusal_vector": "may be"}).status_code == 422
    run_id = hub.post("/v1/runs/autonomous", json={**body, "refusal_vector": True}).json()["id"]
    deadline = time.monotonic() + 20
    while hub.get(f"/v1/runs/{run_id}").json()["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert vec.sent[-1]["params"] == {"refusal_vector": True}  # the task's own choice over the model's
    assert hub.get(f"/v1/runs/{run_id}/plan").json()["refusal_vector"] == 1
    plain = hub.post("/v1/runs/autonomous", json=body).json()["id"]
    assert hub.get(f"/v1/runs/{plain}/plan").json()["refusal_vector"] is None  # not chosen: as the model is set
    hub.post(f"/v1/runs/{plain}/cancel")


def test_a_chats_own_refusal_vector_and_the_cards_draft(hub, tmp_path):
    """2026-10-28 (the user's word): a chat chooses Strata's refusal vector for itself (over the model's setting, ''
    back to it); a project card's draft is written with the model's own parameters now, the switch among them."""
    vec = Vectored()
    hub.app.state.gateway.register(vec)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    hub.put("/v1/model_settings", json={"model_id": "vec/m", "settings": {"params": {"refusal_vector": False,
                                                                                     "temperature": 0.3}}})  # fmt: skip
    conv = hub.post("/v1/conversations", json={}).json()["id"]

    def ask():
        with hub.websocket_connect("/v1/events", headers={"Authorization": f"Bearer {hub.token}", **WS_HOST}) as ws:
            ws.receive_json()
            hub.post(f"/v1/conversations/{conv}/messages", json={"content": "привет", "model_id": "vec/m"})
            while ws.receive_json()["type"] != "chat.done":
                pass
        return vec.sent[-1]["params"]

    assert hub.get(f"/v1/conversations/{conv}").json()["context"]["refusal_vector"] == ""
    assert ask() == {"refusal_vector": False, "temperature": 0.3}  # the model's
    assert hub.put(f"/v1/conversations/{conv}/context", json={"refusal_vector": "maybe"}).status_code == 422
    assert hub.put(f"/v1/conversations/{conv}/context", json={"refusal_vector": "on"}).json()["refusal_vector"] == "on"
    assert ask() == {"refusal_vector": True, "temperature": 0.3}  # the chat's own, over the model's
    assert hub.get(f"/v1/conversations/{conv}").json()["context"]["refusal_vector"] == "on"
    hub.put(f"/v1/conversations/{conv}/context", json={"refusal_vector": ""})
    assert ask() == {"refusal_vector": False, "temperature": 0.3}  # back to the model's

    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "README.md").write_text("# Проект\n", encoding="utf-8")
    pid = hub.post("/v1/projects", json={"path": str(folder)}).json()["id"]
    assert hub.post(f"/v1/projects/{pid}/card/draft", json={"model_id": "vec/m"}).status_code == 202
    deadline = time.monotonic() + 20
    while not hub.get(f"/v1/projects/{pid}/card").json()["draft"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert vec.sent[-1]["params"] == {"refusal_vector": False, "temperature": 0.3}  # the card: the model's settings
