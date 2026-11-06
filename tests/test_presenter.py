"""What the window shows, without the window."""

from app import presenter


def test_model_labels_and_order():
    local = {"id": "llama-swap/coder", "name": "Coder", "locality": "local", "prices": {}}
    opus = {
        "id": "anthropic/claude-opus-5-5",
        "name": "Claude Opus 5.5",
        "locality": "cloud",
        "prices": {"input": 4.0, "output": 20.0},
    }
    haiku = {
        "id": "anthropic/claude-haiku-5-5",
        "name": "Claude Haiku 5.5",
        "locality": "cloud",
        "prices": {"input": 0.1, "output": 0.5},
    }
    assert presenter.model_label(local) == "Coder · локальная"
    assert presenter.model_label(opus) == "Claude Opus 5.5 · $4/$20 за 1M"
    assert presenter.model_label(haiku) == "Claude Haiku 5.5 · $0.1/$0.5 за 1M"
    assert [m["name"] for m in presenter.sorted_models([opus, haiku, local])] == [
        "Coder",
        "Claude Haiku 5.5",
        "Claude Opus 5.5",
    ]


def test_money():
    assert presenter.money(0) == "$0"
    assert presenter.money(0.00123) == "$0.0012"
    assert presenter.money(1.5) == "$1.50"


def test_split_blocks():
    content = [{"type": "reasoning", "text": "думаю"}, {"type": "text", "text": "ответ"}]
    assert presenter.split_blocks(content) == ("ответ", "думаю")


def test_answer_meta():
    usage = {"output_tokens": 120, "cached_input_tokens": 2000, "cost_usd": 0.0012, "stats": {"tok_s": 46.04}}
    assert presenter.answer_meta(usage) == "46.0 т/с · 120 ток. · кэш 2000 · $0.0012"
    cancelled = {"output_tokens": 7, "estimated": True, "cancelled": True}
    assert presenter.answer_meta(cancelled) == "~7 ток. · остановлено"
    assert presenter.answer_meta({"output_tokens": 5, "stop_reason": "max_tokens"}).endswith("обрезано по лимиту")
    assert presenter.answer_meta(None) == ""


def test_status_line_and_commit_warning():
    status = {
        "gpu": {"name": "RTX", "vram_used_mib": 15054, "vram_total_mib": 16303, "util_pct": 12, "temp_c": 41},
        "memory": {"ram_used_gib": 44.5, "ram_total_gib": 63.4, "commit_used_gib": 76.0, "commit_limit_gib": 79.1},
        "local": {"reachable": True, "running": [{"model": "coder", "state": "ready"}]},
    }
    line = presenter.status_text(status)
    assert "VRAM 14.7/15.9 ГБ · 12% · 41°C" in line
    assert "ОЗУ 44.5/63.4 ГБ, выделено 76/79 ГБ" in line
    assert "загружена: coder (готова)" in line
    assert presenter.commit_is_tight(status)
    status["memory"]["commit_used_gib"] = 60.0
    assert not presenter.commit_is_tight(status)
    assert "llama-swap не отвечает" in presenter.status_text({"local": {"reachable": False}})
    taken = {"local": {"reachable": False, "taken_by": {"port": 8080, "pid": 4242, "name": "strata.exe"}}}
    assert "порт 8080 занят: strata.exe (pid 4242)" in presenter.status_text(taken)  # 23.4
    assert presenter.local_model(taken) == "порт 8080 занят: strata.exe"


def test_usage_text():
    assert (
        presenter.usage_text({"today_cost_usd": 0.0042, "total_cost_usd": 1.2})
        == "Расходы: сегодня $0.0042 · всего $1.20"
    )


