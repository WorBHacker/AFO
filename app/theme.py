"""The app's look: Fusion, a dark palette and one style sheet built from app/palette.py. Drawn by the CPU (D3)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from . import palette as p


@dataclass(frozen=True)
class Fonts:
    ui: str
    mono: str


def pick_font(families: list[str]) -> str:
    installed = set(QFontDatabase.families())
    return next((family for family in families if family in installed), families[-1])


def style_sheet(fonts: Fonts) -> str:
    scroll_handle = "rgba(255, 255, 255, 0.12)"
    scroll_handle_hover = "rgba(255, 255, 255, 0.24)"
    half = p.SCROLLBAR // 2
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.TEXT}; }}
QMainWindow, QWidget#main {{ background: {p.WINDOW}; }}
QWidget#sidebar {{ background: {p.PANEL}; }}
QSplitter::handle {{ background: {p.HAIRLINE}; }}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ color: {p.TEXT_SOFT}; padding: 7px 10px; border-radius: {p.RADIUS_SMALL}px; }}
QListWidget::item:hover {{ background: {p.CARD}; }}
QListWidget::item:selected {{ background: {p.CARD_HOVER}; color: {p.TEXT}; }}

QScrollArea, QWidget#transcriptColumn {{ background: {p.WINDOW}; border: none; }}

QPushButton {{
    background: {p.CARD}; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CONTROL}px; padding: 5px 14px;
}}
QPushButton:hover {{ background: {p.CARD_HOVER}; border-color: {p.HAIRLINE_STRONG}; }}
QPushButton#newChat {{ text-align: left; padding: 7px 10px; }}
QPushButton#sendButton {{
    background: {p.ACCENT}; border: 1px solid {p.ACCENT}; color: #ffffff; font-weight: 600; padding: 5px 16px;
}}
QPushButton#sendButton:hover {{ background: {p.ACCENT_HOVER}; border-color: {p.ACCENT_HOVER}; }}
QPushButton#sendButton[stop="true"] {{ background: transparent; border-color: {p.ERROR}; color: {p.ERROR}; }}

QFrame#composerCard {{
    background: {p.CARD}; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CARD}px;
}}
QTextEdit#composer {{
    background: transparent; border: none; padding: 2px 4px; selection-background-color: {p.SELECTION};
}}
QLineEdit {{
    background: {p.CARD}; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CONTROL}px; padding: 5px 8px;
    selection-background-color: {p.SELECTION};
}}
QLineEdit:focus {{ border-color: {p.HAIRLINE_STRONG}; }}
QComboBox {{
    background: transparent; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CONTROL}px;
    padding: 3px 10px; min-height: 20px; color: {p.TEXT_SOFT};
}}
QComboBox:hover {{ border-color: {p.HAIRLINE_STRONG}; color: {p.TEXT}; }}
QComboBox QAbstractItemView {{
    background: {p.PANEL}; border: 1px solid {p.HAIRLINE}; padding: 4px;
    selection-background-color: {p.CARD_HOVER}; selection-color: {p.TEXT};
}}
QLabel#controlLabel {{ color: {p.MUTED}; }}

QFrame#msg_user, QFrame#msg_assistant {{
    background: {p.CARD}; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CARD}px;
}}
QFrame#msg_error {{
    background: {p.CARD}; border: 1px solid rgba(240, 136, 62, 0.45); border-radius: {p.RADIUS_CARD}px;
}}
QLabel#msgHeader {{ color: {p.MUTED}; font-weight: 600; }}
QLabel#msgFooter, QLabel#msgNotice {{ color: {p.FAINT}; font-size: 9pt; }}
QLabel#msgError {{ color: {p.ERROR}; }}

QToolButton#thinkingHeader {{
    color: {p.MUTED}; background: transparent; border: none; padding: 2px 0; font-size: 9pt;
}}
QToolButton#thinkingHeader:hover {{ color: {p.TEXT_SOFT}; }}
QLabel#thinkingBody {{ color: {p.MUTED}; padding: 2px 0; }}
QFrame#thinkingRail {{ background: {p.HAIRLINE_STRONG}; border: none; border-radius: 1px; }}

QFrame#codeBlock {{ background: {p.CODE_BG}; border: 1px solid {p.HAIRLINE}; border-radius: {p.RADIUS_CONTROL}px; }}
QWidget#codeHeader {{
    background: {p.CODE_HEAD}; border-bottom: 1px solid {p.HAIRLINE};
    border-top-left-radius: {p.RADIUS_CONTROL}px; border-top-right-radius: {p.RADIUS_CONTROL}px;
}}
QLabel#codeLang {{ color: {p.MUTED}; font-family: '{fonts.mono}'; font-size: 9pt; }}
QToolButton#codeAction {{
    color: {p.MUTED}; background: transparent; border: 1px solid transparent;
    border-radius: {p.RADIUS_SMALL}px; padding: 1px 8px; font-size: 9pt;
}}
QToolButton#codeAction:hover {{ color: {p.TEXT}; background: {p.CARD_HOVER}; border-color: {p.HAIRLINE}; }}
QTextBrowser#codeView {{
    background: {p.CODE_BG}; border: none; color: {p.TEXT}; selection-background-color: {p.SELECTION};
}}

QScrollBar:vertical {{ background: transparent; width: {p.SCROLLBAR}px; margin: 2px 0; }}
QScrollBar:horizontal {{ background: transparent; height: {p.SCROLLBAR}px; margin: 0 2px; }}
QScrollBar::handle:vertical {{ background: {scroll_handle}; border-radius: {half}px; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {scroll_handle}; border-radius: {half}px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {scroll_handle_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QStatusBar {{ background: {p.PANEL}; border-top: 1px solid {p.HAIRLINE}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p.MUTED}; font-size: 9pt; padding: 0 2px; }}
QStatusBar QLabel[warn="true"] {{ color: {p.WARN}; }}
QFrame#statusSep {{ background: {p.HAIRLINE}; border: none; }}
QProgressBar#meter {{
    background: rgba(255, 255, 255, 0.08); border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; min-width: 44px; max-width: 44px;
}}
QProgressBar#meter::chunk {{ background: {p.ACCENT}; border-radius: 2px; }}
QProgressBar#meter[warn="true"]::chunk {{ background: {p.WARN}; }}

QMenuBar {{ background: {p.PANEL}; border-bottom: 1px solid {p.HAIRLINE}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; border-radius: {p.RADIUS_SMALL}px; }}
QMenuBar::item:selected {{ background: {p.CARD}; }}
QMenu {{ background: {p.PANEL}; border: 1px solid {p.HAIRLINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: {p.RADIUS_SMALL}px; }}
QMenu::item:selected {{ background: {p.CARD_HOVER}; }}
QMenu::separator {{ height: 1px; background: {p.HAIRLINE}; margin: 4px 6px; }}
QToolTip {{ background: {p.PANEL}; color: {p.TEXT}; border: 1px solid {p.HAIRLINE}; padding: 6px; }}
QDialog, QMessageBox, QInputDialog {{ background: {p.PANEL}; }}
"""


