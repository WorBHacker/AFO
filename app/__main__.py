"""App entry point: `python -m app` opens the chat window, which finds the hub or starts it (D2, D3).

The window is Qt Quick drawn by the CPU (app/ui). `--widgets` opens the earlier Qt Widgets window, which goes
away once the Qt Quick one does everything it did. `--smoke` builds the window without connecting and exits:
a check that the GUI starts.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QTimer

from common import __version__
from common.logging_setup import setup_logging
from common.paths import data_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="LocalChat window")
    parser.add_argument("--smoke", action="store_true", help="build the window, do not connect, exit")
    parser.add_argument("--widgets", action="store_true", help="the earlier Qt Widgets window")
    args = parser.parse_args(argv)

    log = setup_logging("app")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per hub call is noise
    log.info("LocalChat app %s (PySide6 %s)", __version__, pyside_version)
    if sys.platform == "win32":
        # the taskbar groups the window as LocalChat, not as python.exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalChat.App")
    if args.widgets:
        return _widgets(args.smoke, log)

    from PySide6.QtGui import QGuiApplication

    from .ui.main import QuickApp, install_russian, use_software_rendering

    use_software_rendering()
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("LocalChat")
    install_russian(app)
    quick = QuickApp(app, data_paths().ensure(), connect=not args.smoke)
    if args.smoke:
        log.info("smoke check: the window was built (%d QML warnings)", len(quick.warnings))
        quick.close()
        return 1 if quick.warnings else 0
    return app.exec()


def _widgets(smoke: bool, log: logging.Logger) -> int:
    from PySide6.QtWidgets import QApplication

    from .ui.main import install_russian
    from .window import MainWindow  # it applies the saved theme (Вид → Тема)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LocalChat")
    install_russian(app)
    window = MainWindow(data_paths().ensure())
    if smoke:
        log.info("smoke check: the window was built")
        window.close()
        return 0
    window.show()
    QTimer.singleShot(0, window.connect_hub)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
