"""Prices that stay right (stage 20.1, Q9): a promotion's end and an announced rise switch on their day, and a price
nobody checked since the change asks to be compared; a long prompt's dearer tier for the whole request (OpenAI over
272K, Gemini's Pro models over 200K, Haiku 5.5 over 100K, OpenRouter's overrides); prices older than 60 days are
marked in «Модели» and in «Проверить систему». The providers' APIs are mocks: nothing leaves this machine."""

import asyncio
import dataclasses
from datetime import date

import httpx
import pytest

from hub import diagnostics, model_settings, store
from hub.db import Database, migrate
from hub.gateway import anthropic_provider, gemini_provider, openai_responses
from hub.gateway.base import Model, cost_usd, long_tier, on_day, scheduled, tier
from hub.gateway.cloud_openai import OpenRouterProvider

KEY = "sk-test-key-1234567890"


def test_a_long_prompt_pays_the_dearer_tier_for_the_whole_request():
    prices = long_tier({"input": 2.0, "output": 10.0, "cache_read": 0.2}, 200_000, 2, 1.5)
    assert prices == {"input": 2.0, "output": 10.0, "cache_read": 0.2, "long_over": 200_000, "long_input": 4.0,
                      "long_output": 15.0, "long_cache_read": 0.4}  # fmt: skip
    assert tier(prices, 200_000) == {"input": 2.0, "output": 10.0, "cache_read": 0.2}  # at the line: the usual price
    assert tier(prices, 200_001) == {"input": 4.0, "output": 15.0, "cache_read": 0.4}
    usage = {"input_tokens": 300_000, "cached_input_tokens": 100_000, "output_tokens": 1000}
    assert cost_usd(usage, prices) == pytest.approx((200_000 * 4.0 + 100_000 * 0.4 + 1000 * 15.0) / 1e6)
    short = {**usage, "input_tokens": 150_000}
    assert cost_usd(short, prices) == pytest.approx((50_000 * 2.0 + 100_000 * 0.2 + 1000 * 10.0) / 1e6)
    assert tier({"input": 1.0, "long_over": 10, "long_input": 3.0}, 11) == {"input": 3.0}  # a part it does not say:
    assert tier({"input": 1.0, "output": 2.0, "long_over": 10, "long_input": 3.0}, 11)["output"] == 2.0  # the usual


