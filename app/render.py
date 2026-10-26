"""Markdown -> what the chat shows: the prose as HTML (the subset Qt's rich text understands) and each code block
apart, as (language, code), so the window gives every code block its own widget with a header and a copy button.

Colours come from the current palette; after a theme switch the window renders its HTML again.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_all_styles
from pygments.util import ClassNotFound

from . import palette

_MARK = "\x00"  # CommonMark turns NUL in the input into U+FFFD, so this never comes from the model
_FORMATTERS: dict[str, HtmlFormatter] = {}


@dataclass(frozen=True)
class Prose:
    html: str


@dataclass(frozen=True)
class Code:
    lang: str
    code: str


def style_sheet() -> str:
    """The CSS for message HTML in the current theme."""
    p = palette.current()
    return f"""
body {{ color: {p.text_soft}; }}
p, li, blockquote {{ line-height: {palette.LINE_HEIGHT}; }}
a {{ color: {p.link}; text-decoration: none; }}
code {{ font-family: {palette.MONO_CSS}; background-color: {p.inline_code_bg}; }}
pre {{ font-family: {palette.MONO_CSS}; }}
th {{ border: 1px solid {p.hairline_on_card}; padding: 4px 8px; background-color: {p.code_head}; }}
td {{ border: 1px solid {p.hairline_on_card}; padding: 4px 8px; }}
blockquote {{ color: {p.muted}; margin-left: 12px; }}
h1 {{ font-size: 140%; }} h2 {{ font-size: 125%; }} h3 {{ font-size: 112%; }}
"""


def _formatter() -> HtmlFormatter:
    """Pygments colours that suit the current theme (inline styles, so Qt needs no style sheet for them)."""
    p = palette.current()
    if p.name not in _FORMATTERS:
        installed = set(get_all_styles())
        style = next((s for s in p.code_styles if s in installed), "default")
        _FORMATTERS[p.name] = HtmlFormatter(style=style, noclasses=True, nowrap=True)
    return _FORMATTERS[p.name]


def _fence(self, tokens, idx, options, env):
    token = tokens[idx]
    info = token.info.strip()
    codes = env.setdefault("codes", [])
    codes.append(Code(info.split()[0] if info else "", token.content.rstrip("\n")))
    return f"{_MARK}{len(codes) - 1}{_MARK}"


_md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
_md.add_render_rule("fence", _fence)
_md.add_render_rule("code_block", _fence)


def _grid(prose: str) -> str:
    """Qt's rich text draws a table's grid from the table's attributes, not from CSS on its cells."""
    p = palette.current()
    table = (
        f'<table border="1" cellspacing="0" cellpadding="7" width="100%" '
        f'style="border-collapse: collapse; border-style: solid; border-color: {p.hairline_on_card};"'
    )
    return prose.replace("<table", table).replace("<th>", f'<th align="left" bgcolor="{p.code_head}">')


def render_segments(markdown_text: str) -> list[Prose | Code]:
    """The message in reading order: prose HTML between code blocks. Raw HTML from the model is escaped."""
    env: dict = {}
    parts = _md.render(markdown_text, env).split(_MARK)
    segments: list[Prose | Code] = []
    for i, part in enumerate(parts):
        if i % 2:
            segments.append(env["codes"][int(part)])
        elif part.strip():
            segments.append(Prose(_grid(part) if "<table" in part else part))
    return segments


def highlight_code(code: str, lang: str) -> str:
    """Coloured spans for a code block in the current theme (no wrapper, inline styles)."""
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return highlight(code, lexer, _formatter()).rstrip("\n")


def render_plain(text: str) -> str:
    """User messages and reasoning: keep line breaks, no Markdown surprises."""
    return f'<p style="white-space:pre-wrap; line-height:{palette.LINE_HEIGHT};">{html.escape(text)}</p>'


def page(body: str) -> str:
    """A full document for Qt's rich text (a QML TextEdit has no default style sheet of its own)."""
    return f"<html><head><style>{style_sheet()}</style></head><body>{body}</body></html>"


def language_label(lang: str) -> str:
    if not lang:
        return "текст"
    try:
        return get_lexer_by_name(lang).name.lower()
    except ClassNotFound:
        return lang
