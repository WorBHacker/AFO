"""Which model for this chat or task (stage 6.2, D11), and a task's cost before it runs (D12)."""

import json

import pytest

from hub import advisor, agents, model_settings, store
from hub.db import Database, migrate
from hub.gateway.base import Model

OPUS = Model("cloud/claude-opus-5-5", "cloud", "claude-opus-5-5", "Claude Opus 5.5", "cloud", {},
             {"input": 4.0, "output": 20.0})  # fmt: skip
HAIKU = Model("cloud/claude-haiku-5-5", "cloud", "claude-haiku-5-5", "Claude Haiku 5.5", "cloud", {},
              {"input": 0.1, "output": 0.5})  # fmt: skip
CODER = Model("local/coder", "local", "coder", "Coder", "local", {}, {})
BIG = Model("local/big", "local", "big", "Big", "local", {}, {})
CLAUDE_CODE = Model("claude-code/default", "claude-code", "default", "Claude Code", "cloud", {"subscription": True}, {})


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    database.call(migrate)

    def fill(conn):
        for model in (OPUS, HAIKU, CODER, BIG, CLAUDE_CODE):
            store.upsert_model(conn, model)
        for _ in range(4):
            conn.execute("INSERT INTO usage (model_id, output_tokens, cost_usd, meta) VALUES ('local/coder', 50, 0, ?)",
                         (json.dumps({"stats": {"tok_s": 110}}),))  # fmt: skip
        conn.execute("INSERT INTO projects (id, name, path, cloud_ok) VALUES ('p1', 'secret', 'x', 0),"
                     " ('p2', 'open', 'y', 1)")  # fmt: skip
        conn.execute(
            "INSERT INTO conversations (id, kind) VALUES ('local', 'chat'), ('open', 'chat'), ('bare', 'chat')"
        )
        store.set_conversation_projects(conn, "local", ["p1"])
        store.set_conversation_projects(conn, "open", ["p2"])

    database.call(fill)
    yield database
    database.close()


def ranked(db, **kwargs):
    advice = db.call(lambda c: advisor.advise(c, **kwargs))
    return advice, {o["model_id"]: o for o in advice["options"]}


def test_what_rules_a_model_out_is_said(db):
    advice, by_id = ranked(db, conversation_id="local", loaded={"coder"})
    assert by_id["cloud/claude-opus-5-5"]["blocked"] == "«secret» — только для локальных моделей (D10)"
    assert advice["recommended"] in ("local/coder", "local/big")
    assert [o["blocked"] is not None for o in advice["options"]][-1] is True  # the blocked ones last
    _advice, bare = ranked(db, conversation_id="bare")
    assert bare["claude-code/default"]["blocked"] is None  # no project: a folder of the chat's own (2026-10-28)
    _advice, opened = ranked(db, conversation_id="open")
    assert opened["claude-code/default"]["blocked"] is None
    assert "платит подписка Claude Code" in opened["claude-code/default"]["reasons"]


def test_priorities_and_what_the_hub_saw(db):
    _advice, by_id = ranked(db, loaded={"coder"})
    coder = by_id["local/coder"]
    assert coder["reasons"][:2] == ["уже в видеопамяти", "110 т/с здесь"] and "бесплатно" in coder["reasons"]
    assert by_id["local/big"]["reasons"][0].startswith("загрузится в видеопамять")
    assert by_id["cloud/claude-opus-5-5"]["estimate_usd"] == round((4000 * 4 + 600 * 20) / 1e6, 6)
    cheap, _ = ranked(db, priority="price", loaded={"coder"})
    best, _ = ranked(db, priority="quality", loaded={"coder"})
    assert cheap["recommended"] == "local/coder"
    assert best["recommended"] in ("cloud/claude-opus-5-5", "claude-code/default")
    assert ranked(db, priority="nonsense")[0]["priority"] == "balanced"


def test_a_used_up_budget(db):
    def budget(conn, action):
        conn.execute("DELETE FROM budgets")
        conn.execute("INSERT INTO budgets (scope, scope_ref, period, hard_usd, hard_action) VALUES "
                     "('total', '', 'month', 0.0001, ?)", (action,))  # fmt: skip
        conn.execute("INSERT INTO usage (model_id, cost_usd) VALUES ('cloud/claude-haiku-5-5', 1.0)")

    db.call(lambda c: budget(c, "block"))
    _advice, by_id = ranked(db)
    assert by_id["cloud/claude-haiku-5-5"]["blocked"] == "бюджет исчерпан"
    db.call(lambda c: budget(c, "ask"))
    _advice, by_id = ranked(db)
    assert by_id["cloud/claude-haiku-5-5"]["blocked"] is None
    assert "бюджет исчерпан — только с подтверждением" in by_id["cloud/claude-haiku-5-5"]["reasons"]


def test_a_model_marked_as_writing_russian_with_errors_is_not_advised(db):
    """25.5 (§5.4: the local «coder» writes Russian with errors, mixing alphabets - accepted, it is the model's): the
    user marks such a model in its settings, and the advice puts it after every unmarked one and does not recommend
    it while another may answer - saying why; it is never hidden, and it is still advised when nothing else is."""
    advice, _ = ranked(db, priority="price", loaded={"coder"})
    assert advice["recommended"] == "local/coder"  # free and in video memory: the cheapest
    db.call(lambda c: model_settings.save(c, "local/coder", model_settings.check({"weak_russian": True}), by="app"))
    assert db.call(lambda c: store.get_model(c, "local/coder"))["weak_russian"] is True
    advice, after = ranked(db, priority="price", loaded={"coder"})
    free = [o["model_id"] for o in advice["options"] if o["blocked"] is None]
    assert advice["recommended"] != "local/coder" and free[-1] == "local/coder"
    assert "пишет по-русски с ошибками" in after["local/coder"]["reasons"] and after["local/coder"]["blocked"] is None
    advice, _ = ranked(db, conversation_id="local", loaded={"coder"})  # only the local ones may see this chat
    assert advice["recommended"] == "local/big"
    db.call(lambda c: model_settings.save(c, "local/big", model_settings.check({"weak_russian": True}), by="app"))
    assert ranked(db, conversation_id="local", loaded={"coder"})[0]["recommended"] in ("local/coder", "local/big")
    with pytest.raises(model_settings.SettingsError):
        model_settings.check({"weak_russian": "да"})
    assert model_settings.check({"weak_russian": False}) == {}  # not marked is no mark


def test_a_tasks_cost_before_it_runs(db):
    def make(conn):
        profile = agents.save_profile(conn, {"name": "a", "model_id": "cloud/claude-haiku-5-5"})
        first = advisor.run_estimate(conn, profile)
        conn.execute("INSERT INTO conversations (id, kind) VALUES ('r1', 'agent_run')")
        conn.execute(
            "INSERT INTO agent_runs (conversation_id, profile_id, profile_name, model_id, max_steps, max_minutes, task,"
            " status, steps, cost_usd) VALUES ('r1', ?, 'a', 'cloud/claude-haiku-5-5', 30, 30, 't', 'done', 3, 0.02)",
            (profile["id"],),
        )
        return first, advisor.run_estimate(conn, profile)

    before, after = db.call(make)
    assert before["basis"].startswith("≈ 6 шагов") and before["estimate_usd"] == round(6 * 0.0007, 6)
    assert after == {"estimate_usd": 0.02, "basis": "по 1 прошлым задачам этого агента"}
