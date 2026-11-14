import contextlib
import json
import logging
import sqlite3
import subprocess
import sys
import threading
import uuid

import pytest

from common import i18n
from common.paths import DATA_DIR_ENV, HOME_ENV, MODELS_ENV, data_paths
from hub import secret_filter
from hub.autostart import LLAMA_SWAP_START_ENV, RUN_STORE_ENV
from hub.claude_code import CLI_ENV
from hub.engines import GITHUB_ENV
from hub.gpu_prefs import KEY as GPU_PREFS_KEY
from hub.gpu_prefs import KEY_ENV as GPU_PREFS_KEY_ENV
from hub.gpu_prefs import MOVED_SETTING
from hub.hf import BASE_ENV as HF_ENV
from hub.llama_config import CONFIG_ENV as LLAMA_SWAP_CONFIG_ENV
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
    monkeypatch.setenv(HF_ENV, "http://127.0.0.1:9")  # nor Hugging Face: a test that forgets its stand-in fails fast
    monkeypatch.setenv(GITHUB_ENV, "http://127.0.0.1:9")  # nor GitHub (the wizard's engines, the system check)
    # nor the user's llama-swap (its config, its start script) or LocalChat's own folder in %LOCALAPPDATA% (10.3)
    monkeypatch.setenv(LLAMA_SWAP_CONFIG_ENV, str(tmp_path / "no-llama-swap" / "config.yaml"))
    monkeypatch.setenv(LLAMA_SWAP_START_ENV, str(tmp_path / "no-llama-swap" / "start-llama-swap.cmd"))
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "localchat-home"))
    monkeypatch.setenv(MODELS_ENV, str(tmp_path / "localchat-model"))  # nor models into the checkout's `model` folder
    # nor Windows' «Графика» setting of the person's programs (43.5): a key of the test's own, gone afterwards
    gpu_key = rf"Software\AFO-tests\{uuid.uuid4().hex}\UserGpuPreferences"
    monkeypatch.setenv(GPU_PREFS_KEY_ENV, gpu_key)
    yield data
    _drop_key(gpu_key)


PERSONS_KEYS = (  # the person's own registry values a stray hub could write: «Графика» (43.5), AFO at sign-in (43.4)
    (GPU_PREFS_KEY, None),
    (r"Software\Microsoft\Windows\CurrentVersion\Run", ("AFO", "AFO hub", "LocalChat hub")),
)
PERSONS_DB = data_paths().db  # the person's own AFO's database: read before any test sets a data folder of its own


def _persons_moved() -> dict[str, dict]:
    """What the person's own AFO says it wrote in «Графика» (43.5: `gpu.moved` in its hub.db, read only), by the
    program's path in lower case; {} - no such AFO, or its database not read."""
    try:
        with contextlib.closing(sqlite3.connect(f"{PERSONS_DB.as_uri()}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (MOVED_SETTING,)).fetchone()
        moved = json.loads(row[0]) if row else {}
    except sqlite3.Error, ValueError, TypeError, OSError:
        return {}
    return {exe.lower(): record for exe, record in moved.items() if isinstance(record, dict)}


def persons_afo_did(name: str, old, new, moved_before: dict[str, dict], moved_after: dict[str, dict]) -> bool:
    """Whether the person's own AFO, running beside the tests (it starts with Windows, 43.4), made this change of a
    «Графика» value: it wrote what its record says it wrote, or gave back what it had written - its own work, which
    the guard must not take back (2026-11-14: it took back the AFO's Wallpaper Engine, and the AFO, holding its record,
    would not write it again). `old`, `new` - (data, kind) or None."""
    low = name.lower()
    now, then = moved_after.get(low), moved_before.get(low)
    if new is not None and now is not None and now.get("wrote") == new[0]:
        return True
    if then is None or now is not None or old is None or old[0] != then.get("wrote"):
        return False
    return new is None if then.get("before") is None else new is not None and new[0] == then["before"]


def _values(path: str, names) -> dict[str, object]:
    import winreg

    out: dict[str, object] = {}
    with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        i = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if names is None or name in names:
                out[name] = (value, kind)
    return out


@pytest.fixture(scope="session", autouse=True)
def persons_registry_untouched():
    """Whatever a test starts (a hub with too little of the isolation in its environment wrote the person's «Графика»
    once, 2026-11-13): the person's values as they were before the run - put back if changed, and the run fails.
    What the person's own AFO did in «Графика» meanwhile (persons_afo_did) is the person's: it stays."""
    if sys.platform != "win32":
        yield
        return
    import winreg

    moved_before = _persons_moved()
    before = {path: _values(path, names) for path, names in PERSONS_KEYS}
    yield
    moved_after = _persons_moved()
    changed = []
    for path, names in PERSONS_KEYS:
        now = _values(path, names)
        kept = dict(before[path])
        if path == GPU_PREFS_KEY:
            for name in set(now) | set(kept):
                if now.get(name) != kept.get(name) and persons_afo_did(name, kept.get(name), now.get(name),
                                                                       moved_before, moved_after):  # fmt: skip
                    if name in now:
                        kept[name] = now[name]
                    else:
                        del kept[name]
        if now == kept:
            continue
        changed.append(path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name in set(now) - set(kept):
                winreg.DeleteValue(key, name)
            for name, (value, kind) in kept.items():
                if now.get(name) != (value, kind):
                    winreg.SetValueEx(key, name, 0, kind, value)
    assert not changed, f"tests changed the person's registry (put back): {changed}"


def _drop_key(path: str) -> None:
    """A test's registry key and the keys above it the test made (the topmost only when empty)."""
    if sys.platform != "win32":
        return
    import winreg

    while path.count("\\") >= 1:  # never HKCU\Software itself
        with contextlib.suppress(OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        path = path.rsplit("\\", 1)[0]


@pytest.fixture(autouse=True)
def secrets_masked_per_test(monkeypatch):
    """The values the secret filter masks by themselves (a hub's kept keys, its old tokens) are the test's own: a fake
    key of one test does not turn into [REDACTED] in another (22.1)."""
    monkeypatch.setattr(secret_filter, "_exact", None)
    monkeypatch.setattr(secret_filter, "_exact_values", set())


@pytest.fixture(autouse=True)
def russian(monkeypatch):
    """Russian - the code's own texts, which the tests compare - in the test and in the hubs it starts, whatever this
    computer's language and whatever language a test switched to (10.5)."""
    monkeypatch.setenv(i18n.ENV, i18n.SOURCE)
    i18n.set_language(i18n.SOURCE)
    yield
    i18n.set_language(i18n.SOURCE)


class StandInClipboard:
    """The clipboard as the window uses it - text, a picture, copied files' URLs - kept in the test."""

    def __init__(self):
        self.held_text = ""
        self.held_image = None
        self.held_urls: list[str] = []

    def setText(self, text):  # Qt's names
        self.held_text = text

    def text(self):
        return self.held_text

    def image(self):
        from PySide6.QtGui import QImage

        return self.held_image if self.held_image is not None else QImage()

    def mimeData(self):
        from PySide6.QtCore import QMimeData, QUrl

        data = QMimeData()
        if self.held_urls:
            data.setUrls([QUrl(u) for u in self.held_urls])
        return data


@pytest.fixture(autouse=True)
def clipboard_stand_in(monkeypatch):
    """No test touches the user's clipboard (2026-10-30: copy buttons and Ctrl+V tests replaced what the user had
    copied): the window's reads and writes go to a stand-in, the test's own."""
    board = StandInClipboard()
    monkeypatch.setattr("app.ui.controller.Controller._clipboard", lambda _self: board)
    return board


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
