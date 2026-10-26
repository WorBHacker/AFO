"""The app's look: Fusion, a Qt palette and one style sheet, all from app/palette.py. Drawn by the CPU (D3).

Theme modes: "system" follows Windows (and switches when Windows does), "light" and "dark" are fixed. A fixed
mode is also told to Qt, so the native title bar matches the window.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from . import palette
from .palette import Palette

MODES = [("system", "Как в системе"), ("light", "Светлая"), ("dark", "Тёмная")]


@dataclass(frozen=True)
class Fonts:
    ui: str
    mono: str


def pick_font(families: list[str]) -> str:
    installed = set(QFontDatabase.families())
    return next((family for family in families if family in installed), families[-1])


def style_sheet(fonts: Fonts, p: Palette) -> str:
    half = palette.SCROLLBAR // 2
    control, small, card = palette.RADIUS_CONTROL, palette.RADIUS_SMALL, palette.RADIUS_CARD
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; }}
QMainWindow, QWidget#main {{ background: {p.window}; }}
QWidget#sidebar {{ background: {p.panel}; }}
QSplitter::handle:horizontal {{
    width: 5px; background: {p.window}; border-left: 1px solid {p.hairline};  /* a 1px line, a 5px grip */
}}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ color: {p.text_soft}; padding: 7px 10px; border-radius: {small}px; }}
QListWidget::item:hover {{ background: {p.item_hover}; }}
QListWidget::item:selected {{ background: {p.item_selected}; color: {p.text}; }}

QScrollArea, QWidget#transcriptColumn {{ background: {p.window}; border: none; }}

QPushButton {{
    background: {p.card}; border: 1px solid {p.hairline}; border-radius: {control}px; padding: 5px 14px;
}}
QPushButton:hover {{ background: {p.card_hover}; border-color: {p.hairline_strong}; }}
QPushButton#newChat {{ text-align: left; padding: 7px 10px; }}
QPushButton#sendButton {{
    background: {p.accent}; border: 1px solid {p.accent}; color: {p.on_accent}; font-weight: 600; padding: 5px 16px;
}}
QPushButton#sendButton:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#sendButton[stop="true"] {{ background: transparent; border-color: {p.error}; color: {p.error}; }}

QFrame#composerCard {{ background: {p.card}; border: 1px solid {p.hairline}; border-radius: {card}px; }}
QTextEdit#composer {{
    background: transparent; border: none; padding: 2px 4px; selection-background-color: {p.selection};
}}
QLineEdit {{
    background: {p.card}; border: 1px solid {p.hairline}; border-radius: {control}px; padding: 5px 8px;
    selection-background-color: {p.selection};
}}
QLineEdit:focus {{ border-color: {p.hairline_strong}; }}
QComboBox {{
    background: transparent; border: 1px solid {p.hairline}; border-radius: {control}px;
    padding: 3px 10px; min-height: 20px; color: {p.text_soft};
}}
QComboBox:hover {{ border-color: {p.hairline_strong}; color: {p.text}; }}
QComboBox QAbstractItemView {{
    background: {p.popup}; border: 1px solid {p.hairline}; padding: 4px;
    selection-background-color: {p.item_hover}; selection-color: {p.text};
}}

QFrame#msg_user, QFrame#msg_assistant {{
    background: {p.card}; border: 1px solid {p.hairline}; border-radius: {card}px;
}}
QFrame#msg_error {{ background: {p.card}; border: 1px solid {p.error_line}; border-radius: {card}px; }}
QLabel#msgHeader {{ color: {p.muted}; font-weight: 600; }}
QLabel#msgFooter, QLabel#msgNotice {{ color: {p.faint}; font-size: 9pt; }}
QLabel#msgError {{ color: {p.error}; }}

QToolButton#thinkingHeader {{
    color: {p.muted}; background: transparent; border: none; padding: 2px 0; font-size: 9pt;
}}
QToolButton#thinkingHeader:hover {{ color: {p.text_soft}; }}
QLabel#thinkingBody {{ color: {p.muted}; padding: 2px 0; }}
QFrame#thinkingRail {{ background: {p.hairline_strong}; border: none; border-radius: 1px; }}

QFrame#codeBlock {{ background: {p.code_bg}; border: 1px solid {p.hairline}; border-radius: {control}px; }}
QWidget#codeHeader {{
    background: {p.code_head}; border-bottom: 1px solid {p.hairline};
    border-top-left-radius: {control}px; border-top-right-radius: {control}px;
}}
QLabel#codeLang {{ color: {p.muted}; font-family: '{fonts.mono}'; font-size: 9pt; }}
QToolButton#codeAction {{
    color: {p.muted}; background: transparent; border: 1px solid transparent;
    border-radius: {small}px; padding: 1px 8px; font-size: 9pt;
}}
QToolButton#codeAction:hover {{ color: {p.text}; background: {p.card_hover}; border-color: {p.hairline}; }}
QTextBrowser#codeView {{
    background: {p.code_bg}; border: none; color: {p.text}; selection-background-color: {p.selection};
}}

QScrollBar:vertical {{ background: transparent; width: {palette.SCROLLBAR}px; margin: 2px 0; }}
QScrollBar:horizontal {{ background: transparent; height: {palette.SCROLLBAR}px; margin: 0 2px; }}
QScrollBar::handle:vertical {{ background: {p.scroll_handle}; border-radius: {half}px; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {p.scroll_handle}; border-radius: {half}px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {p.scroll_handle_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QStatusBar {{ background: {p.panel}; border-top: 1px solid {p.hairline}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p.muted}; font-size: 9pt; padding: 0 2px; }}
QStatusBar QLabel[warn="true"] {{ color: {p.warn}; }}
QFrame#statusSep {{ background: {p.hairline}; border: none; }}
QProgressBar#meter {{
    background: {p.hairline}; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; min-width: 44px; max-width: 44px;
}}
QProgressBar#meter::chunk {{ background: {p.accent}; border-radius: 2px; }}
QProgressBar#meter[warn="true"]::chunk {{ background: {p.warn}; }}

QMenuBar {{ background: {p.panel}; border-bottom: 1px solid {p.hairline}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; border-radius: {small}px; }}
QMenuBar::item:selected {{ background: {p.item_hover}; }}
QMenu {{ background: {p.popup}; border: 1px solid {p.hairline}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px 6px 24px; border-radius: {small}px; }}
QMenu::item:selected {{ background: {p.item_hover}; }}
QMenu::separator {{ height: 1px; background: {p.hairline}; margin: 4px 6px; }}
QToolTip {{ background: {p.popup}; color: {p.text}; border: 1px solid {p.hairline}; padding: 6px; }}
QDialog, QMessageBox, QInputDialog {{ background: {p.panel}; }}
"""


