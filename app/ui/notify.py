"""Notices outside the window (stage 5, D21): a tray icon, and a Windows notification when an agent waits for the
user's answer or finishes its task while the window is not in front. Clicking the notification (or the icon) brings
the window forward with the chat that waits (its approval card on top). With no system tray (or offscreen) nothing
is shown.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icons import svg

SHOW_MS = 10_000


def tray_icon(name: str = "shield", colour: str = "#4f8cff", side: int = 64) -> QIcon:
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, colour).encode())).render(painter, QRectF(0, 0, side, side))
    painter.end()
    return QIcon(QPixmap.fromImage(image))


class Notifier(QObject):
    """The tray icon and its notifications. `active` says whether the window is in front now."""

    opened = Signal()  # the user clicked a notice or the icon: show the window and the chat that waits

    def __init__(self, active: Callable[[], bool], parent: QObject | None = None):
        super().__init__(parent)
        self._active = active
        self.shown: list[tuple[str, str]] = []  # what was notified (tests read it; the tray may be absent)
        self.tray: QSystemTrayIcon | None = None
        self.stop_shown = False
        self._stop_agent: Callable[[], None] | None = None  # while the agent controls the computer (D41)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(tray_icon(), self)
            self.tray.setToolTip("LocalChat")
            menu = QMenu()
            menu.addAction("Открыть LocalChat", self.opened.emit)
            self._stop = menu.addAction("Остановить агента (управляет компьютером)", self._stop_now)
            self._stop.setVisible(False)
            self.tray.setContextMenu(menu)
            self._menu = menu  # kept: the tray does not own it
            self.tray.activated.connect(self._clicked)
            self.tray.messageClicked.connect(self.opened.emit)
            self.tray.show()

    def notify(self, title: str, text: str) -> bool:
        """Shows a notice when the window is not in front (in front, the window says it itself)."""
        if self._active():
            return False
        self.shown.append((title, text))
        if self.tray is not None:
            self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, SHOW_MS)
        return True

    def show_stop(self, shown: bool, stop: Callable[[], None]) -> None:
        """The tray's «stop» for the agent at the desktop (D41): there while it acts."""
        self.stop_shown = shown
        self._stop_agent = stop if shown else None
        if self.tray is not None:
            self._stop.setVisible(shown)

    def _stop_now(self) -> None:
        if self._stop_agent is not None:
            self._stop_agent()

    def _clicked(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.opened.emit()

    def close(self) -> None:
        if self.tray is not None:
            self.tray.hide()
