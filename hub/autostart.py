"""Starting by itself (Q8): the hub at Windows login, llama-swap together with the hub.

- At login: a value in the user's Run key (HKCU\\...\\CurrentVersion\\Run, "AFO hub") that runs `pythonw -m hub` - no
  console, no administrator rights. Off until the user turns it on in Settings; off again removes the value. The value
  under the old name ("LocalChat hub", before 10.4b) becomes this one when the hub starts.
- llama-swap: when the hub starts and llama-swap does not answer on this computer, the hub starts it hidden and on
  its own - it keeps running when the hub stops, as the hub does when the window closes (D2). What it starts:
  LOCALCHAT_LLAMA_SWAP_START, else the script or llama-swap.exe chosen in Settings (10.3), else the start script beside
  the checkout on the computer it was built on, else the llama-swap.exe the first-run wizard put in AFO's engines
  (10.2) - an .exe gets the config (hub/llama_config.py) and the address. On unless the user turns it off in
  Settings. Its output goes to data/logs/llama-swap.out.log. When another program holds its port (Tomcat or a model
  of Strata on 8080), an .exe is told to listen on a free port after it, kept in the database (39.2, llama_swap_url,
  chosen at the hub's start); a script listens where it says - it is not started, and the hub says who holds the
  port (23.4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import psutil

from common.paths import PROJECT_ROOT, engines_home

from . import processes
from .db import utc_now

log = logging.getLogger("hub.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AFO hub"
OLD_RUN_VALUE = "LocalChat hub"  # before the name AFO (10.4b)
RUN_STORE_ENV = "LOCALCHAT_RUN_KEY"  # "memory": tests and throwaway runs never touch the registry
LLAMA_SWAP_START_ENV = "LOCALCHAT_LLAMA_SWAP_START"
DEFAULT_LLAMA_SWAP_START = PROJECT_ROOT.parent / "llama-swap" / "start-llama-swap.cmd"
LLAMA_SWAP_SETTING = "autostart.llama_swap"
START_SETTING = "llama_swap.start"  # the script or llama-swap.exe chosen in the window (10.3)
configured_start: Path | None = None  # the chosen one, as the hub read it from its settings
FOUND_START_SETTING = "llama_swap.found_start"  # the running llama-swap's program, kept
found_start: Path | None = None
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
READY_TIMEOUT_S = 60.0  # llama-swap itself starts in a second; models load only when asked
PORT_SETTING = "llama_swap.port"  # where AFO's own llama-swap moved when another program held its port (39.2)
PORTS_TRIED = 20  # the ports after the default one it may move to
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


class RunStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, command: str) -> None: ...
    def delete(self) -> None: ...


class RegistryRunStore:
    """A value in the user's Run key in the Windows registry."""

    def __init__(self, name: str = RUN_VALUE) -> None:
        self.name = name

    def get(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, self.name)[0])
        except FileNotFoundError:
            return None

    def set(self, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.name)
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


def default_run_store(name: str = RUN_VALUE) -> RunStore:
    if os.environ.get(RUN_STORE_ENV, "").lower() == "memory" or os.name != "nt":
        return MemoryRunStore()
    return RegistryRunStore(name)


def renamed(store: RunStore, old: RunStore) -> None:
    """The hub at login under the old name becomes AFO's, with the same command (10.4b)."""
    try:
        command = old.get()
        if command:
            if not store.get():
                store.set(command)
            old.delete()
    except OSError as exc:
        log.warning("the login value under the old name stays: %s", exc)


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
    """What starts llama-swap: a .cmd script or llama-swap.exe (see the module's text for the order)."""
    if os.environ.get(LLAMA_SWAP_START_ENV):
        return Path(os.environ[LLAMA_SWAP_START_ENV])
    if configured_start is not None:
        return configured_start
    if DEFAULT_LLAMA_SWAP_START.is_file():
        return DEFAULT_LLAMA_SWAP_START
    return found_start if found_start is not None else engines_home() / "llama-swap" / "llama-swap.exe"


def start_source() -> str:
    """Where llama_swap_script() comes from: env | chosen | found | default."""
    if os.environ.get(LLAMA_SWAP_START_ENV):
        return "env"
    if configured_start is not None:
        return "chosen"
    return "found" if found_start is not None and not DEFAULT_LLAMA_SWAP_START.is_file() else "default"


