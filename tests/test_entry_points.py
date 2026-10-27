"""The real processes: `python -m hub` serves and refuses a second copy; `python -m hub token`; `python -m app`."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

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


def test_app_entry_point_builds_the_window(env, isolated_data_dir):
    result = run({**env, "QT_QPA_PLATFORM": "offscreen"}, "app", "--smoke")
    assert result.returncode == 0, result.stderr
    log_text = (isolated_data_dir / "logs" / "app.log").read_text(encoding="utf-8")
    assert "LocalChat app" in log_text
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