def test_session_counter():
    session = presenter.Session()
    assert session.text() == "Сессия: 0 ток. · $0"
    session.add({"input_tokens": 3000, "cached_input_tokens": 2000, "output_tokens": 250}, 0.0123)
    session.add({"output_tokens": 10}, None)
    assert session.text() == "Сессия: 3.3K ток. · $0.01"
    tip = session.tooltip({"today_cost_usd": 0.5, "total_cost_usd": 2.0})
    assert "2 запросов" in tip and "из кэша 2.0K" in tip and "сегодня $0.50" in tip
    assert presenter.tokens_short(999) == "999"
    assert presenter.tokens_short(2_500_000) == "2.50M"


def test_status_bar_parts():
    status = {
        "gpu": {"vram_used_mib": 8192, "vram_total_mib": 16384, "util_pct": 7, "temp_c": 45},
        "memory": {"ram_used_gib": 30, "ram_total_gib": 64, "commit_used_gib": 40.0, "commit_limit_gib": 80.0},
        "local": {"reachable": True, "running": [{"model": "coder", "state": "starting"}]},
    }
    assert presenter.vram_meter(status) == (0.5, "8.0/16.0 ГБ")
    assert presenter.commit_meter(status) == (0.5, "40/80 ГБ")
    assert presenter.gpu_load(status) == "GPU 7% · 45°C"
    assert presenter.local_model(status) == "coder · загружается"
    assert presenter.local_model({"local": {"reachable": True, "running": []}}) == "видеопамять свободна"
    assert presenter.vram_meter({"gpu": None}) is None
    assert presenter.hub_port("http://127.0.0.1:8765") == ":8765"
    assert presenter.hub_port(None) == ""


def test_budget_texts():
    month = {"scope": "total", "scope_ref": "", "period": "month", "soft_usd": 5.0, "hard_usd": 10.0}
    day = {"scope": "provider", "scope_ref": "anthropic", "period": "day", "soft_usd": 2.0, "hard_usd": None}
    assert presenter.budget_title(month) == "Все платные модели за месяц"
    assert presenter.budget_line({**month, "spent_usd": 3.2}) == (
        "Все платные модели за месяц: $3.20 из $10.00 (предупреждение с $5.00)"
    )
    assert presenter.budget_line({**day, "spent_usd": 0.004}) == "Anthropic за день: $0.0040, предупреждение с $2.00"
    assert presenter.budgets_state([{"state": "ok"}, {"state": "hard"}, {"state": "soft"}]) == "hard"
    assert presenter.budgets_state([]) == "ok"
    warn = presenter.budget_event_text("warning", {**day, "spent_usd": 2.1})
    assert warn == "Бюджет «Anthropic за день»: потрачено $2.10, достигнут порог предупреждения $2.00"
    hard = presenter.budget_event_text("exceeded", {**month, "spent_usd": 10.2, "hard_action": "block"})
    assert hard.endswith("Новые платные запросы — запрещены")
    assert "Отправить запрос всё равно?" in presenter.budget_question([{**month, "spent_usd": 11.0}])
    assert presenter.budget_refusal([{**month, "spent_usd": 11.0, "state": "hard"}]) == (
        "Бюджет исчерпан, платные запросы запрещены: Все платные модели за месяц: $11.00 из $10.00 (предупреждение с "
        "$5.00)"
    )
    # stage 20.2: not used up, but the request may cost more than is left
    dear = {**month, "spent_usd": 9.9, "state": "ok", "left_usd": 0.1, "estimate_usd": 1.28}
    question = presenter.budget_question([dear])
    assert question.startswith("Запрос может стоить до $1.28 — больше, чем осталось в бюджете:\n• Все платные")
    assert "осталось $0.10" in question and "«Предел ответа»" in question and "Отправить запрос всё равно?" in question
    assert presenter.budget_ask_title([dear]) == "Бюджета может не хватить"
    assert presenter.budget_ask_title([{**month, "spent_usd": 11.0, "state": "hard"}]) == "Бюджет исчерпан"
    assert presenter.budget_refusal([dear]) == (
        "Запрос может стоить до $1.28, а в бюджете «Все платные модели за месяц» осталось $0.10 — платные запросы "
        "сверх предела запрещены"
    )
