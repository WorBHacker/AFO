"""Markdown -> what the chat shows: the prose as HTML (the subset Qt's rich text understands) and each code block
apart, as (language, code), so the window gives every code block its own widget with a header and a copy button.
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

from .palette import CODE_HEAD, HAIRLINE_ON_CARD, INLINE_CODE_BG, LINE_HEIGHT, LINK, MONO_CSS, MUTED, TEXT_SOFT

STYLE_SHEET = f"""
body {{ color: {TEXT_SOFT}; }}
p, li, blockquote {{ line-height: {LINE_HEIGHT}; }}
a {{ color: {LINK}; text-decoration: none; }}
code {{ font-family: {MONO_CSS}; background-color: {INLINE_CODE_BG}; }}
pre {{ font-family: {MONO_CSS}; }}
th {{ border: 1px solid {HAIRLINE_ON_CARD}; padding: 4px 8px; background-color: {CODE_HEAD}; }}
td {{ border: 1px solid {HAIRLINE_ON_CARD}; padding: 4px 8px; }}
blockquote {{ color: {MUTED}; margin-left: 12px; }}
h1 {{ font-size: 140%; }} h2 {{ font-size: 125%; }} h3 {{ font-size: 112%; }}
"""

_STYLE = "github-dark" if "github-dark" in set(get_all_styles()) else "monokai"
_FORMATTER = HtmlFormatter(style=_STYLE, noclasses=True, nowrap=True)
_MARK = "\x00"  # CommonMark turns NUL in the input into U+FFFD, so this never comes from the model


@dataclass(frozen=True)
class Prose:
    html: str


@dataclass(frozen=True)
class Code:
    lang: str
    code: str


def _fence(self, tokens, idx, options, env):
    token = tokens[idx]
    info = token.info.strip()
    codes = env.setdefault("codes", [])
    codes.append(Code(info.split()[0] if info else "", token.content.rstrip("\n")))
    return f"{_MARK}{len(codes) - 1}{_MARK}"


_md = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])
_md.add_render_rule("fence", _fence)
_md.add_render_rule("code_block", _fence)


def render_segments(markdown_text: str) -> list[Prose | Code]:
    """The message in reading order: prose HTML between code blocks. Raw HTML from the model is escaped."""
    env: dict = {}
    parts = _md.render(markdown_text, env).split(_MARK)
    segments: list[Prose | Code] = []
    for i, part in enumerate(parts):
        if i % 2:
            segments.append(env["codes"][int(part)])
        elif part.strip():
            segments.append(Prose(part))
    return segments


def highlight_code(code: str, lang: str) -> str:
    """Pygments-coloured spans for a code block (no wrapper, inline styles)."""
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return highlight(code, lexer, _FORMATTER).rstrip("\n")


def render_plain(text: str) -> str:
    """User messages and reasoning: keep line breaks, no Markdown surprises."""
    return f'<p style="white-space:pre-wrap; line-height:{LINE_HEIGHT};">{html.escape(text)}</p>'


def page(body: str) -> str:
    """A full document for a QLabel, which has no default style sheet of its own."""
    return f"<html><head><style>{STYLE_SHEET}</style></head><body>{body}</body></html>"


def language_label(lang: str) -> str:
    if not lang:
        return "текст"
    try:
        return get_lexer_by_name(lang).name.lower()
    except ClassNotFound:
        return lang
