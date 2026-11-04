"""Finding the running hub, or starting one: for the app and for the stdio MCP adapter (D2, D32)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import httpx

from common.i18n import tr
from common.paths import PROJECT_ROOT, DataPaths

from .settings import DEFAULT_PORT, PORT_ENV

SERVICE = "localchat-hub"
START_TIMEOUT_S = 30.0
# Whoever started us may have put us in a Windows job that ends all its processes with it (an MCP client does that
# to the stdio adapter); the hub must not end with the adapter, so it leaves the job when the job allows that.
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)


class HubUnavailable(Exception):
    """No hub answers and none could be started; the message is fit to show to the user."""


def hub_address(paths: DataPaths) -> str:
    """Where the hub listens: hub.json of a running hub, else the configured port."""
    try:
        info = json.loads(paths.hub_info.read_text(encoding="utf-8"))
        return f"http://{info['host']}:{info['port']}"
    except OSError, ValueError, KeyError:
        return f"http://127.0.0.1:{int(os.environ.get(PORT_ENV, DEFAULT_PORT))}"


def probe(base_url: str) -> str:
    """'ok' - an AFO hub answers; 'absent' - nothing listens; 'foreign' - some other program does."""
    try:
        response = httpx.get(f"{base_url}/v1/health", timeout=1.0)
    except httpx.HTTPError:
        return "absent"
    try:
        return "ok" if response.json().get("service") == SERVICE else "foreign"
    except ValueError:
        return "foreign"


def start_hub() -> None:
    """Starts `python -m hub` as its own process with no console window; it outlives whoever started it (D2)."""
    flags = 0
    if os.name == "nt":
        # a hidden console rather than none: the venv's python.exe starts the real interpreter as a child,
        # which would open a console window of its own if the parent had none
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    for extra in (BREAKAWAY, 0) if os.name == "nt" else (0,):
        try:
            subprocess.Popen(
                [sys.executable, "-m", "hub"],
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags | extra,
                close_fds=True,
            )
            return
        except PermissionError:  # inside a job that forbids leaving it: the hub then ends with that job
            continue


def running_hub(paths: DataPaths, *, start: bool = True, timeout: float = START_TIMEOUT_S) -> str:
    """The base URL of the running hub; starts one first when none answers and `start` is set."""
    base = hub_address(paths)
    state = probe(base)
    if state == "foreign":
        raise HubUnavailable(tr("на {address} отвечает не хаб AFO — порт занят другой программой", address=base))
    if state == "ok":
        return base
    if not start:
        raise HubUnavailable(tr("хаб не запущен"))
    start_hub()
    deadline = time.monotonic() + timeout
    while True:
        base = hub_address(paths)
        if probe(base) == "ok":
            return base
        if time.monotonic() > deadline:
            raise HubUnavailable(tr("хаб не запустился за {seconds:.0f} с, подробности в {log}", seconds=timeout,
                                     log=paths.logs / "hub.log"))  # fmt: skip
        time.sleep(0.2)