def test_the_tables_know_their_long_tiers():
    haiku = anthropic_provider._CATALOG["claude-haiku-5-5"][1]
    assert tier(haiku, 100_000)["input"] == 0.10
    assert tier(haiku, 100_001) == {"input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625}
    assert "long_over" not in anthropic_provider._CATALOG["claude-opus-5-5"][1]  # one price at any length
    sol = tier(openai_responses.MODELS["gpt-6.1-sol"][1], 272_001)
    assert (sol["input"], sol["cache_read"], sol["output"]) == (4.0, 0.2, 15.0)
    pro = gemini_provider._CATALOG["gemini-3.1-pro-preview"][1][0][1]
    assert tier(pro, 200_001) == {"input": 4.0, "output": 18.0, "cache_read": 0.4}
    old_pro = gemini_provider._CATALOG["gemini-2.5-pro"][1][0][1]
    assert tier(old_pro, 200_001) == {"input": 2.5, "output": 15.0, "cache_read": 0.25}
    assert "long_over" not in gemini_provider._CATALOG["gemini-3.8-flash"][1][0][1]


def test_dated_prices_switch_on_their_day():
    schedule = [("2026-01-01", {"input": 1.0}), ("2026-12-01", {"input": 2.0})]
    prices, caps = scheduled(schedule, date(2026, 11, 30), "2026-10-27")
    assert prices == {"input": 1.0}
    assert caps == {"prices_asof": "2026-10-27", "prices_ahead": [{"since": "2026-12-01", "prices": {"input": 2.0}}]}
    row = {"id": "x/m", "prices": prices, "capabilities": caps}
    assert on_day(row, date(2026, 11, 30)) == row
    later = on_day(row, date(2026, 12, 1))  # a catalog read before the day: the row switches by itself
    assert later["prices"] == {"input": 2.0} and later["capabilities"] == {"prices_asof": "2026-10-27"}
    assert scheduled(schedule, date(2026, 12, 2), "2026-10-27") == ({"input": 2.0}, {"prices_asof": "2026-10-27"})


def test_a_promotion_ends_on_its_day_and_asks_to_be_compared():
    def api(request):
        return httpx.Response(200, json={"data": [{"id": "gpt-5.6-sol"}]})

    def catalog(day):
        sol = openai_responses.OpenAIProvider(lambda: KEY, transport=httpx.MockTransport(api), today=lambda: day)
        (model,) = asyncio.run(sol.list_models())
        return dataclasses.asdict(model)

    during = catalog(date(2026, 11, 6))
    assert (during["prices"]["input"], during["prices"]["output"]) == (4.0, 20.0)
    assert during["capabilities"]["promo_until"] == "2026-11-30"
    (ahead,) = during["capabilities"]["prices_ahead"]
    assert ahead["since"] == "2026-11-30" and ahead["promo_end"] is True
    assert (ahead["prices"]["input"], ahead["prices"]["cache_read"], ahead["prices"]["output"]) == (5.0, 0.5, 30.0)
    assert tier(ahead["prices"], 272_001)["output"] == 45.0  # the long tier after it too
    ended = on_day(during, date(2026, 11, 30))
    assert ended["prices"]["input"] == 5.0 and "promo_until" not in ended["capabilities"]
    assert ended["capabilities"]["promo_ended"] == "2026-11-30" and "prices_ahead" not in ended["capabilities"]
    after = catalog(date(2026, 12, 5))  # the catalog read after the day says the same
    assert after["prices"] == ended["prices"] and after["capabilities"] == ended["capabilities"]


def test_the_stored_row_switches_on_its_day_and_the_users_own_price_ends_the_notes(tmp_path, monkeypatch):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    prices, caps = scheduled([("2026-08-21", {"input": 4.0, "output": 20.0}), ("2026-11-30", {"input": 5.0,
                              "output": 30.0})], date(2026, 11, 6), "2026-10-27", promo_until="2026-11-30")  # fmt: skip
    db.call(lambda c: store.upsert_model(c, Model("openai/sol", "openai", "sol", "Sol", "cloud", caps, prices)))
    monkeypatch.setattr(store, "today", lambda: date(2026, 11, 6))
    assert db.call(lambda c: store.get_model(c, "openai/sol"))["prices"]["input"] == 4.0
    monkeypatch.setattr(store, "today", lambda: date(2026, 11, 30))
    row = db.call(lambda c: store.get_model(c, "openai/sol"))
    assert row["prices"]["input"] == 5.0 and row["capabilities"]["promo_ended"] == "2026-11-30"
    assert db.call(lambda c: store.list_models(c))[0]["prices"]["input"] == 5.0
    db.call(lambda c: model_settings.save(c, "openai/sol", {"prices": {"input": 4.5}}, by="app"))
    row = db.call(lambda c: store.get_model(c, "openai/sol"))
    assert row["prices"] == {"input": 4.5, "output": 30.0}
    assert not {"promo_ended", "promo_until", "prices_ahead"} & set(row["capabilities"])  # the user's price stands
    db.close()


def test_gemini_2_5_pro_goes_over_to_flash_prices():
    pro = gemini_provider._CATALOG["gemini-2.5-pro"][1]
    prices, caps = scheduled(pro, date(2026, 12, 1), gemini_provider.PRICES_ASOF)
    assert prices == {"input": 0.75, "output": 3.75, "cache_read": 0.075}  # Google routes it to Gemini 3.8 Flash
    assert caps["prices_ahead"][0]["since"] == "2027-01-01"  # and Flash's own rise follows


def test_openrouters_long_prompt_overrides_go_into_the_catalog():
    listed = [{"id": "openai/gpt-5.6-sol-pro", "name": "Sol Pro", "context_length": 1_050_000,
               "supported_parameters": ["tools"], "pricing": {
                   "prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000002",
                   "overrides": [{"min_prompt_tokens": 272000, "prompt": "0.000004", "completion": "0.000015",
                                  "input_cache_read": "0.0000004"},
                                 {"utc_start": 800, "utc_end": 1630, "prompt": "0.000009"},  # by the hour: not this
                                 {"min_prompt_tokens": 500000, "completion": "0.00002"}]}}]  # fmt: skip

    def api(request):
        return httpx.Response(200, json={"data": listed})

    router = OpenRouterProvider(lambda: KEY, chosen=_chosen, transport=httpx.MockTransport(api))
    (model,) = asyncio.run(router.list_models())
    # the first threshold, the dearest tier's prices: an estimate stays on the safe side (the charge comes in usage)
    assert model.prices == {"input": 2.0, "output": 10.0, "cache_read": 0.2, "long_over": 271_999, "long_input": 4.0,
                            "long_output": 20.0, "long_cache_read": 0.4}  # fmt: skip


async def _chosen():
    return ["openai/gpt-5.6-sol-pro"]


def test_old_or_unchecked_prices_are_marked_in_the_system_check():
    def model(model_id, asof, **caps):
        return {"id": model_id, "provider": model_id.partition("/")[0], "name": model_id, "locality": "cloud",
                "prices": {"input": 1.0}, "capabilities": {"prices_asof": asof, **caps}}  # fmt: skip

    fresh = [model("anthropic/opus", "2026-11-06"), model("openrouter/x", "")]
    line = diagnostics.prices_line(fresh, date(2026, 11, 20))
    assert line["ok"] is True and "2026-11-06" in line["text"]
    old = [*fresh, model("deepseek/flash", "2026-08-01"), model("openai/sol", "2026-10-27", promo_ended="2026-11-30")]
    line = diagnostics.prices_line(old, date(2026, 12, 1))
    assert line["ok"] is None
    assert "deepseek: цены на 2026-08-01" in line["text"] and "openai/sol: акция кончилась 2026-11-30" in line["text"]
    assert "api-docs.deepseek.com" in line["advice"] and "developers.openai.com" in line["advice"]
    assert "anthropic" not in line["text"]
    local = {**model("llama-swap/coder", ""), "locality": "local", "prices": {}}
    assert diagnostics.prices_line([local], date(2026, 12, 1)) is None  # nothing paid


def test_the_models_sheet_says_what_the_prices_are():
    from app.ui.model_setup import price_words

    caps = {
        "prices_asof": "2026-10-27",
        "promo_until": "2026-11-30",
        "prices_ahead": [{"since": "2026-11-30", "prices": {"input": 5.0, "output": 30.0}, "promo_end": True}],
    }
    assert price_words(caps, date(2026, 11, 6)) == ["цены на 2026-10-27", "акция до 2026-11-30, потом $5/$30 за 1M"]
    rise = {
        "prices_asof": "2026-10-27",
        "prices_ahead": [{"since": "2026-12-01", "prices": {"input": 4.0, "output": 20.0}}],
    }
    assert price_words(rise, date(2026, 11, 6))[1] == "с 2026-12-01 — $4/$20 за 1M"
    ended = {"prices_asof": "2026-10-27", "promo_ended": "2026-11-30"}
    assert price_words(ended, date(2026, 12, 1)) == ["цены на 2026-10-27", "акция кончилась 2026-11-30 — сверьте цену"]
    assert price_words({"prices_asof": "2026-08-01"}, date(2026, 11, 6)) == [
        "цены на 2026-08-01 — старше 60 дней, сверьте"
    ]
    assert price_words({}, date(2026, 11, 6)) == []
