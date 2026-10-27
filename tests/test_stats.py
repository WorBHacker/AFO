"""What the hub learns about its models and spending (stage 6.1, D11, D12): medians, errors, tasks, the forecast."""

import json
from datetime import UTC, datetime

import pytest

from hub import stats
from hub.db import Database, migrate

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    database.call(migrate)

    def fill(conn):
        def usage(ts, model, cost, out, stats_, stop="stop", estimated=0):
            meta = {"stats": stats_, "stop_reason": stop, "cancelled": False}
            conn.execute(
                "INSERT INTO usage (ts, model_id, input_tokens, output_tokens, cost_usd, estimated, meta)"
                " VALUES (?, ?, 100, ?, ?, ?, ?)",
                (ts, model, out, cost, estimated, json.dumps(meta)),
            )

        usage("2026-10-27T10:00:00.000Z", "local/coder", 0, 50, {"tok_s": 100, "ttft_s": 0.5, "total_s": 1.0})
        usage("2026-10-27T10:01:00.000Z", "local/coder", 0, 60, {"tok_s": 120, "ttft_s": 0.3, "total_s": 1.2},
              stop="tool_calls")  # fmt: skip
        usage("2026-10-26T10:00:00.000Z", "local/coder", 0, 70, {"tok_s": 20, "ttft_s": 90, "total_s": 95})  # a swap
        usage("2026-10-25T10:00:00.000Z", "cloud/opus", 0.30, 200, {"tok_s": 60, "ttft_s": 1.5, "total_s": 5})
        usage("2026-10-02T10:00:00.000Z", "cloud/opus", 0.70, 300, {"tok_s": 50, "ttft_s": 2.0, "total_s": 7})
        usage("2026-09-20T10:00:00.000Z", "cloud/opus", 9.00, 900, {"tok_s": 50})  # last month: not in this month
        usage("2026-10-27T11:00:00.000Z", "claude-code/default", 0, 40,
              {"cost_estimate_usd": 0.05, "subscription": True}, estimated=1)  # fmt: skip
        error = json.dumps({"model_id": "local/coder", "detail": "boom"})
        conn.execute("INSERT INTO events (ts, type, payload) VALUES ('2026-10-27T10:05:00.000Z', 'chat.error', ?)",
                     (error,))  # fmt: skip
        conn.execute("INSERT INTO conversations (id, kind) VALUES ('r1', 'agent_run'), ('r2', 'agent_run')")
        for rid, status, steps, cost in (("r1", "done", 4, 0.0), ("r2", "error", 30, 0.0)):
            conn.execute(
                "INSERT INTO agent_runs (conversation_id, profile_name, model_id, max_steps, max_minutes, task, status,"
                " steps, cost_usd, created_at) VALUES (?, 'p', 'local/coder', 30, 30, 't', ?, ?, ?, ?)",
                (rid, status, steps, cost, "2026-10-27T09:00:00.000Z"),
            )

    database.call(fill)
    yield database
    database.close()


def test_per_model(db):
    found = {m["model_id"]: m for m in db.call(lambda c: stats.model_stats(c, days=30, now=NOW))}
    coder = found["local/coder"]
    assert coder["requests"] == 3 and coder["errors"] == 1 and coder["error_rate"] == 0.25
    assert coder["tok_s"] == 100 and coder["ttft_s"] == 0.5 and coder["answer_s"] == 1.2  # medians: the swap is one
    assert coder["tool_steps"] == 1 and coder["output_tokens"] == 180
    assert (coder["runs"], coder["runs_done"], coder["runs_error"], coder["run_success"]) == (2, 1, 1, 0.5)
    assert coder["run_steps"] == 17
    opus = found["cloud/opus"]
    assert opus["requests"] == 2 and opus["cost_usd"] == 1.0 and opus["run_success"] is None  # the 30-day window
    assert found["claude-code/default"]["estimate_usd"] == 0.05 and found["claude-code/default"]["cost_usd"] == 0
    assert next(iter(found)) == "local/coder"  # the busiest first


def test_the_months_spending_and_forecast(db):
    money = db.call(lambda c: stats.spending(c, now=NOW))
    assert money["month_to_date_usd"] == 1.0  # September's 9 dollars are not October's
    assert money["daily_avg_7d_usd"] == round(0.30 / 7, 6)
    assert money["days_left"] == 4.5  # October 27, noon: 4.5 days to go
    assert money["forecast_month_usd"] == round(1.0 + 0.30 / 7 * 4.5, 4)
    assert money["by_model_usd"] == {"cloud/opus": 1.0}
    assert money["subscription_estimate_usd"] == 0.05


def test_the_api_and_errors_carry_their_model(tmp_path):
    from starlette.testclient import TestClient

    from common.paths import data_paths
    from hub.server import create_app
    from hub.settings import HubSettings

    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        body = client.get("/v1/stats", params={"days": 7}).json()
        assert body["days"] == 7 and body["models"] == [] and body["spending"]["month_to_date_usd"] == 0
