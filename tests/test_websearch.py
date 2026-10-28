"""web_search (stage 5.4, D19, Q13): Brave Search by its key or the user's SearXNG by its address, asked every time,
and a chat that searched counts as one that has read the web. The engines are mocks: nothing leaves this machine."""

import asyncio
import json

import httpx
import pytest

from hub import connections as conns
from hub import websearch
from hub.db import Database, migrate
from hub.secrets import MemorySecretStore
from tests.test_changes import edit, finished, new_chat, result_of, say, setup_chat, wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture

BRAVE = {
    "web": {
        "results": [
            {"title": "Python <strong>difflib</strong>", "url": "https://docs.python.org/3/library/difflib.html",
             "description": "Helpers for computing <strong>deltas</strong> &amp; diffs.", "extra_snippets": ["More."]},
            {"title": "Leak", "url": "https://leak.example", "description": "key sk-ant-api03-" + "k" * 40},
            {"title": "no address"},
        ]
    }
}  # fmt: skip


@pytest.fixture
def engines(monkeypatch):
    seen = []

    def answer(request):
        seen.append(request)
        if request.url.host == "api.tavily.com":
            if request.headers.get("Authorization") != "Bearer tvly-good":
                return httpx.Response(432 if request.headers.get("Authorization") == "Bearer tvly-spent" else 401)
            body = json.loads(request.content)
            assert body == {"query": body["query"], "max_results": body["max_results"], "search_depth": "basic"}
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "results": [
                        {
                            "title": "Tavily <b>hit</b>",
                            "url": "https://tv.example",
                            "content": "from Tavily",
                            "score": 0.9,
                        }
                    ],
                },
            )
        if request.url.host == "api.search.brave.com":
            if request.headers.get("X-Subscription-Token") != "BSA-good-key":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(200, json=BRAVE)
        if request.url.params.get("format") != "json" or request.url.port == 9999:
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json={"results": [{"title": "SX", "url": "https://sx.example", "content": "c"}]})

    monkeypatch.setattr(websearch, "TRANSPORT", httpx.MockTransport(answer))
    return seen


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    database.call(migrate)
    yield database
    database.close()


def at(db, url):
    db.call(lambda conn: conns.set_address(conn, conns.SEARXNG_URL, url))
    return db


def test_brave_results_become_text(engines, db):
    store = MemorySecretStore({conns.BRAVE_SEARCH_KEY: "BSA-good-key"})
    at(db, "http://127.0.0.1:8888")
    assert run(websearch.engines(store, db)) == ["searxng", "brave"]  # the free one first
    found = run(websearch.search("difflib", engine="brave", store=store, db=db, count=5))
    assert found[0] == {"title": "Python difflib", "url": "https://docs.python.org/3/library/difflib.html",
                        "snippet": "Helpers for computing deltas & diffs. More."}  # fmt: skip
    assert "[REDACTED]" in found[1]["snippet"] and len(found) == 2  # secrets masked, a result without address out
    assert engines[0].url.params["q"] == "difflib" and engines[0].url.params["count"] == "5"
    text = websearch.as_text("difflib", "brave", found)
    assert text.startswith("Results for «difflib» (Brave Search):\n1. Python difflib\n   https://docs.python.org/")

    store.set(conns.BRAVE_SEARCH_KEY, "BSA-wrong")
    with pytest.raises(websearch.SearchError, match="refused the key"):
        run(websearch.search("x", engine="brave", store=store, db=db))


def test_searxng_and_no_engine(engines, db):
    store = MemorySecretStore()
    assert run(websearch.engine(store, db)) is None
    assert run(websearch.engine(store, at(db, "http://127.0.0.1:8888"))) == "searxng"
    found = run(websearch.search("q", engine="searxng", store=store, db=db))
    assert found == [{"title": "SX", "url": "https://sx.example", "snippet": "c"}]
    with pytest.raises(websearch.SearchError, match="enable `json`"):
        run(websearch.search("q", engine="searxng", store=store, db=at(db, "http://127.0.0.1:9999")))
    assert websearch.as_text("q", "searxng", []) == "No results for «q» (SearXNG)."


def search_call(query="difflib cutoff"):
    return {"id": "s1", "name": "web_search", "input": {"query": query}}


def test_a_search_is_asked_and_taints_the_chat(hub, repo, engines):  # noqa: F811
    hub.app.state.secrets.set(conns.BRAVE_SEARCH_KEY, "BSA-good-key")
    steps = [[search_call()], [{"id": "w1", "name": "edit_file", "input": edit()}]]
    pid, conv, provider = setup_chat(hub, repo, steps[:1])
    say(hub, conv)
    asked = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert asked["tool"] == "web_search" and asked["kind"] == "network"
    assert asked["preview"] == {"kind": "text", "text": "difflib cutoff", "note": "Brave Search"}
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": True})
    finished(hub, conv)
    # a trusted chat searches without a card (2026-10-28) - but then its edit is asked: it has searched the web (D19)
    provider.steps += steps
    conv = new_chat(hub, pid, "trusted")
    say(hub, conv)
    write = wait(lambda: hub.get("/v1/approvals").json()["approvals"])[0]
    assert write["tool"] == "edit_file"
    hub.post(f"/v1/approvals/{write['id']}", json={"allow": True})
    messages = finished(hub, conv)
    results = [b for m in messages if m["role"] == "tool" for b in m["content"]]
    assert results[0]["error"] is False and "https://docs.python.org/3/library/difflib.html" in results[0]["output"]


def test_without_an_engine_the_model_hears_how_to_get_one(hub, repo, engines):  # noqa: F811
    _pid, conv, _provider = setup_chat(hub, repo, [[search_call()]])
    say(hub, conv)
    result = result_of(finished(hub, conv))
    assert result["error"] is True and "no search engine is connected" in result["output"]
    assert hub.get("/v1/approvals").json()["approvals"] == []  # nothing to ask about
    assert engines == []


def test_tavily_and_the_next_engine_when_one_fails(engines, db):
    store = MemorySecretStore({conns.TAVILY_API_KEY: "tvly-good"})
    found = run(websearch.search("q", engine="tavily", store=store, db=db, count=3))
    assert found == [{"title": "Tavily hit", "url": "https://tv.example", "snippet": "from Tavily"}]
    store.set(conns.TAVILY_API_KEY, "tvly-spent")
    with pytest.raises(websearch.SearchError, match="used up"):
        run(websearch.search("q", engine="tavily", store=store, db=db))

    # SearXNG first; it refuses, Tavily answers
    at(db, "http://127.0.0.1:9999")
    store.set(conns.TAVILY_API_KEY, "tvly-good")
    assert run(websearch.engines(store, db)) == ["searxng", "tavily"]
    answered, found = run(websearch.search_any("q", store=store, db=db))
    assert answered == "tavily" and found[0]["url"] == "https://tv.example"
    store.set(conns.TAVILY_API_KEY, "tvly-wrong")  # both fail: the model hears both reasons
    with pytest.raises(websearch.SearchError, match=r"enable `json`.*; Tavily refused the key"):
        run(websearch.search_any("q", store=store, db=db))
    with pytest.raises(websearch.SearchError, match="no search engine is connected"):
        run(websearch.search_any("q", store=MemorySecretStore(), db=_empty(db)))


def _empty(db):
    db.call(lambda conn: conns.set_address(conn, conns.SEARXNG_URL, None))
    return db
