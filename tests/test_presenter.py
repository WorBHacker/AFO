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
    assert "выделено 76/79 ГБ" in line
    assert "загружена: coder (готова)" in line
    assert presenter.commit_is_tight(status)
    status["memory"]["commit_used_gib"] = 60.0
    assert not presenter.commit_is_tight(status)
    assert "llama-swap не отвечает" in presenter.status_text({"local": {"reachable": False}})


def test_usage_text():
    assert (
        presenter.usage_text({"today_cost_usd": 0.0042, "total_cost_usd": 1.2})
        == "Расходы: сегодня $0.0042 · всего $1.20"
    )


def test_markdown_html_carries_the_style_and_the_code():
    html, codes = presenter.markdown_html("```python\nprint(1)\n```")
    assert "<style>" in html and 'href="copy:0"' in html
    assert codes == ["print(1)"]
    assert "&lt;b&gt;" in presenter.plain_html("<b>")
