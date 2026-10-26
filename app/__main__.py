"""App entry point: `python -m app` opens the chat window, which finds the hub or starts it (D2, D3).

The window is Qt Quick drawn by the CPU (app/ui, D3). `--smoke` builds the window without connecting and exits:
a check that the GUI starts.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtGui import QGuiApplication

from common import __version__
from common.logging_setup import setup_logging
from common.paths import data_paths

from .ui.main import QuickApp, install_russian, use_software_rendering


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


if __name__ == "__main__":
    raise SystemExit(main())
