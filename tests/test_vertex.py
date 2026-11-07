"""Gemini on Google Cloud's Vertex AI (stage 30): a service account's key file kept as the hub needs it, a token by the
signed JWT, the models the account may call - a free countTokens says so, and a missing role is said at once - and
the answer over the same streamed generateContent as the Gemini API. Google is a mock: nothing leaves this machine."""

import asyncio
import json
import time
from urllib.parse import parse_qsl

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from hub import connections as conns
from hub.gateway.base import ProviderError
from hub.gateway.gemini_provider import _CATALOG
from hub.gateway.vertex_provider import NO_ROLE, TOKEN_MARGIN_S, VertexProvider
from tests.test_connections import hub, row, unlocked  # noqa: F401 - the hub fixture
from tests.test_gemini import collect, sse

PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = PRIVATE.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
FILE = {
    "type": "service_account",
    "project_id": "afo-test",
    "private_key_id": "0123456789abcdef0123456789abcdef0123cafe",
    "private_key": PEM,
    "client_email": "afo@afo-test.iam.gserviceaccount.com",
    "client_id": "123456789012345678901",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "universe_domain": "googleapis.com",
}
MODELS = {
    "publisherModels": [
        {"name": "publishers/google/models/gemini-3.5-flash-lite", "launchStage": "GA"},
        {"name": "publishers/google/models/gemini-2.5-flash", "launchStage": "GA"},
        {"name": "publishers/google/models/gemini-3.5-flash", "launchStage": "GA"},  # no price in this build
        {"name": "publishers/google/models/imagen-4", "launchStage": "GA"},
    ]
}
MODEL_PATH = "/v1/projects/afo-test/locations/global/publishers/google/models/"
DENIED = {
    "error": {
        "code": 403,
        "message": "Permission 'aiplatform.endpoints.predict' denied on resource '//aiplatform.googleapis.com/projects/"
        "afo-test/locations/global/publishers/google/models/gemini-2.5-flash' (or it may not exist).",
        "status": "PERMISSION_DENIED",
    }
}


class Google:
    """Google's token service and Vertex AI as the adapter uses them; it keeps what it was sent."""

    def __init__(self, role=True, answer=""):
        self.role = role
        self.answer = answer
        self.tokens = 0
        self.paths = []
        self.sent = []

    def __call__(self, request):
        if request.url.host == "oauth2.googleapis.com":
            form = dict(parse_qsl(request.content.decode()))
            assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
            # leeway: a test moves its clock an hour ahead
            claims = jwt.decode(form["assertion"], PRIVATE.public_key(), algorithms=["RS256"],
                                audience="https://oauth2.googleapis.com/token", leeway=3600)  # fmt: skip
            assert claims["iss"] == FILE["client_email"] and claims["scope"].endswith("/auth/cloud-platform")
            assert jwt.get_unverified_header(form["assertion"])["kid"] == FILE["private_key_id"]
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"ya29.t-{self.tokens}", "expires_in": 3599})
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.headers["Authorization"] == f"Bearer ya29.t-{self.tokens}"
        self.paths.append(request.url.path)
        if request.url.path == "/v1beta1/publishers/google/models":
            return httpx.Response(200, json=MODELS)
        assert request.url.path.startswith(MODEL_PATH)
        if not self.role:
            return httpx.Response(403, json=DENIED)
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 2})
        assert request.url.params["alt"] == "sse"
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, text=self.answer, headers={"content-type": "text/event-stream"})


class Clock:
    def __init__(self):
        self.now = time.time()  # Google's side checks the JWT's times against the real clock

    def __call__(self):
        return self.now


def vertex(google, kept="", clock=None):
    kept = kept or conns.service_account(json.dumps(FILE, indent=2))
    return VertexProvider(lambda: kept, transport=httpx.MockTransport(google), clock=clock or Clock())


def test_the_key_file_keeps_only_what_signs_in():
    kept = json.loads(conns.service_account("\n" + json.dumps(FILE, indent=2) + "\n"))
    assert kept == {k: FILE[k] for k in ("project_id", "client_email", "private_key_id", "private_key")}
    for wrong in ("AIza-not-a-file-123", json.dumps({**FILE, "type": "authorized_user"}), "[1, 2]"):
        with pytest.raises(ValueError, match="service_account"):
            conns.service_account(wrong)
    with pytest.raises(ValueError, match="скачайте ключ заново"):
        conns.service_account(json.dumps({k: v for k, v in FILE.items() if k != "private_key"}))
    with pytest.raises(ValueError, match="не читается"):
        conns.service_account(json.dumps({**FILE, "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END"}))


