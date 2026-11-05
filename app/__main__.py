"""App entry point: `python -m app` opens the chat window, which finds the hub or starts it (D2, D3).

The window is Qt Quick drawn by the CPU (app/ui, D3). `--smoke` builds the window without connecting and exits:
a check that the GUI starts.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import logging
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QSettings, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from common import __version__
from common.logging_setup import setup_logging
from common.paths import data_paths
from hub.secret_filter import clean

from .ui import brand, translator
from .ui.hitches import HitchWatch
from .ui.main import QuickApp, use_software_rendering

APP_ID = "AFO.App"  # the taskbar's group: the window's, and the installer's shortcuts' (installer/afo.iss)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="AFO window")
    parser.add_argument("--smoke", action="store_true", help="build the window, do not connect, exit")
    args = parser.parse_args(argv)

    log = setup_logging("app", redact=clean)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per hub call is noise
    log.info("AFO app %s (PySide6 %s)", __version__, pyside_version)
    watch_for_crashes(log)
    if sys.platform == "win32":
        # the taskbar groups the window as AFO, not as python.exe
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    use_software_rendering()
    app = QApplication(sys.argv[:1])  # QtWidgets for the tray icon and its menu (D21); QML runs on it
    app.setApplicationName("AFO")
    app.setWindowIcon(brand.icon())
    paths = data_paths().ensure()
    language = translator.use(app, QSettings(str(paths.root / "app.ini"), QSettings.Format.IniFormat))
    log.info("language: %s", language)
    quick = QuickApp(app, paths, connect=not args.smoke)
    if args.smoke:
        log.info("smoke check: the window was built (%d QML warnings)", len(quick.warnings))
        quick.close()
        return 1 if quick.warnings else 0
    hitches = HitchWatch(app)  # a lag the person sees leaves a line in the log: how long, and where (2026-11-05)
    hitches.start()
    code = app.exec()
    hitches.stop()
    log.info("the window closed (exit code %s)", code)
    return code


def watch_for_crashes(log: logging.Logger) -> None:
    """A native crash (in Qt, not in Python) leaves the Python stacks in data/logs/app-crash.log, and Qt's own
    warnings join the log: without them a crash or a freeze leaves no trace at all."""
    crash_file = open(data_paths().logs / "app-crash.log", "a", encoding="utf-8")  # noqa: SIM115 - open until exit
    faulthandler.enable(crash_file, all_threads=True)
    qt_log = logging.getLogger("app.qt")
    levels = {QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}  # fmt: skip

    def handler(kind: QtMsgType, context, message: str) -> None:
        if kind in levels and ".qml:" not in message:  # QML warnings are logged by the window already
            qt_log.log(levels[kind], "%s", message)

    qInstallMessageHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