def _flag(args: list[str], name: str) -> str | None:
    """A Go program's flag: -name value, --name value, -name=value, --name=value."""
    for i, arg in enumerate(args):
        for prefix in (f"-{name}", f"--{name}"):
            if arg == prefix and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith(prefix + "="):
                return arg.split("=", 1)[1]
    return None


def running_llama_swap(url: str) -> tuple[Path, Path] | None:
    """The llama-swap running at `url`'s port - one the person ran before AFO came, say: its program and its config,
    from its command line (`-config` relative to its folder; `-listen` is :8080 when not given)."""
    port = str(urlsplit(url).port or 8080)
    for process in psutil.process_iter(["name", "exe", "cmdline", "cwd"]):
        info = process.info
        args = info.get("cmdline") or []
        if not (info.get("name") or "").lower().startswith("llama-swap") or not args:
            continue
        if (_flag(args, "listen") or ":8080").rsplit(":", 1)[-1] != port:
            continue
        config = Path(_flag(args, "config") or "config.yaml")
        if not config.is_absolute():
            config = Path(info.get("cwd") or ".") / config
        return Path(info.get("exe") or args[0]), config
    return None


def needs_finding() -> bool:
    """Neither the environment nor the window chose llama-swap's config or start, and the files the hub would use
    are not there: AFO's own engine is not installed (and none was found before)."""
    from .llama_config import config_path, config_source

    return (config_source() == "default" and not config_path().is_file()) or (
        start_source() == "default" and not llama_swap_script().is_file())  # fmt: skip


def keep_found(conn: sqlite3.Connection, found: tuple[Path, Path]) -> None:
    """The running llama-swap's program and config, kept: a hub started later uses them, and can start it itself."""
    from . import llama_config

    for key, path in ((FOUND_START_SETTING, found[0]), (llama_config.FOUND_SETTING, found[1])):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
            " value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(str(path)), utc_now()),
        )
    load_choices(conn)


async def find_running(db, url: str) -> bool:
    """When the hub needs it (needs_finding), the running llama-swap's program and config are found and kept."""
    if not needs_finding():
        return False
    found = await asyncio.to_thread(running_llama_swap, url)
    if found is None:
        return False
    await db.acall(lambda conn: keep_found(conn, found))
    log.info("llama-swap found running: %s with %s", found[0], found[1])
    return True


def start_command(script: Path, url: str) -> list[str]:
    """The command for a script, or for llama-swap.exe with the config and the address it should listen on."""
    if script.suffix.lower() != ".exe":
        return ["cmd", "/c", str(script)]
    from .llama_config import config_path

    parts = urlsplit(url)
    listen = f"{parts.hostname or '127.0.0.1'}:{parts.port or 8080}"
    return [str(script), "--config", str(config_path()), "--listen", listen, "--watch-config"]


def load_choices(conn: sqlite3.Connection) -> None:
    """The paths chosen in the window (10.3) into this module and llama_config (at the hub's start and on a change)."""
    from . import llama_config

    def chosen(key: str) -> Path | None:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        value = json.loads(row["value"]) if row else ""
        return Path(value) if value else None

    global configured_start, found_start
    configured_start = chosen(START_SETTING)
    found_start = chosen(FOUND_START_SETTING)
    llama_config.configured = chosen(llama_config.CONFIG_SETTING)
    llama_config.found = chosen(llama_config.FOUND_SETTING)


def set_choice(conn: sqlite3.Connection, key: str, path: str) -> None:
    """A path chosen in the window ('' - back to the default)."""
    if path:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
            " value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(path), utc_now()),
        )
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    load_choices(conn)


def llama_swap_wanted(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (LLAMA_SWAP_SETTING,)).fetchone()
    return json.loads(row["value"]) if row else True


def set_llama_swap_wanted(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (LLAMA_SWAP_SETTING, json.dumps(bool(on)), utc_now()),
    )


