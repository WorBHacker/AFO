"""Markdown -> the HTML subset QTextBrowser understands, with Pygments-highlighted code blocks."""
from __future__ import annotations

import html

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_all_styles
from pygments.util import ClassNotFound

MONO = "'Cascadia Mono', Consolas, monospace"
TEXT = "#e6e6e6"
MUTED = "#9aa0a6"
LINK = "#7cb7ff"
CODE_BG = "#161b22"
CODE_HEAD = "#21262d"
INLINE_BG = "#2d333b"
BORDER = "#3d444d"

STYLE_SHEET = f"""
body {{ color: {TEXT}; }}
a {{ color: {LINK}; }}
code {{ font-family: {MONO}; background-color: {INLINE_BG}; }}
th {{ border: 1px solid {BORDER}; padding: 4px; background-color: {CODE_HEAD}; }}
td {{ border: 1px solid {BORDER}; padding: 4px; }}
blockquote {{ color: {MUTED}; margin-left: 12px; }}
h1 {{ font-size: 150%; }} h2 {{ font-size: 130%; }} h3 {{ font-size: 115%; }}
"""

_STYLE = "github-dark" if "github-dark" in set(get_all_styles()) else "monokai"
_FORMATTER = HtmlFormatter(style=_STYLE, noclasses=True, nowrap=True)


def _code_html(code: str, lang: str, index: int) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    body = highlight(code, lexer, _FORMATTER)
    label = html.escape(lang or "code")
    cell = "border-width:0px;"
    return (f'<table width="100%" border="0" cellspacing="0" cellpadding="8" style="margin-top:6px; margin-bottom:6px;">'
            f'<tr><td style="{cell} background-color:{CODE_HEAD};"><span style="color:{MUTED};">{label}</span>'
            f'&nbsp;&nbsp;&nbsp;<a href="copy:{index}" style="color:{LINK}; text-decoration:none;">Копировать</a></td></tr>'
            f'<tr><td style="{cell} background-color:{CODE_BG};">'
            f'<pre style="font-family:{MONO}; white-space:pre-wrap; margin:0;">{body}</pre></td></tr></table>')


def _fence(self, tokens, idx, options, env):
    tok = tokens[idx]
    lang = tok.info.strip().split()[0] if tok.info.strip() else ""
    codes = env.setdefault("codes", [])
    codes.append(tok.content.rstrip("\n"))
    return _code_html(codes[-1], lang, len(codes) - 1)


_md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
_md.add_render_rule("fence", _fence)
_md.add_render_rule("code_block", _fence)


def render(markdown_text: str) -> tuple[str, list[str]]:
    """HTML for the message and the raw text of its code blocks (for the copy links)."""
    env: dict = {}
    body = _md.render(markdown_text, env)
    return body, env.get("codes", [])


def render_plain(text: str) -> str:
    """User messages and reasoning: keep line breaks, no Markdown surprises."""
    return f'<p style="white-space:pre-wrap;">{html.escape(text)}</p>'
