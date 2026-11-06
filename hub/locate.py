"""Finding the running hub, or starting one: for the app and for the stdio MCP adapter (D2, D32)."""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

import httpx

from common.i18n import tr
from common.paths import PROJECT_ROOT, DataPaths

from .settings import DEFAULT_PORT, PORT_ENV

log = logging.getLogger("hub.locate")

SERVICE = "localchat-hub"
START_TIMEOUT_S = 30.0
# Whoever started us may have put us in a Windows job that ends all its processes with it (an MCP client does that
# to the stdio adapter); the hub must not end with the adapter, so it leaves the job - or is born outside it (23.3).
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
SUSPENDED = 0x00000004  # CREATE_SUSPENDED: where it is, is looked at before it runs a line
# a hidden console rather than none: the venv's python.exe starts the real interpreter as a child, which would open a
# console window of its own if the parent had none
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
WMI_TIMEOUT_S = 30.0


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
    """Starts `python -m hub` as its own process with no console window; it outlives whoever started it (D2).

    In a job that does not let processes leave it (the MCP client of the Python SDK holds the stdio adapter in one)
    the hub would end with the job (23.3). Such a job says no to leaving - or keeps the process silently: the venv's
    python.exe puts the interpreter in a job of its own that lets go, inside the one that does not. So the hub starts
    suspended, and if it is still in a job, it is killed before it ran a line and born again through WMI."""
    command = [sys.executable, "-m", "hub"]
    if os.name != "nt":
        _popen(command, 0)
        return
    with contextlib.suppress(PermissionError):  # a job that says no
        process = _popen(command, NO_WINDOW | BREAKAWAY | SUSPENDED)
        if not _in_a_job(process) and _resume(process):
            return
        process.kill()
        process.wait()
    if not _through_wmi(command):
        _popen(command, NO_WINDOW)  # the hub then ends with that job


def _popen(command: list[str], flags: int) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)  # fmt: skip


def _in_a_job(process: subprocess.Popen) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    inside = wintypes.BOOL()
    handle = int(process._handle)  # type: ignore[attr-defined]
    return not kernel32.IsProcessInJob(handle, None, ctypes.byref(inside)) or bool(inside.value)


def _resume(process: subprocess.Popen) -> bool:
    """A process started suspended runs (Popen keeps no handle of its thread)."""
    return ctypes.WinDLL("ntdll").NtResumeProcess(wintypes.HANDLE(int(process._handle))) == 0  # type: ignore[attr-defined]


def _through_wmi(command: list[str]) -> bool:
    """WMI's Win32_Process.Create: the process is born to WMI's host, outside every job of ours, with this process's
    environment and folder. COM lives in a thread of its own for it. False when WMI could not."""
    done: list[bool] = []
    thread = threading.Thread(target=_wmi_create, args=(command, done), name="wmi", daemon=True)
    thread.start()
    thread.join(WMI_TIMEOUT_S)
    return bool(done and done[0])


def _wmi_create(command: list[str], done: list[bool]) -> None:
    try:
        import comtypes
        import comtypes.client

        comtypes.CoInitializeEx()  # the first import did it already in this thread: counted all the same
    except (ImportError, OSError) as exc:
        log.warning("no WMI to start the hub outside the job: %s", exc)
        done.append(False)
        return
    try:
        done.append(_wmi_process(comtypes.client, command))
    finally:
        comtypes.CoUninitialize()


def _wmi_process(client: Any, command: list[str]) -> bool:
    try:
        wmi = client.CoGetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2", dynamic=True)
        startup = wmi.Get("Win32_ProcessStartup").SpawnInstance_()
        startup.Properties_.Item("CreateFlags").Value = NO_WINDOW
        startup.Properties_.Item("EnvironmentVariables").Value = [f"{k}={v}" for k, v in os.environ.items()]
        params = wmi.Get("Win32_Process").Methods_("Create").InParameters.SpawnInstance_()
        params.Properties_.Item("CommandLine").Value = subprocess.list2cmdline(command)
        params.Properties_.Item("CurrentDirectory").Value = str(PROJECT_ROOT)
        params.Properties_.Item("ProcessStartupInformation").Value = startup
        code = wmi.ExecMethod("Win32_Process", "Create", params).Properties_.Item("ReturnValue").Value
    except Exception as exc:  # COM's errors are many and not OSError
        log.warning("WMI did not start the hub: %s", exc)
        return False
    if code:
        log.warning("WMI did not start the hub: Win32_Process.Create returned %s", code)
        return False
    log.info("the hub started through WMI: the job we are in does not let processes leave it")
    return True


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
