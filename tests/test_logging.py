import logging
import sys
import threading

import pytest

from common.logging_setup import setup_logging
from common.paths import data_paths
from hub.secret_filter import clean

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


def test_secrets_never_reach_the_log():
    """The audit (D46): a key in a message or in an error's traceback is masked before the line is written."""
    key = "sk-ant-api03-" + "z" * 40
    log = setup_logging("hub", console=False, redact=clean)
    log.warning("the provider said: invalid key %s", key)
    try:
        raise RuntimeError(f"Authorization: Bearer {key[::-1]}")
    except RuntimeError:
        log.exception("a request failed")
    _flush()
    text = (data_paths().logs / "hub.log").read_text(encoding="utf-8")
    assert "invalid key [REDACTED]" in text and "Authorization: Bearer [REDACTED]" in text
    assert key not in text and key[::-1] not in text
