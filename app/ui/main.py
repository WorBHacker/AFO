"""Starts the QML window: the software scene graph (the CPU draws, VRAM stays with the models, D3), the theme,
the controller, the hub connection. Window size and place are remembered between runs.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QPoint, QSettings, QTimer, QTranslator
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from shiboken6 import Shiboken

from common.paths import DataPaths

from .controller import Controller
from .icons import IconProvider
from .notify import Notifier
from .theme import Theme

log = logging.getLogger("app.ui")
QML_DIR = Path(__file__).parent / "qml"


def use_software_rendering() -> None:
    """Must run before the first Qt Quick window exists."""
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


def install_russian(app: QGuiApplication) -> None:
    """Qt's own words in Russian, like the rest of the window."""
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale(QLocale.Language.Russian), "qtbase", "_", folder):
        app.installTranslator(translator)


def native_frame_touches(window: QWindow) -> None:
    """A frameless window on Windows 11 still gets the system's rounded corners and its shadow."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        round_corners = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_corners), ctypes.sizeof(round_corners))

        class Margins(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_int),
                ("right", ctypes.c_int),
                ("top", ctypes.c_int),
                ("bottom", ctypes.c_int),
            ]

        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(0, 0, 0, 1)))  # brings the shadow back
    except (OSError, AttributeError) as exc:
        log.info("no DWM window touches (%s)", exc)


def restore_geometry(window: QWindow, settings: QSettings) -> None:
    geometry = settings.value("quick/geometry")
    if isinstance(geometry, list) and len(geometry) == 4:
        x, y, w, h = (int(v) for v in geometry)
        screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + 20))
        if screen is not None:  # only where a screen still is
            window.setGeometry(x, y, w, h)
    if settings.value("quick/maximized") in (True, "true"):
        window.showMaximized()


def save_geometry(window: QWindow, settings: QSettings) -> None:
    maximized = window.visibility() == QWindow.Visibility.Maximized
    settings.setValue("quick/maximized", maximized)
    if not maximized:
        g = window.geometry()
        settings.setValue("quick/geometry", [g.x(), g.y(), g.width(), g.height()])
    settings.sync()


class QuickApp:
    """The pieces of a running QML window, kept together so nothing is collected while it runs."""

    def __init__(self, app: QGuiApplication, paths: DataPaths, *, connect: bool = True):
        self.settings = QSettings(str(paths.root / "app.ini"), QSettings.Format.IniFormat)
        self.theme = Theme(str(self.settings.value("ui/theme", "system")))
        self.controller = Controller(paths, self.settings, self.theme)
        self.engine = QQmlApplicationEngine()
        self.warnings: list[str] = []
        self.engine.warnings.connect(self._warned)
        self.engine.addImageProvider("icons", IconProvider())
        context = self.engine.rootContext()
        context.setContextProperty("App", self.controller)
        context.setContextProperty("Projects", self.controller.projects)
        context.setContextProperty("Terminals", self.controller.terminals)
        context.setContextProperty("Feed", self.controller.feed)
        context.setContextProperty("Mentions", self.controller.mentions)
        context.setContextProperty("Approvals", self.controller.approvals)
        context.setContextProperty("Agents", self.controller.agents)
        context.setContextProperty("Theme", self.theme)
        self.engine.load(str(QML_DIR / "Main.qml"))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError("the QML window did not load: " + "; ".join(self.warnings))
        self.window: QWindow | None = roots[0]
        restore_geometry(self.window, self.settings)
        native_frame_touches(self.window)
        # notices outside the window (D21): an agent waits for an answer, or finished, while the window is behind
        self.notifier = Notifier(lambda: self.window is not None and self.window.isActive(), self.controller)
        self.controller.approvals.requested.connect(self.notifier.notify)
        self.controller.agents.finished.connect(self.notifier.notify)
        self.notifier.opened.connect(self._front)
        app.aboutToQuit.connect(self.close)
        if connect:
            QTimer.singleShot(0, self.controller.connectHub)

    def _front(self) -> None:
        """A notice was clicked: the window comes forward with the approval queue."""
        if self.window is None:
            return
        if self.window.visibility() == QWindow.Visibility.Minimized:
            self.window.showNormal()
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()
        if self.controller.approvals.count:
            self.controller.uiRequested.emit("approvals")

    def _warned(self, warnings) -> None:
        for warning in warnings:
            self.warnings.append(warning.toString())
            log.warning("QML: %s", warning.toString())

    def close(self) -> None:
        if self.window is None:
            return
        save_geometry(self.window, self.settings)
        self.notifier.close()
        self.controller.shutdown()  # the hub keeps running (D2)
        # the engine and its window go now, before the controller, so no binding ever sees App gone
        self.window = None
        Shiboken.delete(self.engine)
        self.engine = None