def apply(app: QApplication) -> Fonts:
    fonts = Fonts(pick_font(p.UI_FONTS), pick_font(p.MONO_FONTS))
    app.setStyle("Fusion")
    colours = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, p.WINDOW),
        (QPalette.ColorRole.Base, p.CARD),
        (QPalette.ColorRole.AlternateBase, p.PANEL),
        (QPalette.ColorRole.Button, p.CARD),
        (QPalette.ColorRole.Text, p.TEXT),
        (QPalette.ColorRole.WindowText, p.TEXT),
        (QPalette.ColorRole.ButtonText, p.TEXT),
        (QPalette.ColorRole.Highlight, p.SELECTION),
        (QPalette.ColorRole.HighlightedText, p.TEXT),
        (QPalette.ColorRole.Link, p.LINK),
        (QPalette.ColorRole.ToolTipBase, p.PANEL),
        (QPalette.ColorRole.ToolTipText, p.TEXT),
        (QPalette.ColorRole.PlaceholderText, p.FAINT),
    ):
        colours.setColor(role, QColor(colour))
    app.setPalette(colours)
    app.setFont(QFont(fonts.ui, p.UI_POINT_SIZE))
    app.setStyleSheet(style_sheet(fonts))
    app.setProperty("monoFont", fonts.mono)
    return fonts


def mono_font() -> QFont:
    app = QApplication.instance()
    family = app.property("monoFont") if app is not None else None
    return QFont(family or p.MONO_FONTS[-1], p.MONO_POINT_SIZE)
