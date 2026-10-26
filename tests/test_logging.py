import logging
import sys
import threading

import pytest

from common.logging_setup import setup_logging
from common.paths import data_paths

pytestmark = pytest.mark.usefixtures("restore_logging")


def _flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_writes_utf8_file_per_component():
    log = setup_logging("hub", console=False)
    log.info("проверка: модель загружена")
    _flush()
    text = (data_paths().logs / "hub.log").read_text(encoding="utf-8")
    assert "INFO    hub: проверка: модель загружена" in text


def test_second_call_does_not_duplicate_lines():
    setup_logging("app", console=False)
    log = setup_logging("app", console=False)
    log.warning("one line")
    _flush()
    assert (data_paths().logs / "app.log").read_text(encoding="utf-8").count("one line") == 1


def test_keeps_foreign_handlers(caplog):
    setup_logging("hub", console=False)
    logging.getLogger("hub").error("seen by pytest")
    assert "seen by pytest" in caplog.text


def test_uncaught_exception_is_logged():
    setup_logging("hub", console=False)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    _flush()
    text = (data_paths().logs / "hub.log").read_text(encoding="utf-8")
    assert "CRITICAL hub: uncaught exception" in text
    assert "RuntimeError: boom" in text


def test_uncaught_exception_in_thread_is_logged():
    setup_logging("hub", console=False)

    def fail():
        raise ValueError("thread boom")

    worker = threading.Thread(target=fail, name="worker-1")
    worker.start()
    worker.join()
    _flush()
    text = (data_paths().logs / "hub.log").read_text(encoding="utf-8")
    assert "uncaught exception in thread worker-1" in text
    assert "ValueError: thread boom" in text
