"""Markdown -> what the chat shows: the prose as HTML (the subset Qt's rich text understands) and each code block
apart, as (language, code), so the window gives every code block its own widget with a header and a copy button.

Colours come from the current palette; after a theme switch the window renders its HTML again.
"""

from __future__ import annotations

import functools
import html
from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatter import Formatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.styles import get_all_styles
from pygments.token import Token
from pygments.util import ClassNotFound

from . import palette

_MARK = "\x00"  # CommonMark turns NUL in the input into U+FFFD, so this never comes from the model
_FORMATTERS: dict[str, Formatter] = {}


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


class QtRichFormatter(Formatter):
    """Pygments -> the rich text Qt reads fastest: <font color>, <b>, <i>, <u> instead of CSS spans (Qt parses a
    style attribute per span: a 2700-line file is ~1 MB of them), and a run of tokens of one look in one tag."""

    def __init__(self, **options):
        super().__init__(**options)
        self._tags: dict[Any, tuple[str, str]] = {}
        for token, look in self.style:
            opening, closing = "", ""
            if look["color"]:
                opening, closing = f'<font color="#{look["color"]}">', "</font>"
            for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
                if look[flag]:
                    opening, closing = f"{opening}<{tag}>", f"</{tag}>{closing}"
            self._tags[token] = (opening, closing)
        self._tags[Token] = self._tags[Token.Text] = ("", "")  # plain text keeps the window's own text colour

    def _tags_of(self, token) -> tuple[str, str]:
        while token not in self._tags and token.parent is not None:  # a lexer's own subtype: its family's look
            token = token.parent
        return self._tags.get(token, ("", ""))

    def format(self, tokensource, outfile) -> None:
        current, run = ("", ""), []
        for token, value in tokensource:
            tags = self._tags_of(token)
            # blanks take the look they come in (their colour does not show): fewer tags
            if tags != current and not (value.isspace() and "<u>" not in current[0]):
                if run:
                    outfile.write(current[0] + html.escape("".join(run), quote=False) + current[1])
                    run = []
                current = tags
            run.append(value)
        text = "".join(run)
        text = text[:-1] if text.endswith("\n") else text  # the lexer's own last newline: the code has none
        if text:
            outfile.write(current[0] + html.escape(text, quote=False) + current[1])


def _formatter() -> Formatter:
    """Pygments colours that suit the current theme (inline, so Qt needs no style sheet for them)."""
    p = palette.current()
    if p.name not in _FORMATTERS:
        installed = set(get_all_styles())
        style = next((s for s in p.code_styles if s in installed), "default")
        _FORMATTERS[p.name] = QtRichFormatter(style=style)
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
    return _highlighted(code, lang, palette.current().name)


@functools.lru_cache(maxsize=512)
def _highlighted(code: str, lang: str, theme: str) -> str:
    """A streamed answer is rendered again many times a second: its finished code blocks come from here."""
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


@functools.lru_cache(maxsize=256)
def language_label(lang: str) -> str:
    if not lang:
        return "текст"
    try:
        return get_lexer_by_name(lang).name.lower()
    except ClassNotFound:
        return lang
