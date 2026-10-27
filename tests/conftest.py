import logging
import subprocess
import sys
import threading

import pytest

from common.paths import DATA_DIR_ENV
from hub.autostart import RUN_STORE_ENV
from hub.claude_code import CLI_ENV
from hub.secrets import STORE_ENV


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own data folder and in-memory secrets, so no test touches the real data/ folder, the
    Windows Credential Manager or the user's Claude Code (subprocesses inherit all three through the environment)."""
    data = tmp_path / "data"
    monkeypatch.setenv(DATA_DIR_ENV, str(data))
    monkeypatch.setenv(STORE_ENV, "memory")
    monkeypatch.setenv(RUN_STORE_ENV, "memory")  # nor the Windows registry's Run key (autostart)
    monkeypatch.setenv(CLI_ENV, str(tmp_path / "no-claude.exe"))  # the user's Claude Code CLI is never found or run
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


def run_git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A small repository: committed files, an ignored file and folder, then a change and a new file."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "build").mkdir()
    (root / "README.md").write_text("# Проект\n", encoding="utf-8", newline="\n")
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("secret.txt\nbuild/\n", encoding="utf-8", newline="\n")
    (root / "secret.txt").write_text("token", encoding="utf-8", newline="\n")
    (root / "build" / "out.bin").write_bytes(b"\0\1\2")
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "first")
    (root / "src" / "app.py").write_text("print('hi')\nprint('new line')\n", encoding="utf-8", newline="\n")
    (root / "notes.txt").write_text("свежий файл\n", encoding="utf-8", newline="\n")
    return root