def test_the_models_the_account_may_call():
    google, clock = Google(), Clock()
    provider = vertex(google, clock=clock)
    models = asyncio.run(provider.list_models())
    assert [m.id for m in models] == ["vertex/gemini-3.5-flash-lite", "vertex/gemini-2.5-flash"]  # priced ones only
    lite = models[0]
    assert lite.locality == "cloud" and lite.name == "Gemini 3.5 Flash-Lite"
    assert lite.prices == _CATALOG["gemini-3.5-flash-lite"][1][0][1]  # the Gemini API's paid tier
    assert lite.capabilities["prices_asof"] and lite.capabilities["vision"] is True
    assert google.paths[-1] == MODEL_PATH + "gemini-2.5-flash:countTokens"  # the account may call them: free to ask
    asyncio.run(provider.list_models())
    assert google.tokens == 1  # the token lives an hour: kept
    clock.now += 3599 - TOKEN_MARGIN_S + 1
    asyncio.run(provider.list_models())
    assert google.tokens == 2  # close to lapsing: a new one

    assert asyncio.run(VertexProvider(lambda: None, transport=httpx.MockTransport(Google())).list_models()) == []


def test_without_the_role_it_says_what_to_give():
    google = Google(role=False)
    with pytest.raises(ProviderError) as listing:
        asyncio.run(vertex(google).list_models())
    assert listing.value.detail == f"vertex: {NO_ROLE}" and "Vertex AI User" in NO_ROLE
    with pytest.raises(ProviderError, match="Vertex AI User"):
        collect(vertex(google).stream_chat("gemini-2.5-flash", [{"role": "user", "content": "Привет"}], "low",
                                           asyncio.Event()))  # fmt: skip
    with pytest.raises(ProviderError, match="no Vertex AI service account"):
        collect(VertexProvider(lambda: None).stream_chat("gemini-2.5-flash", [], "low", asyncio.Event()))


def test_an_answer_over_vertex():
    answer = sse(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Думаю", "thought": True}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Да."}]}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 2, "thoughtsTokenCount": 5}},
    )  # fmt: skip
    google = Google(answer=answer)
    events = collect(vertex(google).stream_chat("gemini-3.5-flash-lite", [{"role": "user", "content": "Да?"}],
                                                "medium", asyncio.Event()))  # fmt: skip
    assert [(e.kind, e.text) for e in events[:-1]] == [("reasoning", "Думаю"), ("content", "Да.")]
    end = events[-1]
    assert end.usage == {"input_tokens": 12, "cached_input_tokens": 0, "output_tokens": 7}
    assert end.stop_reason == "end_turn" and end.raw["content"]["parts"][-1] == {"text": "Да."}
    assert google.paths[-1] == MODEL_PATH + "gemini-3.5-flash-lite:streamGenerateContent"
    sent = google.sent[0]
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "Да?"}]}]
    assert sent["generationConfig"]["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "medium"}


def test_the_key_file_through_the_connections(hub):  # noqa: F811 - the fixture
    google = Google()
    hub.app.state.gateway.register(
        VertexProvider(lambda: hub.store.get(conns.VERTEX_ACCOUNT), transport=httpx.MockTransport(google))
    )
    headers = unlocked(hub)
    assert row(hub, conns.VERTEX_ACCOUNT)["kind"] == "file"
    wrong = hub.put(f"/v1/connections/{conns.VERTEX_ACCOUNT}", json={"value": "AIza-a-key-not-a-file"}, headers=headers)
    assert wrong.status_code == 422 and "service_account" in wrong.json()["detail"]
    put = hub.put(f"/v1/connections/{conns.VERTEX_ACCOUNT}", json={"value": json.dumps(FILE, indent=2)},
                  headers=headers)  # fmt: skip
    assert put.status_code == 200 and "vertex" not in put.json()["catalog"].get("errors", "")
    assert PEM[40:80] not in put.text
    assert json.loads(hub.store.get(conns.VERTEX_ACCOUNT))["client_email"] == FILE["client_email"]
    assert {"vertex/gemini-3.5-flash-lite", "vertex/gemini-2.5-flash"} <= {
        m["id"] for m in hub.get("/v1/models").json()["models"]
    }
    assert row(hub, conns.VERTEX_ACCOUNT, headers)["tail"] == "cafe"  # the key's id, as the console lists it
    assert row(hub, conns.VERTEX_ACCOUNT)["tail"] == ""
    checked = hub.post(f"/v1/connections/{conns.VERTEX_ACCOUNT}/check", headers=headers).json()
    assert checked["ok"] is True and checked["detail"] == "ключ работает: моделей — 2"

    google.role = False  # the role taken away in the console
    checked = hub.post(f"/v1/connections/{conns.VERTEX_ACCOUNT}/check", headers=headers).json()
    assert checked["ok"] is False and "Vertex AI User" in checked["detail"]
