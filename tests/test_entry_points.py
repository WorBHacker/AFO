"""The real processes: `python -m hub` serves and refuses a second copy; `python -m hub token`; `python -m app`."""

import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

import httpx
import pytest

from common import crashes
from common.paths import DATA_DIR_ENV, PROJECT_ROOT
from hub.settings import PORT_ENV


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(isolated_data_dir):
    return {**os.environ, DATA_DIR_ENV: str(isolated_data_dir), PORT_ENV: str(free_port())}


def run(env, *args, timeout=60):
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",  # the hub writes UTF-8 to pipes
        timeout=timeout,
    )


def wait_healthy(port: int, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=1).json()
        except httpx.HTTPError:
            time.sleep(0.2)
    raise TimeoutError("the hub did not answer /v1/health")


def stop_tree(process: subprocess.Popen) -> None:
    """A venv's python.exe on Windows is a launcher that starts the real interpreter as a child, so
    stopping only `process` could leave the hub running. Stop the whole tree."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        process.terminate()
    process.wait(timeout=30)


def test_hub_serves_and_refuses_a_second_copy(env, isolated_data_dir):
    port = int(env[PORT_ENV])
    hub = subprocess.Popen([sys.executable, "-m", "hub"], cwd=PROJECT_ROOT, env=env)
    try:
        assert wait_healthy(port)["status"] == "ok"
        info = json.loads((isolated_data_dir / "hub.json").read_text(encoding="utf-8"))
        assert info["port"] == port
        assert isinstance(info["pid"], int)  # the real interpreter's pid, not necessarily hub.pid
        token = (isolated_data_dir / "tokens" / "app.token").read_text(encoding="utf-8")
        me = httpx.get(f"http://127.0.0.1:{port}/v1/info", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["client"]["name"] == "app"

        second = run(env, "hub", timeout=30)
        assert second.returncode == 3, second.stderr
        assert "already running" in (isolated_data_dir / "logs" / "hub.log").read_text(encoding="utf-8")

        stopped = run(env, "hub", "stop", timeout=60)
        assert stopped.returncode == 0, stopped.stderr
        assert "Хаб остановлен" in stopped.stdout
        assert hub.wait(timeout=30) == 0
        assert not (isolated_data_dir / "hub.json").exists()
        log_text = (isolated_data_dir / "logs" / "hub.log").read_text(encoding="utf-8")
        assert "hub stopped" in log_text
    finally:
        if hub.poll() is None:
            stop_tree(hub)
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)  # nothing left listening
    assert run(env, "hub", "stop").returncode == 1  # not running any more


def test_token_cli(env):
    created = run(env, "hub", "token", "create", "cline")
    assert created.returncode == 0, created.stderr
    token = created.stdout.strip().splitlines()[-1]
    assert len(token) >= 40
    assert run(env, "hub", "token", "create", "cline").returncode == 1  # the name is taken
    listed = run(env, "hub", "token", "list")
    assert "cline" in listed.stdout
    assert "облачный" in listed.stdout  # readable Cyrillic through a pipe
    assert run(env, "hub", "token", "revoke", "cline").returncode == 0
    assert run(env, "hub", "token", "revoke", "cline").returncode == 1


def test_db_restore_lists_the_copies_and_waits_for_the_hub_to_stop(env, isolated_data_dir):
    from hub import backup
    from hub.singleton import InstanceLock

    assert run(env, "hub", "token", "create", "first").returncode == 0  # makes the database
    folder = isolated_data_dir / "backups"
    assert "Копий базы нет" in run(env, "hub", "db", "restore").stdout
    copy = backup.back_up(isolated_data_dir / "hub.db", folder, date(2026, 11, 2))
    assert run(env, "hub", "token", "create", "second").returncode == 0  # after the copy
    listed = run(env, "hub", "db", "restore")
    assert listed.returncode == 0 and copy.name in listed.stdout and "схема" in listed.stdout

    with InstanceLock(isolated_data_dir / "hub.lock"):  # as if a hub ran
        busy = run(env, "hub", "db", "restore", copy.name)
    assert busy.returncode == 3 and "Остановите" in busy.stderr
    assert run(env, "hub", "db", "restore", "nothing.db").returncode == 1

    restored = run(env, "hub", "db", "restore", copy.name)
    assert restored.returncode == 0, restored.stderr
    assert "восстановлена" in restored.stdout and "replaced-" in restored.stdout
    names = run(env, "hub", "token", "list").stdout
    assert "first" in names and "second" not in names


def test_app_entry_point_builds_the_window(env, isolated_data_dir):
    result = run({**env, "QT_QPA_PLATFORM": "offscreen"}, "app", "--smoke")
    assert result.returncode == 0, result.stderr
    log_text = (isolated_data_dir / "logs" / "app.log").read_text(encoding="utf-8")
    assert "AFO app" in log_text
    assert "the window was built" in log_text


def test_a_native_crash_of_the_window_leaves_a_trace(env, isolated_data_dir):
    """A crash inside Qt has no Python traceback: the stacks go to app-crash.log, Qt's warnings to app.log."""
    code = (
        "import faulthandler\n"
        "from PySide6.QtCore import qWarning\n"
        "from app.__main__ import watch_for_crashes\n"
        "from common.logging_setup import setup_logging\n"
        "watch_for_crashes(setup_logging('app', console=False))\n"
        "qWarning('QObject: a warning from Qt')\n"
        "faulthandler._sigsegv()\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=60)
    assert result.returncode != 0
    logs = isolated_data_dir / "logs"
    assert "Segmentation fault" in (logs / "app-crash.log").read_text(encoding="utf-8")
    assert "app.qt: QObject: a warning from Qt" in (logs / "app.log").read_text(encoding="utf-8")


def test_a_native_crash_of_the_hub_leaves_a_trace(env, isolated_data_dir):
    """A crash in tree-sitter or winpty has no Python traceback either: the stacks go to hub-crash.log (D46)."""
    code = (
        "import faulthandler\n"
        "from common.paths import data_paths\n"
        "from hub.__main__ import watch_for_crashes\n"
        "watch_for_crashes(data_paths().ensure())\n"
        "faulthandler._sigsegv()\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, capture_output=True, timeout=60)
    assert result.returncode != 0
    assert "Segmentation fault" in (isolated_data_dir / "logs" / "hub-crash.log").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="an exception Windows itself raises and catches")
def test_a_caught_windows_exception_is_not_a_crash_and_a_crash_is_found_at_the_next_start(env, isolated_data_dir):
    """23.2: faulthandler writes an exception that was caught too - 0x800706be, a COM call that failed: 29.10 the window
    went on after it and closed normally. Only a record its session did not live through is a crash."""
    start = (
        "import ctypes, faulthandler\n"
        "from common.paths import data_paths\n"
        "from hub.__main__ import watch_for_crashes\n"
        "watch_for_crashes(data_paths().ensure())\n"
    )
    caught = "try:\n    ctypes.windll.kernel32.RaiseException(0x800706BE, 0, 0, None)\nexcept OSError:\n    pass\n"

    def session(code=""):
        return subprocess.run([sys.executable, "-c", start + code], cwd=PROJECT_ROOT, env=env, capture_output=True,
                              timeout=60).returncode  # fmt: skip

    log = isolated_data_dir / "logs" / "hub-crash.log"
    since = datetime.now() - timedelta(hours=1)
    assert session(caught) == 0 and session() == 0
    text = log.read_text(encoding="utf-8")
    assert "Windows fatal exception: code 0x800706be" in text and "an exception Windows caught, not a crash" in text
    assert crashes.found(log, since) == []
    assert session("faulthandler._sigsegv()\n") != 0
    assert crashes.found(log, since) == []  # the next start says it
    assert session() == 0 and session() == 0
    found = crashes.found(log, since)
    assert [what for _when, what in found] == ["Fatal Python error: Segmentation fault"]
    assert since < found[0][0] <= datetime.now()
