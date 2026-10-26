"""The theme for QML (D36): the current palette as a colour map, the mode, the fonts.

"system" follows Windows and switches with it; "light" and "dark" are fixed and are also told to Qt, so the
window's native parts (its frame, scroll gestures) match. Message HTML carries colours inside it, so whoever
shows HTML listens to `changed` and renders again.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QFontDatabase, QGuiApplication

from .. import palette

MODES = ["system", "light", "dark"]


def qml_colour(value: str) -> str:
    """'rgba(255, 255, 255, 0.08)' -> '#14ffffff': QML takes #AARRGGBB, not CSS rgba()."""
    match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", value)
    if not match:
        return value
    r, g, b, a = match.groups()
    return f"#{round(float(a) * 255):02x}{int(r):02x}{int(g):02x}{int(b):02x}"


def qml_palette(p: palette.Palette) -> dict:
    return {k: qml_colour(v) if isinstance(v, str) else v for k, v in asdict(p).items() if not isinstance(v, tuple)}


def pick_font(families: list[str]) -> str:
    installed = set(QFontDatabase.families())
    return next((f for f in families if f in installed), families[-1])


class Theme(QObject):
    changed = Signal()

    def __init__(self, mode: str = "system", parent=None):
        super().__init__(parent)
        self._mode = mode if mode in MODES else "system"
        self._ui_font = pick_font(palette.UI_FONTS)
        self._mono_font = pick_font(palette.MONO_FONTS)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_changed)
        self._apply()

    def _apply(self) -> None:
        hints = QGuiApplication.styleHints()
        if self._mode in palette.PALETTES:
            hints.setColorScheme(Qt.ColorScheme.Dark if self._mode == "dark" else Qt.ColorScheme.Light)
            chosen = palette.PALETTES[self._mode]
        else:
            hints.unsetColorScheme()
            chosen = palette.LIGHT if hints.colorScheme() == Qt.ColorScheme.Light else palette.DARK
        palette.use(chosen)
        self._colours = qml_palette(chosen)
        self.changed.emit()

    def _system_changed(self, _scheme) -> None:
        if self._mode == "system":
            self._apply()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self._apply()

    @Property("QVariantMap", notify=changed)
    def p(self) -> dict:
        return self._colours

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property(bool, notify=changed)
    def dark(self) -> bool:
        return palette.current().dark

    @Property(str, constant=True)
    def uiFont(self) -> str:
        return self._ui_font

    @Property(str, constant=True)
    def monoFont(self) -> str:
        return self._mono_font
