"""Design tokens: colours, radii, fonts. One place for the Qt style sheet (theme.py) and the HTML (render.py).

A deep neutral dark theme with no pure black (#000 smears when scrolling on many panels). Surfaces step up in
lightness: window < panels < cards and inputs; code sits a step below the card it is on. Edges are 1px hairlines,
not shadows.
"""

from __future__ import annotations

# surfaces
WINDOW = "#0f1012"  # window background
PANEL = "#18191c"  # side panels, docks, the status bar, menus
CARD = "#1f2024"  # message cards, inputs
CARD_HOVER = "#25262b"
CODE_BG = "#141518"  # code body, a step below the card
CODE_HEAD = "#1a1b1f"  # code header

# lines: a 1px white hairline at 8% for Qt style sheets; Qt's HTML takes no rgba, so the same line pre-blended
HAIRLINE = "rgba(255, 255, 255, 0.08)"
HAIRLINE_STRONG = "rgba(255, 255, 255, 0.16)"
HAIRLINE_ON_CARD = "#323337"  # HAIRLINE over CARD

# text
TEXT = "#e4e5e9"
TEXT_SOFT = "#c9cbd1"  # long-form answer text, a touch softer than UI labels
MUTED = "#8b8e96"  # secondary text, reasoning
FAINT = "#5f626a"  # placeholders, disabled
LINK = "#7cb7ff"
INLINE_CODE_BG = "#2a2b30"

# accents and states
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9dff"
SELECTION = "#264f78"
OK = "#3fb950"
WARN = "#d29922"
ERROR = "#f0883e"

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
