"""A terminal's screen in the window: a VT emulator (pyte) whose lines become rich text for QML. No Qt here.

The hub sends a terminal's raw output (escape sequences and all, hub/terminals.py); the window plays it into a
pyte screen of its own size. Lines that scroll off the top of the screen are kept as scrollback the way pyte left
them, and become rich text only when the view first shows them (D46, step 13.7: a flood of output made every line
into HTML at once, most of them let go unseen). Colours: the 16 console colours from a palette per theme (the VS
Code terminal's), 256-colour and true colour as sent. The window does not answer the console's queries (cursor
position, device attributes): ConPTY does not wait for them, and an answer replayed from old output would land in
the shell as typed text.
"""

from __future__ import annotations

import html

import pyte
from pyte import modes
from pyte.screens import Margins, wcwidth

from .. import palette

HISTORY_LINES = 3000
STYLES_KEPT = 64  # styles whose characters' cells the screen keeps for drawing (see TermScreen.draw)

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


class Scrolled:
    """A line gone up into the scrollback, as pyte left it (it never changes it again): rich text when first asked."""

    __slots__ = ("_html", "columns", "line")

    def __init__(self, line, columns: int):
        self.line, self.columns, self._html = line, columns, None

    @property
    def html(self) -> str:
        if self._html is None:
            self._html = line_html(self.line, self.columns)
        return self._html

    @property
    def text(self) -> str:
        return line_text(self.line, self.columns)


class TermScreen(pyte.Screen):
    """A pyte screen that keeps what scrolls off its top: `scrolled` (Scrolled lines, oldest first, at most
    HISTORY_LINES), `new_scrolled` (lines added since the last look) and `dropped` (old lines let go)."""

    def __init__(self, columns: int, lines: int):
        super().__init__(columns, lines)
        self.scrolled: list[Scrolled] = []
        self.new_scrolled = 0
        self.dropped = 0
        self._cells: dict = {}  # a style -> {character: its cell}

    def draw(self, data: str) -> None:
        """pyte's drawing, quicker for ordinary text - every character one cell wide, no insert mode: pyte made a new
        cell (Char) for each character, 70% of the window's time in a flood of output (D46, step 13.7). Cells never
        change (pyte replaces them), so the same character in the same style shares one; a line is filled up to the
        screen's edge at a time. Anything else goes to pyte as it came."""
        if modes.IRM in self.mode:
            super().draw(data)
            return
        text = data.translate(self.g1_charset if self.charset else self.g0_charset)
        if any(wcwidth(char) != 1 for char in set(text)):
            super().draw(data)
            return
        attrs = self.cursor.attrs
        cells = self._cells.get(attrs)
        if cells is None:
            if len(self._cells) >= STYLES_KEPT:
                self._cells.clear()
            cells = self._cells[attrs] = {}
        columns = self.columns
        while text:
            cursor = self.cursor
            if cursor.x == columns:  # past the last column, as pyte leaves it
                if modes.DECAWM in self.mode:
                    self.dirty.add(cursor.y)
                    self.carriage_return()
                    self.linefeed()
                else:  # no wrapping: each character takes the last cell in turn, the last one stays
                    text = text[-1]
                    cursor.x -= 1
            room = columns - cursor.x
            part, text = text[:room], text[room:]
            line, x = self.buffer[cursor.y], cursor.x
            for char in part:
                cell = cells.get(char)
                if cell is None:
                    cell = cells[char] = attrs._replace(data=char)
                line[x] = cell
                x += 1
            cursor.x = x
        self.dirty.add(self.cursor.y)

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:  # the whole screen scrolls: its top line goes to the scrollback
            self.scrolled.append(Scrolled(self.buffer[0], self.columns))
            self.new_scrolled += 1
            if len(self.scrolled) > HISTORY_LINES:
                extra = len(self.scrolled) - HISTORY_LINES
                del self.scrolled[:extra]
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
        """The theme changed: the scrollback is made into rich text again, in the new palette, when it shows (new
        objects, so a view holding the old ones sees each line change)."""
        self.scrolled = [Scrolled(s.line, s.columns) for s in self.scrolled]

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
        lines = [s.text for s in self.scrolled] + [line_text(self.buffer[y], self.columns) for y in range(self.lines)]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
