import logging
import sys
import threading

import pytest

from common.paths import DATA_DIR_ENV
from hub.secrets import STORE_ENV


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own data folder and in-memory secrets, so no test touches the real data/ folder
    or the Windows Credential Manager (subprocesses inherit both through the environment)."""
    data = tmp_path / "data"
    monkeypatch.setenv(DATA_DIR_ENV, str(data))
    monkeypatch.setenv(STORE_ENV, "memory")
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
