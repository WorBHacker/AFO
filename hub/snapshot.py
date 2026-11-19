"""A picture of a web page for a model that sees pictures (61.7): how a page its tasks made looks.

Microsoft Edge (in Windows 11 already) draws the page without a window - `--headless=new --screenshot` - with its own
empty profile in a temporary folder: the person's tabs, passwords and cookies are not touched, and the folder goes
when the picture is taken. Only this computer's pages - http(s) on localhost, 127.0.0.1 or [::1], not the ports of
AFO itself (the hub, llama-swap) - and the HTML files of a project or of a task's copy; sites of the internet are
web_fetch's (D19). Nothing leaves the computer while the page is drawn: every request but this computer's goes to a
proxy that is not there, so a page's fonts or pictures from the internet are not fetched. The page's scripts get a
few seconds (`--virtual-time-budget`) before the picture is taken."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .projects import ProjectError, resolve_inside, secret_file

TIMEOUT_S = 30.0  # Edge took 1-2 s for a page on this computer (2026-11-20)
SCRIPTS_MS = 3000  # the page's own time to run its scripts before the picture
WIDTH, HEIGHT = 1280, 800
SIZES = {"width": (320, 1920), "height": (240, 3000)}
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
PAGES = (".html", ".htm")
NOWHERE = "http://127.0.0.1:9"  # the proxy every request but this computer's goes to: nothing listens there
PNG = b"\x89PNG\r\n\x1a\n"

PAGE_SNAPSHOT: dict[str, Any] = {
    "name": "page_snapshot",
    "description": "A picture of a web page as a browser draws it - to see how a page your tasks made looks. A page "
    "of this computer (`url`: http://localhost:PORT/...) or an HTML file (`path`) of a task's copy (`task_id`) or of "
    "your project. The picture comes to you after the result. Not for sites of the internet: the page is drawn "
    "without them.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "A page of this computer: http://localhost:PORT/..."},
            "path": {"type": "string", "description": "An HTML file, as the task's or the project's files name it."},
            "task_id": {"type": "string", "description": "The task whose copy holds `path`, as your batch named it."},
            "project": {"type": "string", "description": "Which project, when there are several."},
            "width": {"type": "integer", "description": f"The window's width in pixels, {WIDTH} if not given."},
            "height": {"type": "integer", "description": f"The window's height in pixels, {HEIGHT} if not given."},
        },
    },
}
NAME = PAGE_SNAPSHOT["name"]


class SnapshotError(Exception):
    """The picture cannot be taken; the message is for the model."""


def edge() -> Path | None:
    """Edge's program: where Windows says it is (App Paths), else where it is put."""
    found: list[str] = []
    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
                with winreg.OpenKey(root, key) as opened:
                    found.append(str(winreg.QueryValueEx(opened, "")[0]))
            except OSError:
                continue
    except ImportError:  # not Windows
        pass
    for folder in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(folder):
            found.append(str(Path(os.environ[folder]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return next((Path(p.strip('"')) for p in found if p and Path(p.strip('"')).is_file()), None)


def page_url(url: Any, own_ports: set[int]) -> str:
    """A page of this computer the model may see, or SnapshotError: http(s), localhost or a loopback address, not a
    port of AFO itself."""
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SnapshotError("`url`: a page of this computer, http://localhost:PORT/... (an HTML file: `path`)")
    if parts.hostname.lower() not in LOOPBACK:
        raise SnapshotError(f"{parts.hostname} is not this computer: only pages of this computer are drawn here - "
                            "localhost, 127.0.0.1, [::1]; web_fetch reads sites of the internet")  # fmt: skip
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise SnapshotError(f"`url`: {exc}") from None
    if port in own_ports:
        raise SnapshotError(f"port {port} is AFO's own (the hub or llama-swap): its pages are not shown")
    return text


def page_file(root: Path, path: Any) -> str:
    """An HTML file inside `root` as a file:// address, or SnapshotError: inside, an HTML file, there, not secret."""
    rel = str(path or "").replace("\\", "/").strip().strip("/")
    if not rel:
        raise SnapshotError("`path`: the HTML file, as the files name it")
    if Path(rel).suffix.lower() not in PAGES:
        raise SnapshotError(f"{rel}: only an HTML file (.html, .htm) is drawn")
    if secret_file(root, rel):
        raise SnapshotError(f"{rel} is a file of secrets (D10): it is not shown")
    try:
        found = resolve_inside(root, rel)
    except ProjectError as exc:
        raise SnapshotError(str(exc)) from None
    if not found.is_file():
        raise SnapshotError(f"{rel}: there is no such file")
    return found.as_uri()


def size(args: dict[str, Any]) -> tuple[int, int]:
    """The window's width and height the model asked for, or SnapshotError."""
    out = []
    for name, default in (("width", WIDTH), ("height", HEIGHT)):
        value = args.get(name, default) or default
        low, high = SIZES[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise SnapshotError(f"`{name}`: a whole number from {low} to {high}")
        out.append(value)
    return out[0], out[1]


def take(page: str, width: int = WIDTH, height: int = HEIGHT, *, program: Path | None = None,
         timeout_s: float = TIMEOUT_S) -> bytes:  # fmt: skip
    """The PNG of `page` (blocking): Edge without a window, its own empty profile, the internet cut off. SnapshotError
    when Edge is not there, does not end in time or leaves no picture."""
    program = program or edge()
    if program is None:
        raise SnapshotError("Microsoft Edge is not found on this computer: page_snapshot needs it")
    folder = Path(tempfile.mkdtemp(prefix="afo-snapshot-"))
    shot = folder / "page.png"
    command = [str(program), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-sync", "--disable-background-networking",
               "--disable-component-update", "--hide-scrollbars", f"--proxy-server={NOWHERE}",
               f"--user-data-dir={folder / 'profile'}", f"--window-size={width},{height}",
               f"--virtual-time-budget={SCRIPTS_MS}", f"--screenshot={shot}", page]  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        edge_run = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=flags)  # fmt: skip
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise SnapshotError(f"Edge did not start: {exc}") from None
    try:
        edge_run.wait(timeout_s)
    except subprocess.TimeoutExpired:
        _kill(edge_run)
        raise SnapshotError(f"the page was not drawn in {timeout_s:.0f} s") from None
    finally:
        data = shot.read_bytes() if shot.is_file() else b""
        _remove(folder)
    if not data.startswith(PNG):
        raise SnapshotError("Edge drew no picture of the page")
    return data


def _kill(edge_run: subprocess.Popen) -> None:
    """Edge and the processes it started: a page that did not end in time."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(edge_run.pid)], capture_output=True, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)  # fmt: skip
    else:
        edge_run.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        edge_run.wait(5)


def _remove(folder: Path) -> None:
    """The temporary profile goes; a helper of Edge may hold a file of it a moment longer."""
    for _ in range(10):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.3)