def port_taken(url: str) -> dict[str, Any] | None:
    """Another program than llama-swap listens on llama-swap's port here: {port, pid, name, exe} - llama-swap cannot
    start, and what answers there is not it (23.4). None: nobody, llama-swap, another computer's port, or a program
    whose name cannot be read (llama-swap run as an administrator, maybe)."""
    parts = urlsplit(url)
    if (parts.hostname or "") not in LOCAL_HOSTS:
        return None
    port = parts.port or 8080
    holder = processes.listener(port)
    if holder is None or not holder["name"] or holder["name"].lower().startswith("llama-swap"):
        return None
    return {"port": port, **holder}


def _with_port(url: str, port: int) -> str:
    parts = urlsplit(url)
    return parts._replace(netloc=f"{parts.hostname}:{port}").geturl()


def _kept_port(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (PORT_SETTING,)).fetchone()
    return json.loads(row["value"]) if row else None


def _keep_port(conn: sqlite3.Connection, port: int | None) -> None:
    if port is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (PORT_SETTING,))
        return
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (PORT_SETTING, json.dumps(port), utc_now()),
    )


def free_port(host: str, ports: list[int]) -> int | None:
    """The first of the ports nobody listens on and this process can bind (blocking: psutil and a socket)."""
    for port in ports:
        if processes.listener(port) is not None:
            continue
        with socket.socket() as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
        return port
    return None


async def llama_swap_url(db, url: str) -> str:
    """Where llama-swap listens (39.2): its default port - or, when another program holds it (Tomcat on 8080) and the
    llama-swap AFO starts is a program it tells where to listen (an .exe, not a script), a free port after it, kept
    in the database. AFO's llama-swap still running on the kept port (it outlives the hub, D2) is found there; with
    nothing there and the default free, back on the default."""
    from .resources import llama_swap_answers

    parts = urlsplit(url)
    if (parts.hostname or "") not in LOCAL_HOSTS:
        return url
    kept = await db.acall(_kept_port)
    if kept and await llama_swap_answers(_with_port(url, kept)):
        return _with_port(url, kept)
    if await asyncio.to_thread(port_taken, url) is None or llama_swap_script().suffix.lower() != ".exe":
        await db.acall(lambda conn: _keep_port(conn, None))
        return url
    default = parts.port or 8080
    ports = [p for p in [kept, *range(default + 1, default + 1 + PORTS_TRIED)] if p]
    port = await asyncio.to_thread(free_port, parts.hostname or "127.0.0.1", ports)
    if port is None:
        log.warning("port %s is taken and none of the next %s is free: llama-swap stays", default, PORTS_TRIED)
        return url
    await db.acall(lambda conn: _keep_port(conn, port))
    log.info("port %s is taken by another program: llama-swap listens on %s", default, port)
    return _with_port(url, port)


async def answers(url: str, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=transport) as http:
            response = await http.get(f"{url.rstrip('/')}/v1/models")
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def spawn(command: list[str], cwd: Path, log_file: Path) -> None:
    """The command on its own, hidden: it outlives the hub (D2). Inside a job that forbids leaving, it ends with it."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_file, "ab") as out:
        for extra in (BREAKAWAY, 0) if os.name == "nt" else (0,):
            try:
                subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 creationflags=flags | extra, close_fds=True)  # fmt: skip
                return
            except PermissionError:
                continue


async def ensure_llama_swap(url: str, script: Path, wanted: bool, log_file: Path, *, starter=spawn,
                            transport: httpx.AsyncBaseTransport | None = None,
                            ready_timeout: float = READY_TIMEOUT_S) -> str:  # fmt: skip
    """running (it answered), started (and now answers), slow (started, not answering yet), off, missing, remote,
    taken (another program holds its port: it answers there, if it does, and llama-swap could not listen)."""
    if (urlsplit(url).hostname or "") not in LOCAL_HOSTS:
        return "remote"
    taken = await asyncio.to_thread(port_taken, url)
    if taken is not None:
        log.warning("port %s is taken by %s (pid %s): llama-swap cannot listen there", taken["port"], taken["name"],
                    taken["pid"])  # fmt: skip
        return "taken"
    if await answers(url, transport):
        return "running"
    if not wanted:
        return "off"
    if not script.is_file():
        return "missing"
    log.info("llama-swap does not answer on %s: starting %s", url, script)
    await asyncio.to_thread(starter, start_command(script, url), script.parent, log_file)
    deadline = asyncio.get_running_loop().time() + ready_timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        if await answers(url, transport):
            return "started"
    return "slow"
