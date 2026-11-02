"""Logging for every LocalChat process: console + a rotating UTF-8 file per component in data/logs."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler

from .paths import DataPaths, data_paths

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5
_MARK = "_localchat_handler"


class _Redacting(logging.Formatter):
    """Every line, a traceback included, through `redact` before it is written: a key or a token that came in an
    error's text does not stay in the log (D46)."""

    def __init__(self, fmt: str, redact: Callable[[str], str]):
        super().__init__(fmt)
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        return self._redact(super().format(record))


def setup_logging(
    component: str,
    level: int = logging.INFO,
    paths: DataPaths | None = None,
    console: bool = True,
    redact: Callable[[str], str] | None = None,
) -> logging.Logger:
    """Configures the root logger for this process and returns the component's logger.

    The log file is data/logs/<component>.log, rotated at 5 MB with 5 old files kept.
    Calling it again replaces the handlers it added before instead of duplicating them, and
    leaves other handlers (pytest's capture, for one) alone. Uncaught exceptions, in the main
    thread and in other threads, are written to the log, so a crash leaves a trace in the file.
    `redact` (the hub's secret filter) masks secrets in every line.
    """
    paths = (paths or data_paths()).ensure()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(handler)
        handler.close()

    formatter = _Redacting(LOG_FORMAT, redact) if redact else logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        paths.logs / f"{component}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handlers: list[logging.Handler] = [file_handler]
    if console and sys.stderr is not None:  # pythonw.exe has no console
        if hasattr(sys.stderr, "reconfigure"):
            # a cp1251/cp866 console must not turn a Cyrillic log line into a logging error
            sys.stderr.reconfigure(errors="backslashreplace")
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)

    # every HTTP request at INFO would bury the log (and a download's signed CDN links): only trouble is written
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))
    log = logging.getLogger(component)

    def log_uncaught(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
        log.critical(
            "uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught
    threading.excepthook = log_uncaught_in_thread
    return log
