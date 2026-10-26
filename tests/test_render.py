from app.render import Code, Prose, highlight_code, language_label, page, render_plain, render_segments


def test_code_blocks_become_their_own_segments():
    segments = render_segments("Пример:\n\n```python\nprint('привет')\n```\n\nи всё")
    assert [type(s) for s in segments] == [Prose, Code, Prose]
    assert segments[1] == Code("python", "print('привет')")
    assert "Пример:" in segments[0].html and "и всё" in segments[2].html


def test_code_blocks_keep_their_order_and_indented_code_counts():
    segments = render_segments("```\na\n```\n\ntext\n\n```js\nb\n```\n\n    indented\n")
    codes = [s for s in segments if isinstance(s, Code)]
    assert codes == [Code("", "a"), Code("js", "b"), Code("", "indented")]


def test_raw_html_from_the_model_is_escaped():
    (prose,) = render_segments("<script>alert(1)</script> <b>bold</b>")
    assert "<script>" not in prose.html
    assert "&lt;script&gt;" in prose.html


def test_a_nul_in_the_text_cannot_fake_a_code_block():
    segments = render_segments("a\x000\x00b")
    assert all(isinstance(s, Prose) for s in segments)


def test_tables_and_inline_code():
    (prose,) = render_segments("| a | b |\n|---|---|\n| `x` | 2 |\n")
    assert "<table>" in prose.html
    assert "<code>x</code>" in prose.html


def test_highlighting_and_language_names():
    spans = highlight_code("def f(): pass", "python")
    assert "<span" in spans and "def" in spans
    assert "<span" not in highlight_code("plain", "no-such-language")
    assert language_label("py") == "python"
    assert language_label("") == "текст"
    assert language_label("no-such-language") == "no-such-language"


def test_plain_text_keeps_line_breaks_and_escapes():
    html = render_plain("строка 1\n<строка 2>")
    assert "white-space:pre-wrap" in html and "line-height:140%" in html
    assert "&lt;строка 2&gt;" in html


def test_page_carries_the_style_sheet():
    document = page("<p>x</p>")
    assert "<style>" in document and "line-height: 140%" in document
