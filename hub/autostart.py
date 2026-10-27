"""Starting by itself (Q8): the hub at Windows login, llama-swap together with the hub.

- At login: a value in the user's Run key (HKCU\\...\\CurrentVersion\\Run, "LocalChat hub") that runs `pythonw -m hub`
  - no console, no administrator rights. Off until the user turns it on in Settings; off again removes the value.
- llama-swap: when the hub starts and llama-swap does not answer on this computer, the hub runs its start script
  (`D:\\AI model\\llama-swap\\start-llama-swap.cmd`, or the one LOCALCHAT_LLAMA_SWAP_START names) hidden and on its
  own - it keeps running when the hub stops, as the hub does when the window closes (D2). On unless the user turns it
  off in Settings. Its output goes to data/logs/llama-swap.out.log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from common.paths import PROJECT_ROOT

from .db import utc_now

log = logging.getLogger("hub.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LocalChat hub"
RUN_STORE_ENV = "LOCALCHAT_RUN_KEY"  # "memory": tests and throwaway runs never touch the registry
LLAMA_SWAP_START_ENV = "LOCALCHAT_LLAMA_SWAP_START"
DEFAULT_LLAMA_SWAP_START = PROJECT_ROOT.parent / "llama-swap" / "start-llama-swap.cmd"
LLAMA_SWAP_SETTING = "autostart.llama_swap"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
READY_TIMEOUT_S = 60.0  # llama-swap itself starts in a second; models load only when asked
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


class RunStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, command: str) -> None: ...
    def delete(self) -> None: ...


class RegistryRunStore:
    """The user's Run key in the Windows registry."""

    def get(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, RUN_VALUE)[0])
        except FileNotFoundError:
            return None

    def set(self, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass


class MemoryRunStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def get(self) -> str | None:
        return self.value

    def set(self, command: str) -> None:
        self.value = command

    def delete(self) -> None:
        self.value = None


def default_run_store() -> RunStore:
    if os.environ.get(RUN_STORE_ENV, "").lower() == "memory" or os.name != "nt":
        return MemoryRunStore()
    return RegistryRunStore()


def login_command() -> str:
    """`pythonw -m hub` of this installation: the hub with no console window."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return f'"{windowless if windowless.exists() else exe}" -m hub'


def login_enabled(store: RunStore) -> bool:
    return bool(store.get())


def set_login(store: RunStore, on: bool) -> None:
    if on:
        store.set(login_command())
    else:
        store.delete()


# --- llama-swap ---


def llama_swap_script() -> Path:
    return Path(os.environ.get(LLAMA_SWAP_START_ENV) or DEFAULT_LLAMA_SWAP_START)


def llama_swap_wanted(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (LLAMA_SWAP_SETTING,)).fetchone()
    return json.loads(row["value"]) if row else True


def set_llama_swap_wanted(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (LLAMA_SWAP_SETTING, json.dumps(bool(on)), utc_now()),
    )


async def answers(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=transport) as http:
            response = await http.get(f"{url.rstrip('/')}/v1/models")
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def spawn(script: Path, log_file: Path) -> None:
    """The script on its own, hidden: it outlives the hub (D2). Inside a job that forbids leaving, it ends with it."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_file, "ab") as out:
        for extra in (BREAKAWAY, 0) if os.name == "nt" else (0,):
            try:
                subprocess.Popen(["cmd", "/c", str(script)], cwd=script.parent, stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, creationflags=flags | extra, close_fds=True)  # fmt: skip
                return
            except PermissionError:
                continue


async def ensure_llama_swap(url: str, script: Path, wanted: bool, log_file: Path, *, starter=spawn,
                            transport: httpx.AsyncBaseTransport | None = None,
                            ready_timeout: float = READY_TIMEOUT_S) -> str:  # fmt: skip
    """running (it answered), started (and now answers), slow (started, not answering yet), off, missing, remote."""
    if (urlsplit(url).hostname or "") not in LOCAL_HOSTS:
        return "remote"
    if await answers(url, transport):
        return "running"
    if not wanted:
        return "off"
    if not script.is_file():
        return "missing"
    log.info("llama-swap does not answer on %s: starting %s", url, script)
    await asyncio.to_thread(starter, script, log_file)
    deadline = asyncio.get_running_loop().time() + ready_timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        if await answers(url, transport):
            return "started"
    return "slow"
