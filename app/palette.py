"""Design tokens (D36): colours for a dark and a light theme, radii, sizes, fonts. One place for the QML colours
(app/ui/theme.py) and the message HTML (render.py).

Both themes follow the same rules. Neutral greys with a hint of cool; edges are 1px hairlines, not shadows.
- dark: no pure black (#000 smears when scrolling on many panels); surfaces step up in lightness:
  window < panels < cards and inputs, code a step below the card it is on
- light: the content area a soft grey, panels a step darker, cards and inputs white, code a step below white

`current()` is the palette in use; app/ui/theme.py switches it, and whatever shows HTML renders it again.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    # surfaces
    window: str  # window background
    panel: str  # side panel, the status bar
    popup: str  # menus, drop-down lists, tooltips
    card: str  # message cards, inputs, buttons
    card_hover: str
    item_hover: str  # list rows on the panel
    item_selected: str
    code_bg: str  # code body, a step below the card
    code_head: str  # code header
    # lines; Qt's HTML takes no rgba, so the card's hairline also comes pre-blended
    hairline: str
    hairline_strong: str
    hairline_on_card: str
    scroll_handle: str
    scroll_handle_hover: str
    # text
    text: str
    text_soft: str  # long-form answer text
    muted: str  # secondary text, reasoning
    faint: str  # placeholders, footers, small headings: still 4.5:1 on the window, panels and cards
    link: str
    inline_code_bg: str
    # accents and states
    accent: str  # words, icons and lines on the surfaces
    accent_fill: str  # the ground of a primary button: on_accent reads on it at 4.5:1
    accent_hover: str  # that ground under the mouse
    on_accent: str
    selection: str
    find_match: str  # every match of the editor's find on screen (14.3), behind the text
    find_current: str  # the selected one
    ok: str
    warn: str
    error: str
    error_line: str
    code_styles: tuple[str, ...]  # Pygments styles, the first one installed is used


DARK = Palette(
    name="dark",
    dark=True,
    window="#0f1012",
    panel="#18191c",
    popup="#18191c",
    card="#1f2024",
    card_hover="#25262b",
    item_hover="#1f2024",
    item_selected="#25262b",
    code_bg="#141518",
    code_head="#1a1b1f",
    hairline="rgba(255, 255, 255, 0.08)",
    hairline_strong="rgba(255, 255, 255, 0.16)",
    hairline_on_card="#323337",
    scroll_handle="rgba(255, 255, 255, 0.12)",
    scroll_handle_hover="rgba(255, 255, 255, 0.24)",
    text="#e4e5e9",
    text_soft="#c9cbd1",
    muted="#95989f",
    faint="#838790",
    link="#7cb7ff",
    inline_code_bg="#2a2b30",
    accent="#4f8cff",
    accent_fill="#2f6fec",
    accent_hover="#2860d1",
    on_accent="#ffffff",
    selection="#264f78",
    find_match="rgba(234, 160, 60, 0.25)",
    find_current="rgba(234, 160, 60, 0.55)",
    ok="#3fb950",
    warn="#d29922",
    error="#f0883e",
    error_line="rgba(240, 136, 62, 0.45)",
    code_styles=("github-dark", "monokai"),
)

LIGHT = Palette(
    name="light",
    dark=False,
    window="#f5f5f7",
    panel="#ececef",
    popup="#ffffff",
    card="#ffffff",
    card_hover="#f1f2f5",
    item_hover="#e3e4e8",
    item_selected="#dadce1",
    code_bg="#f6f7f9",
    code_head="#eef0f3",
    hairline="rgba(0, 0, 0, 0.08)",
    hairline_strong="rgba(0, 0, 0, 0.16)",
    hairline_on_card="#e8e9ec",
    scroll_handle="rgba(0, 0, 0, 0.18)",
    scroll_handle_hover="rgba(0, 0, 0, 0.32)",
    text="#1d2025",
    text_soft="#2f333a",
    muted="#5d616a",
    faint="#666b75",
    link="#0a63d1",
    inline_code_bg="#eceef2",
    accent="#155be4",
    accent_fill="#155be4",
    accent_hover="#124fc7",
    on_accent="#ffffff",
    selection="#cfe0ff",
    find_match="rgba(255, 196, 0, 0.35)",
    find_current="rgba(255, 150, 0, 0.55)",
    ok="#187533",
    warn="#8b5d00",
    error="#ad4617",
    error_line="rgba(196, 80, 26, 0.45)",
    code_styles=("xcode", "default"),
)

PALETTES = {p.name: p for p in (DARK, LIGHT)}
_current = DARK


def current() -> Palette:
    return _current


def use(palette: Palette) -> None:
    global _current
    _current = palette


# shape
RADIUS_CONTROL = 6  # buttons, inputs, combo boxes
RADIUS_SMALL = 4  # chips, list items, scroll handles
RADIUS_CARD = 8  # message cards, code blocks
SCROLLBAR = 6  # px, flat, no arrow buttons
STATUS_BAR = 26  # px
LINE_HEIGHT = "140%"

# fonts: the first one installed wins (Inter or JetBrains Mono are picked up once installed)
UI_FONTS = ["Inter", "Geist", "Segoe UI Variable Text", "Segoe UI"]
MONO_FONTS = ["JetBrains Mono", "Cascadia Code", "Cascadia Mono", "Consolas"]
UI_POINT_SIZE = 10
MONO_POINT_SIZE = 10


def css_font_list(families: list[str], generic: str) -> str:
    return ", ".join(f"'{f}'" for f in families) + f", {generic}"


MONO_CSS = css_font_list(MONO_FONTS, "monospace")
