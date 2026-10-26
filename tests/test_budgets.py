"""Budgets (D12): periods, spending per scope, limits; refusing and confirming paid requests; threshold events."""

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import budgets, store
from hub.auth import create_client
from hub.db import Database, migrate
from hub.gateway.base import Model, StreamEvent
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_chat import BASE, WS_HOST, auth, drain_until, register_fake, send

PLUS5 = timezone(timedelta(hours=5))
WEDNESDAY = datetime(2026, 10, 28, 3, 0, tzinfo=PLUS5)  # the week began on Monday the 26th


@pytest.fixture
def db():
    database = Database(data_paths().ensure().db)
    database.call(migrate)
    yield database
    database.close()


def spend(conn, model_id, usd, ts):
    conn.execute("INSERT INTO usage (ts, model_id, cost_usd) VALUES (?, ?, ?)", (ts, model_id, usd))


# --- the module ---


def test_periods_start_at_local_midnight_monday_and_the_first():
    assert budgets.period_start("day", WEDNESDAY) == "2026-10-27T19:00:00.000Z"
    assert budgets.period_start("week", WEDNESDAY) == "2026-10-25T19:00:00.000Z"
    assert budgets.period_start("month", WEDNESDAY) == "2026-09-30T19:00:00.000Z"
    with pytest.raises(ValueError):
        budgets.period_start("year", WEDNESDAY)


def test_spending_is_counted_per_scope_and_period(db):
    def setup(conn):
        spend(conn, "anthropic/claude-opus-5-5", 2.0, "2026-10-28T00:00:00.000Z")  # today (+05:00)
        spend(conn, "anthropic/claude-haiku-5-5", 0.5, "2026-10-26T10:00:00.000Z")  # this week
        spend(conn, "openai/gpt", 1.0, "2026-10-28T01:00:00.000Z")  # today, another provider
        spend(conn, "anthropic/claude-opus-5-5", 4.0, "2026-09-15T00:00:00.000Z")  # last month
        spend(conn, "anthropic-like/x", 9.0, "2026-10-28T01:00:00.000Z")  # a name that only starts the same
        day_all = budgets.upsert_budget(conn, scope="total", period="day", soft_usd=1.0, hard_usd=None)
        week_anthropic = budgets.upsert_budget(
            conn, scope="provider", scope_ref="anthropic", period="week", soft_usd=None, hard_usd=2.5
        )
        return day_all, week_anthropic

    day_all, week_anthropic = db.call(setup)
    assert db.call(lambda c: budgets.spent(c, day_all, WEDNESDAY)) == 12.0
    assert db.call(lambda c: budgets.spent(c, week_anthropic, WEDNESDAY)) == 2.5
    assert week_anthropic.applies_to("anthropic/claude-opus-5-5")
    assert not week_anthropic.applies_to("anthropic-like/x")
    assert not week_anthropic.applies_to("openai/gpt")
    states = {s["period"]: s["state"] for s in db.call(lambda c: budgets.statuses(c, now=WEDNESDAY))}
    assert states == {"day": "soft", "week": "hard"}
    reached = db.call(lambda c: budgets.reached(c, "anthropic/claude-opus-5-5", WEDNESDAY))
    assert [b["scope_ref"] for b in reached] == ["anthropic"]
    assert db.call(lambda c: budgets.reached(c, "openai/gpt", WEDNESDAY)) == []


def test_upsert_replaces_and_validates(db):
    first = db.call(lambda c: budgets.upsert_budget(c, scope="total", period="month", soft_usd=5, hard_usd=10))
    second = db.call(
        lambda c: budgets.upsert_budget(c, scope="total", period="month", soft_usd=7, hard_usd=20, hard_action="block")
    )
    assert second.id == first.id and (second.soft_usd, second.hard_usd, second.hard_action) == (7, 20, "block")
    bad = [
        {"scope": "galaxy", "period": "month", "soft_usd": 1, "hard_usd": None},
        {"scope": "total", "period": "year", "soft_usd": 1, "hard_usd": None},
        {"scope": "provider", "period": "day", "soft_usd": 1, "hard_usd": None},  # no provider name
        {"scope": "total", "period": "day", "soft_usd": None, "hard_usd": None},
        {"scope": "total", "period": "day", "soft_usd": 5, "hard_usd": 1},  # warning above the limit
        {"scope": "total", "period": "day", "soft_usd": -1, "hard_usd": None},
        {"scope": "total", "period": "day", "soft_usd": 1, "hard_usd": 2, "hard_action": "pray"},
    ]
    for kwargs in bad:
        with pytest.raises(ValueError):
            db.call(lambda c, k=kwargs: budgets.upsert_budget(c, **k))
    assert db.call(lambda c: budgets.delete_budget(c, first.id))
    assert not db.call(lambda c: budgets.delete_budget(c, first.id))


