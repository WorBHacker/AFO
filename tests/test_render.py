from app.render import render, render_plain


def test_code_block_gets_copy_link_and_raw_text():
    html, codes = render("Пример:\n\n```python\nprint('привет')\n```\n")
    assert codes == ["print('привет')"]
    assert 'href="copy:0"' in html
    assert "python" in html


def test_code_blocks_are_numbered_in_order():
    _, codes = render("```\na\n```\n\ntext\n\n```js\nb\n```\n")
    assert codes == ["a", "b"]


def test_raw_html_from_the_model_is_escaped():
    html, _ = render("<script>alert(1)</script> <b>bold</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_tables_and_inline_code():
    html, _ = render("| a | b |\n|---|---|\n| `x` | 2 |\n")
    assert "<table>" in html
    assert "<code>x</code>" in html


def test_plain_text_keeps_line_breaks_and_escapes():
    html = render_plain("строка 1\n<строка 2>")
    assert "white-space:pre-wrap" in html
    assert "&lt;строка 2&gt;" in html
