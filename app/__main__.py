"""App entry point: `python -m app` opens the chat window, which finds the hub or starts it (D2, D3).

`python -m app --smoke` builds the window without connecting to the hub and exits: a check that the GUI starts.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from common import __version__
from common.logging_setup import setup_logging
from common.paths import data_paths

from .window import MainWindow  # it applies the saved theme (Вид → Тема)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="LocalChat window")
    parser.add_argument("--smoke", action="store_true", help="build the window, do not connect, exit")
    args = parser.parse_args(argv)

    log = setup_logging("app")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per hub call is noise
    log.info("LocalChat app %s (PySide6 %s)", __version__, pyside_version)
    if sys.platform == "win32":
        # the taskbar groups the window as LocalChat, not as python.exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalChat.App")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("LocalChat")
    window = MainWindow(data_paths().ensure())
    if args.smoke:
        log.info("smoke check: the window was built")
        window.close()
        return 0
    window.show()
    QTimer.singleShot(0, window.connect_hub)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