def test_crossings_name_only_the_limit_just_passed(db):
    def setup(conn):
        budgets.upsert_budget(conn, scope="total", period="day", soft_usd=1.0, hard_usd=2.0)
        spend(conn, "anthropic/x", 0.9, budgets.period_start("day"))

    db.call(setup)
    assert db.call(lambda c: budgets.crossed(c, "anthropic/x", 0.0)) == []

    def step(conn, usd):
        spend(conn, "anthropic/x", usd, budgets.period_start("day"))
        return [kind for kind, _ in budgets.crossed(conn, "anthropic/x", usd)]

    assert db.call(lambda c: step(c, 0.2)) == ["warning"]  # 0.9 -> 1.1
    assert db.call(lambda c: step(c, 0.2)) == []  # 1.1 -> 1.3: nothing new
    assert db.call(lambda c: step(c, 1.0)) == ["exceeded"]  # 1.3 -> 2.3


# --- through the API ---


@pytest.fixture
def api():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    with TestClient(app, base_url=BASE) as client:
        client.token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


PAID = [Model("fake/echo", "fake", "echo", "Echo", "cloud", {}, {"input": 1.0, "output": 2.0})]


def put_budget(api, **body):
    return api.put("/v1/budgets", headers=auth(api), json=body)


def spend_now(api, usd):
    api.app.state.db.call(
        lambda c: store.record_usage(
            c, client_id=None, conversation_id=None, message_id=None, model_id="fake/echo", usage={}, cost_usd=usd
        )
    )


def test_budgets_api(api):
    register_fake(api, models=PAID)
    saved = put_budget(api, scope="provider", provider="fake", period="day", soft_usd=1, hard_usd=2)
    assert saved.status_code == 200
    assert saved.json()["state"] == "ok" and saved.json()["spent_usd"] == 0
    spend_now(api, 1.5)
    (listed,) = api.get("/v1/budgets", headers=auth(api)).json()["budgets"]
    assert (listed["spent_usd"], listed["state"]) == (1.5, "soft")
    assert put_budget(api, scope="total", period="day", soft_usd=3, hard_usd=1).status_code == 422
    cline = api.app.state.db.call(lambda c: create_client(c, "cline"))
    assert api.put("/v1/budgets", headers={"Authorization": f"Bearer {cline}"}, json={"soft_usd": 1}).status_code == 403
    assert api.delete(f"/v1/budgets/{listed['id']}", headers=auth(api)).status_code == 204
    assert api.delete(f"/v1/budgets/{listed['id']}", headers=auth(api)).status_code == 404


def test_a_reached_budget_refuses_until_confirmed(api):
    register_fake(api, models=PAID)
    put_budget(api, scope="total", period="month", hard_usd=1.0)
    spend_now(api, 1.0)
    conv = api.post("/v1/conversations", headers=auth(api), json={}).json()["id"]
    refused = send(api, conv, "дорогой вопрос")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "budget_exceeded" and detail["can_confirm"] is True
    assert detail["budgets"][0]["spent_usd"] == 1.0
    assert api.get(f"/v1/conversations/{conv}", headers=auth(api)).json()["messages"] == []  # nothing stored

    with api.websocket_connect("/v1/events", headers={**auth(api), **WS_HOST}) as ws:
        ws.receive_json()
        body = {"content": "всё равно", "model_id": "fake/echo", "confirm_over_budget": True}
        assert api.post(f"/v1/conversations/{conv}/messages", headers=auth(api), json=body).status_code == 202
        override = drain_until(ws, "budget.overridden")[-1]["payload"]
        drain_until(ws, "chat.done")
    assert override["model_id"] == "fake/echo" and override["budgets"][0]["hard_usd"] == 1.0


def test_a_blocking_budget_cannot_be_confirmed_and_local_models_are_free(api):
    register_fake(api, models=PAID)
    put_budget(api, scope="total", period="day", hard_usd=0.5, hard_action="block")
    spend_now(api, 0.6)
    conv = api.post("/v1/conversations", headers=auth(api), json={}).json()["id"]
    body = {"content": "x", "model_id": "fake/echo", "confirm_over_budget": True}
    refused = api.post(f"/v1/conversations/{conv}/messages", headers=auth(api), json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["can_confirm"] is False

    local = [Model("free/echo", "free", "echo", "Local", "local", {}, {})]
    provider = register_fake(api, models=local)
    provider.name = "free"
    api.app.state.gateway.register(provider)
    api.portal.call(api.app.state.gateway.sync_catalog)
    assert send(api, conv, "локально", model_id="free/echo").status_code == 202  # costs nothing, never held


def test_crossing_limits_publishes_warning_and_exceeded(api):
    end = StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 3})  # 5e-6 + 6e-6 = 1.1e-5 USD
    register_fake(api, end=end, models=PAID)
    put_budget(api, scope="provider", provider="fake", period="day", soft_usd=0.00001)
    put_budget(api, scope="total", period="day", hard_usd=0.00001)
    conv = api.post("/v1/conversations", headers=auth(api), json={}).json()["id"]
    with api.websocket_connect("/v1/events", headers={**auth(api), **WS_HOST}) as ws:
        ws.receive_json()
        send(api, conv, "раз")
        events = drain_until(ws, "chat.done")
        events += [ws.receive_json(), ws.receive_json()]
    kinds = {e["type"]: e["payload"]["budget"]["scope"] for e in events if e["type"].startswith("budget.")}
    assert kinds == {"budget.warning": "provider", "budget.exceeded": "total"}
