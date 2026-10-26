"""A dark theme for the whole app, matching the colours of app/render.py. Fusion style, drawn by the CPU (D3)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .render import BORDER, CODE_BG, CODE_HEAD, LINK, MUTED, TEXT

WINDOW = "#0d1117"
PANEL = "#161b22"
USER_BG = "#1c2330"
ERROR = "#f0883e"
WARN = "#d29922"

STYLE = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {WINDOW}; }}
QListWidget {{ background: {PANEL}; border: none; padding: 4px; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {CODE_HEAD}; color: {TEXT}; }}
QPlainTextEdit, QLineEdit {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}
QComboBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {CODE_HEAD}; }}
QPushButton {{ background: {CODE_HEAD}; border: 1px solid {BORDER}; border-radius: 4px; padding: 5px 14px; }}
QPushButton:hover {{ border-color: {LINK}; }}
QPushButton#sendButton[stop="true"] {{ border-color: {ERROR}; color: {ERROR}; }}
QFrame#msg_user {{ background: {USER_BG}; border-radius: 8px; }}
QFrame#msg_assistant {{ background: transparent; }}
QFrame#msg_error {{ border: 1px solid {ERROR}; border-radius: 8px; }}
QLabel#msgHeader {{ color: {MUTED}; font-weight: bold; }}
QLabel#msgFooter, QLabel#msgNotice {{ color: {MUTED}; }}
QLabel#msgThink {{ color: {MUTED}; background: {CODE_BG}; border-radius: 4px; padding: 6px; }}
QLabel#msgError {{ color: {ERROR}; }}
QToolButton#thinkToggle {{ color: {MUTED}; border: none; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 6px; }}
QStatusBar QLabel[warn="true"] {{ color: {WARN}; }}
QSplitter::handle {{ background: {BORDER}; }}
QMenuBar, QMenu {{ background: {PANEL}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background: {CODE_HEAD}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, WINDOW),
        (QPalette.ColorRole.Base, PANEL),
        (QPalette.ColorRole.AlternateBase, CODE_HEAD),
        (QPalette.ColorRole.Button, CODE_HEAD),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, "#264f78"),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.Link, LINK),
        (QPalette.ColorRole.ToolTipBase, PANEL),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
    ):
        palette.setColor(role, QColor(colour))
    app.setPalette(palette)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
