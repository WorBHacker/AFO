import logging
import sys
import threading

import pytest

from common.paths import DATA_DIR_ENV


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own data folder, so no test touches the real data/ folder."""
    data = tmp_path / "data"
    monkeypatch.setenv(DATA_DIR_ENV, str(data))
    return data


@pytest.fixture
def restore_logging():
    """setup_logging changes process-wide state; put it back after the test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    hooks = sys.excepthook, threading.excepthook
    yield
    for handler in [h for h in root.handlers if h not in handlers]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    sys.excepthook, threading.excepthook = hooks