def qt_palette(p: Palette) -> QPalette:
    colours = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, p.window),
        (QPalette.ColorRole.Base, p.card),
        (QPalette.ColorRole.AlternateBase, p.panel),
        (QPalette.ColorRole.Button, p.card),
        (QPalette.ColorRole.Text, p.text),
        (QPalette.ColorRole.WindowText, p.text),
        (QPalette.ColorRole.ButtonText, p.text),
        (QPalette.ColorRole.Highlight, p.selection),
        (QPalette.ColorRole.HighlightedText, p.text),
        (QPalette.ColorRole.Link, p.link),
        (QPalette.ColorRole.ToolTipBase, p.popup),
        (QPalette.ColorRole.ToolTipText, p.text),
        (QPalette.ColorRole.PlaceholderText, p.faint),
    ):
        colours.setColor(role, QColor(colour))
    return colours


def resolve(mode: str, app: QApplication) -> Palette:
    """The palette for a mode. A fixed mode overrides Qt's colour scheme; "system" hands it back to Windows."""
    hints = app.styleHints()
    if mode in palette.PALETTES:
        hints.setColorScheme(Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light)
        return palette.PALETTES[mode]
    hints.unsetColorScheme()
    return palette.LIGHT if hints.colorScheme() == Qt.ColorScheme.Light else palette.DARK


def apply(app: QApplication, mode: str = "system") -> Palette:
    """Applies a theme mode to the whole app and makes its palette current. Returns that palette."""
    chosen = resolve(mode, app)
    palette.use(chosen)
    fonts = Fonts(pick_font(palette.UI_FONTS), pick_font(palette.MONO_FONTS))
    app.setStyle("Fusion")
    app.setPalette(qt_palette(chosen))
    app.setFont(QFont(fonts.ui, palette.UI_POINT_SIZE))
    app.setStyleSheet(style_sheet(fonts, chosen))
    app.setProperty("monoFont", fonts.mono)
    return chosen


def mono_font() -> QFont:
    app = QApplication.instance()
    family = app.property("monoFont") if app is not None else None
    return QFont(family or palette.MONO_FONTS[-1], palette.MONO_POINT_SIZE)
