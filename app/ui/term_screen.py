"""A terminal's screen in the window: a VT emulator (pyte) whose lines become rich text for QML. No Qt here.

The hub sends a terminal's raw output (escape sequences and all, hub/terminals.py); the window plays it into a
pyte screen of its own size. Lines that scroll off the top of the screen are kept, already rendered, as
scrollback. Colours: the 16 console colours from a palette per theme (the VS Code terminal's), 256-colour and
true colour as sent. The window does not answer the console's queries (cursor position, device attributes): ConPTY
does not wait for them, and an answer replayed from old output would land in the shell as typed text.
"""

from __future__ import annotations

import html

import pyte
from pyte.screens import Margins

from .. import palette

HISTORY_LINES = 3000

# names as pyte gives them ("brown" is yellow; "bfightmagenta" is pyte's own typo for a background colour)
DARK = {
    "black": "#000000", "red": "#cd3131", "green": "#0dbc79", "brown": "#e5e510", "blue": "#2472c8",
    "magenta": "#bc3fbc", "cyan": "#11a8cd", "white": "#e5e5e5", "brightblack": "#666666", "brightred": "#f14c4c",
    "brightgreen": "#23d18b", "brightbrown": "#f5f543", "brightblue": "#3b8eea", "brightmagenta": "#d670d6",
    "brightcyan": "#29b8db", "brightwhite": "#e5e5e5",
}  # fmt: skip
LIGHT = {
    "black": "#000000", "red": "#cd3131", "green": "#00bc00", "brown": "#949800", "blue": "#0451a5",
    "magenta": "#bc05bc", "cyan": "#0598bc", "white": "#555555", "brightblack": "#666666", "brightred": "#cd3131",
    "brightgreen": "#14ce14", "brightbrown": "#b5ba00", "brightblue": "#0451a5", "brightmagenta": "#bc05bc",
    "brightcyan": "#0598bc", "brightwhite": "#a5a5a5",
}  # fmt: skip
for _colours in (DARK, LIGHT):
    _colours["bfightmagenta"] = _colours["brightmagenta"]

APP_CURSOR_KEYS = 1 << 5  # DECCKM (private mode 1), the way pyte stores private modes
BRACKETED_PASTE = 2004 << 5
CURSOR_VISIBLE = pyte.modes.DECTCEM


def colour(name: str, default: str, colours: dict[str, str]) -> str:
    if name == "default":
        return default
    if name in colours:
        return colours[name]
    if len(name) == 6:  # 256-colour and true colour come as rrggbb
        return f"#{name}"
    return default


def line_html(line, columns: int) -> str:
    """One screen line as rich text: runs of the same style become one span; trailing blanks are dropped."""
    p = palette.current()
    colours = DARK if p.dark else LIGHT
    chars = [line[x] for x in range(columns)]
    end = len(chars)
    while end and chars[end - 1].data in (" ", "") and chars[end - 1].bg == "default" and not chars[end - 1].reverse:
        end -= 1
    out: list[str] = []
    run: list[str] = []
    style = None

    def close() -> None:
        if run:
            text = html.escape("".join(run), quote=False).replace(" ", "&nbsp;")
            out.append(f'<span style="{style}">{text}</span>' if style else text)
            run.clear()

    for char in chars[:end]:
        if char.data == "":  # the second half of a wide character
            continue
        fg = colour(char.fg, p.text, colours)
        bg = colour(char.bg, "", colours)
        if char.bold and char.fg in colours and not char.fg.startswith("bright"):
            fg = colours.get("bright" + char.fg, fg)  # bold brightens the 8 basic colours, as consoles do
        if char.reverse:
            fg, bg = (bg or p.code_bg), fg
        parts = []
        if fg != p.text:
            parts.append(f"color:{fg}")
        if bg:
            parts.append(f"background-color:{bg}")
        if char.bold:
            parts.append("font-weight:600")
        if char.italics:
            parts.append("font-style:italic")
        lines = [d for d, on in (("underline", char.underscore), ("line-through", char.strikethrough)) if on]
        if lines:
            parts.append("text-decoration:" + " ".join(lines))
        now = "; ".join(parts)
        if now != style:
            close()
            style = now
        run.append(char.data)
    close()
    return "".join(out)


def line_text(line, columns: int) -> str:
    return "".join(line[x].data for x in range(columns)).rstrip()


class TermScreen(pyte.Screen):
    """A pyte screen that keeps what scrolls off its top: `scrolled` (rendered lines, oldest first, at most
    HISTORY_LINES), `scrolled_text` (the same as plain text), `new_scrolled` (lines added since the last look) and
    `dropped` (old lines let go)."""

    def __init__(self, columns: int, lines: int):
        super().__init__(columns, lines)
        self.scrolled: list[str] = []
        self.scrolled_text: list[str] = []
        self.new_scrolled = 0
        self.dropped = 0

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:  # the whole screen scrolls: its top line goes to the scrollback
            self.scrolled.append(line_html(self.buffer[0], self.columns))
            self.scrolled_text.append(line_text(self.buffer[0], self.columns))
            self.new_scrolled += 1
            if len(self.scrolled) > HISTORY_LINES:
                extra = len(self.scrolled) - HISTORY_LINES
                del self.scrolled[:extra]
                del self.scrolled_text[:extra]
                self.dropped += extra
        super().index()

    def take_news(self) -> tuple[int, set[int]]:
        """(lines scrolled into the scrollback, screen lines changed) since the last call."""
        news, dirty = self.new_scrolled, set(self.dirty)
        self.new_scrolled = 0
        self.dirty.clear()
        return news, dirty

    def html(self, y: int) -> str:
        return line_html(self.buffer[y], self.columns)

    def restyle(self) -> None:
        """The theme changed: the scrollback is rendered again from its plain text (its colours are gone)."""
        self.scrolled = [html.escape(t, quote=False).replace(" ", "&nbsp;") for t in self.scrolled_text]

    @property
    def cursor_visible(self) -> bool:
        return CURSOR_VISIBLE in self.mode

    @property
    def app_cursor_keys(self) -> bool:
        return APP_CURSOR_KEYS in self.mode

    @property
    def bracketed_paste(self) -> bool:
        return BRACKETED_PASTE in self.mode

    def plain_text(self) -> str:
        lines = self.scrolled_text + [line_text(self.buffer[y], self.columns) for y in range(self.lines)]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
