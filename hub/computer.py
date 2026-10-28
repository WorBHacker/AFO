"""Computer control (stage 9.6, D41): the autonomous agent sees a window of a program the user allowed (D43) as its
Windows UI Automation tree - names, roles, places, more reliable than pixels - and acts on it, under the user's
control.

The agent's tools: `screen_windows` - the windows of allowed programs open now; `screen_look` - a window's elements,
numbered, and with a question a snapshot of it shown to the model with vision (the role «vision», a local one) for
an answer in words; `screen_act` - click, type, toggle, select, expand, collapse, scroll an element (through UI
Automation's own patterns: no mouse, no keys, whatever is in front), keys or a click at a point (real input: only
with the window in front), focus, wait; `open_program` - start an allowed one. Looking goes without a question (an
autonomous run may look before its plan); acting in a program is asked once per task (the answer "for this task"
covers that program) - not at all in a run the user let go on by itself («Сам») - and a click on what sends, posts
or publishes - every time.

Never by the agent (D41): a password field (UIA IsPassword) - not read, not typed in; what looks like card or bank
details, a purchase, a payment or a transfer, accepting terms - the person does it. A window of a program not
allowed is not seen and not touched. One run controls the computer at a time; the person's own input (the mouse,
the keys) pauses it - the person comes first. Each action is in the journal (`computer_actions`) with a snapshot of
the window after it (`data/computer/<run>/`, kept as D30 says); events computer.control {run_id, window, action}
and computer.released {run_id} let the window draw its frame and its «stop».
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psutil

from . import model_settings, programs, store
from .db import Database
from .events import EventBus

log = logging.getLogger("hub.computer")

MAX_NODES = 250  # elements listed in one look
NAME_CHARS = 80
VALUE_CHARS = 200
WAIT_MAX_S = 30.0
HUMAN_QUIET_S = 3.0  # the person's input this recent pauses the agent
SNAPSHOT_MAX_SIDE = 1600

WINDOW = {"type": "integer", "description": "the window's id from screen_windows"}
TOOLS = [
    {
        "name": "screen_windows",
        "description": "The open windows of the programs the user allowed you to control: id, title, program. "
        "Windows of other programs are not shown and cannot be touched.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "screen_look",
        "description": "A window's elements in sight as UI Automation sees them, numbered [n]: role, name, value, "
        "state, place; (focus) marks where typed text and keys go. Use the numbers in screen_act. With `part`, only "
        "what is inside that element of your last look (when the look was cut). With `question`, a snapshot of the "
        "window goes to the model with vision and you get its answer in words (for what the tree does not say: a "
        "picture, a chart, colours).",
        "parameters": {
            "type": "object",
            "properties": {
                "window": WINDOW,
                "part": {"type": "integer", "description": "an element [n] of your last look of this window"},
                "question": {"type": "string"},
            },
            "required": ["window"],
        },
    },
    {
        "name": "screen_act",
        "description": "One action in a window. click / toggle / select / expand / collapse / scroll an element "
        "[n] from your last screen_look; type: put `text` into an element (replaces its value), or without "
        "`element` type it where the focus is; keys: a combination like 'ctrl+s', 'enter', 'tab', 'ctrl+shift+n'; "
        "click with `x`, `y` (inside the window, from its top left) when no element fits; focus: bring the window "
        "in front; wait: `seconds` (up to 30). Look again after actions that change the window. Password fields, "
        "payments and accepting terms are the user's to do.",
        "parameters": {
            "type": "object",
            "properties": {
                "window": WINDOW,
                "action": {
                    "type": "string",
                    "enum": [
                        "click",
                        "type",
                        "toggle",
                        "select",
                        "expand",
                        "collapse",
                        "scroll",
                        "keys",
                        "focus",
                        "wait",
                    ],
                },
                "element": {"type": "integer"},
                "text": {"type": "string"},
                "keys": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "scroll steps, 1-20"},
                "seconds": {"type": "number"},
            },
            "required": ["window", "action"],
        },
    },
    {
        "name": "open_program",
        "description": "Start a program the user allowed, by its name as screen_windows or the user calls it - or "
        "have it open a link of its own kind (`link`): Telegram opens tg://resolve?domain=NAME as the chat of exactly "
        "@NAME, surer than its search, which mixes in strangers. Web pages and files are not links here.",
        "parameters": {
            "type": "object",
            "properties": {
                "program": {"type": "string"},
                "link": {
                    "type": "string",
                    "description": "A link the program itself opens (its own scheme, like tg://).",
                },
            },
            "required": ["program"],
        },
    },
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)
READING = frozenset({"screen_windows", "screen_look"})

# what the agent never does itself (D41): the person does
FORBIDDEN = re.compile(
    r"парол|password|passcode|pin-?код|cvv|cvc|card\s*number|номер\s*карт|срок\s*действ|expir|iban|swift|бик\b"
    r"|оплат|оплатить|pay\b|payment|checkout|купить|buy\b|purchase|заказать|place\s*order|перевест|перевод\s*сред"
    r"|transfer|принимаю|принять\s*услов|accept\s*(the\s*)?terms|i\s*agree|согласен\s*с\s*услов",
    re.IGNORECASE,
)
# what sends from the person's name: asked every time (D41)
SENDS = re.compile(r"отправ|send\b|submit|опублик|publish|\bpost\b|ответить|reply|переслать|forward", re.IGNORECASE)
# a field Enter does not send from (a search, an address)
SEARCHES = re.compile(r"поиск|найти|search|find|фильтр|filter|адрес|address|url", re.IGNORECASE)
BLOCKED_KEYS = {"win", "lwin", "rwin", "meta", "super"}  # the Start menu and the shell are outside the program
BLOCKED_COMBOS = {("alt", "tab"), ("alt", "esc"), ("ctrl", "esc"), ("alt", "shift", "tab")}

ROLES = {
    50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox", 50004: "edit", 50005: "link",
    50006: "image", 50007: "listitem", 50008: "list", 50009: "menu", 50010: "menubar", 50011: "menuitem",
    50012: "progressbar", 50013: "radio", 50014: "scrollbar", 50015: "slider", 50016: "spinner", 50017: "statusbar",
    50018: "tabs", 50019: "tab", 50020: "text", 50021: "toolbar", 50022: "tooltip", 50023: "tree", 50024: "treeitem",
    50025: "custom", 50026: "group", 50027: "thumb", 50028: "grid", 50029: "dataitem", 50030: "document",
    50031: "splitbutton", 50032: "window", 50033: "pane", 50034: "header", 50035: "headeritem", 50036: "table",
    50037: "titlebar", 50038: "separator",
}  # fmt: skip
SKIPPED = {50037, 50014, 50027, 50038}  # the title bar's buttons, scroll bars' parts, separators: noise

# ---------------------------------------------------------------- Win32


class ScreenError(Exception):
    """What the model reads: why the action was not done."""


user32 = ctypes.windll.user32 if sys.platform == "win32" else None
gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def last_input_ms() -> int:
    """Milliseconds since the last input of anyone (the person's, or ours)."""
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """Two boxes (left, top, right, bottom): the part they share, None when none."""
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[2] > box[0] and box[3] > box[1] else None


LINK = re.compile(r"^([a-z][a-z0-9+.-]{1,30}):\S+$", re.IGNORECASE)
NOT_LINKS = frozenset({"http", "https", "file", "ftp", "javascript", "data", "vbscript", "about", "shell"})


def link_handler(scheme: str) -> str:
    """The .exe Windows opens a link scheme with (its shell\\open\\command), '' when none."""
    if sys.platform != "win32":
        return ""
    import winreg

    for hive, key in ((winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}\shell\open\command"),
                      (winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command")):  # fmt: skip
        try:
            with winreg.OpenKey(hive, key) as opened:
                command = str(winreg.QueryValueEx(opened, "")[0]).strip()
        except OSError:
            continue
        found = re.match(r'^"([^"]+)"|^(\S+\.exe)', command, re.IGNORECASE)
        if found:
            return os.path.expandvars(found.group(1) or found.group(2))
    return ""


def check_link(link: str, program: dict[str, Any], handler: str) -> None:
    """A link the agent may have an allowed program open: of that program's own scheme - Windows opens it with that
    very program - never a web page, a file or the shell."""
    match = LINK.match(link)
    if not match or match.group(1).lower() in NOT_LINKS:
        raise ScreenError("link: a program's own link (like tg://resolve?domain=NAME); web pages and files are not")
    if not handler or not program.get("exe") or programs.norm(handler) != programs.norm(program["exe"]):
        raise ScreenError(f"«{program['name']}» does not open {match.group(1)}: links")


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(wt.HWND(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wt.HWND(hwnd), buffer, length + 1)
    return buffer.value


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return pid.value


class _Guid(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> _Guid:
        h = text.strip("{}").replace("-", "")
        return cls(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", wt.DWORD)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.WORD), ("r2", wt.WORD), ("r3", wt.WORD), ("value", ctypes.c_void_p),
                ("pad", ctypes.c_void_p)]  # fmt: skip


IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
PKEY_APP_ID = ("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)  # PKEY_AppUserModel_ID


def window_aumid(hwnd: int) -> str:
    """The window's AppUserModelID - how a Store app (and some others) is known; '' when it has none. Plain COM
    through ctypes (the shell's IPropertyStore: GetValue is its sixth method)."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)  # this thread in COM's multithreaded apartment; a thread already in one stays
    store = ctypes.c_void_p()
    iid = _Guid.of(IID_PROPERTY_STORE)
    shell = ctypes.windll.shell32
    shell.SHGetPropertyStoreForWindow.argtypes = [wt.HWND, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
    if shell.SHGetPropertyStoreForWindow(wt.HWND(hwnd), ctypes.byref(iid), ctypes.byref(store)) != 0 or not store:
        return ""
    vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PropertyKey),
                                   ctypes.POINTER(_PropVariant))(vtable[5])  # fmt: skip
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    key = _PropertyKey(_Guid.of(PKEY_APP_ID[0]), PKEY_APP_ID[1])
    value = _PropVariant()
    try:
        if get_value(store, ctypes.byref(key), ctypes.byref(value)) != 0 or value.vt != 31:  # VT_LPWSTR
            return ""
        return ctypes.wstring_at(value.value) or ""
    finally:
        ole32.PropVariantClear(ctypes.byref(value))
        release(store)


def process_exe(pid: int) -> str:
    try:
        return psutil.Process(pid).exe()
    except psutil.Error, OSError:
        return ""


def cloaked(hwnd: int) -> bool:
    """A window Windows keeps out of sight though it is "visible": the input host's full-screen layer, a suspended
    Store app, a window on another virtual desktop. The person does not see it; the agent does not get it."""
    value = ctypes.c_int()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(value), 4):  # DWMWA_CLOAKED
        return False
    return value.value != 0


def _cmdline(pid: int) -> list[str]:
    try:
        return psutil.Process(pid).cmdline()
    except psutil.Error, OSError:
        return []


def is_localchat(pid: int) -> bool:
    """A window of LocalChat itself - the hub, the window (`-m app`), an installed LocalChat.exe: never the agent's
    to control, whatever programs are allowed (it would answer its own approval cards)."""
    if pid == os.getpid():
        return True
    parts = [p.lower() for p in _cmdline(pid)]
    if any(p.endswith("localchat.exe") for p in parts):
        return True
    return any(p == "-m" and i + 1 < len(parts) and parts[i + 1] in ("app", "hub") for i, p in enumerate(parts))


def top_windows() -> list[dict[str, Any]]:
    """Visible top-level windows with a title, front first: id, title, pid, exe, aumid, place."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and not cloaked(int(hwnd)):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(each, 0)
    out = []
    for hwnd in found:
        pid = window_pid(hwnd)
        out.append({"id": hwnd, "title": window_title(hwnd), "pid": pid, "exe": process_exe(pid),
                    "aumid": window_aumid(hwnd), "rect": window_rect(hwnd),
                    "minimized": bool(user32.IsIconic(wt.HWND(hwnd)))})  # fmt: skip
    return out


def visible_rect(hwnd: int) -> tuple[int, int, int, int]:
    """The window as the person sees it - without the invisible resize borders GetWindowRect counts (DWM)."""
    rect = wt.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)):
        return window_rect(hwnd)  # DWMWA_EXTENDED_FRAME_BOUNDS refused: the whole rectangle
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def dpi_aware() -> None:
    """Real pixels everywhere - windows, UI Automation, SendInput - whatever each monitor's scale."""
    if user32 is not None:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2; refused once set


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def bring_to_front(hwnd: int) -> bool:
    """Windows lets a process take the front only after input of its own: a lone Alt press first (the usual way)."""
    if user32.IsIconic(wt.HWND(hwnd)):
        user32.ShowWindow(wt.HWND(hwnd), 9)  # SW_RESTORE
    if foreground() != hwnd:
        user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down, up
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(wt.HWND(hwnd))
    for _ in range(20):
        if foreground() == hwnd:
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- snapshots


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]  # fmt: skip


def png(width: int, height: int, bgra: bytes) -> bytes:
    """A PNG of top-down BGRA pixels (the standard library's zlib: no imaging package)."""
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride : (y + 1) * stride]) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def snapshot(hwnd: int) -> bytes:
    """The window as it draws itself (PrintWindow, PW_RENDERFULLCONTENT) - even behind others; PNG."""
    _x, _y, width, height = window_rect(hwnd)
    if width <= 0 or height <= 0:
        raise ScreenError("the window has no size (minimized?): focus it first")
    width, height = min(width, SNAPSHOT_MAX_SIDE), min(height, SNAPSHOT_MAX_SIDE)
    window_dc = user32.GetWindowDC(wt.HWND(hwnd))
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old = gdi32.SelectObject(memory_dc, bitmap)
    try:
        user32.PrintWindow(wt.HWND(hwnd), memory_dc, 2)
        info = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        return png(width, height, buffer.raw)
    finally:
        gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(wt.HWND(hwnd), window_dc)


# ---------------------------------------------------------------- real input (only with the window in front)

VK = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
      "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "ctrl": 0x11, "control": 0x11, "shift": 0x10,
      "alt": 0x12, **{f"f{n}": 0x6F + n for n in range(1, 13)}}  # fmt: skip
MODIFIERS = {"ctrl", "control", "shift", "alt"}


class _KeyInput(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]  # fmt: skip


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]  # fmt: skip


class _Union(ctypes.Union):
    _fields_ = [("ki", _KeyInput), ("mi", _MouseInput), ("pad", ctypes.c_byte * 32)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _Union)]


def _send(*items: _Input) -> None:
    array = (_Input * len(items))(*items)
    user32.SendInput(len(items), array, ctypes.sizeof(_Input))


def _key(vk: int = 0, scan: int = 0, up: bool = False, unicode: bool = False) -> _Input:
    flags = (0x0002 if up else 0) | (0x0004 if unicode else 0)
    return _Input(1, _Union(ki=_KeyInput(vk, scan, flags, 0, 0)))


def parse_keys(text: str) -> list[int]:
    """'ctrl+shift+s' -> virtual keys; the Windows key and switching away (alt+tab...) are refused."""
    names = [k.strip().lower() for k in str(text or "").split("+") if k.strip()]
    if not names:
        raise ScreenError("keys: a combination like 'ctrl+s' or 'enter'")
    if BLOCKED_KEYS & set(names) or tuple(names) in BLOCKED_COMBOS:
        raise ScreenError(f"not done: {text} leaves the program (the Windows key, switching windows)")
    codes = []
    for name in names:
        if name in VK:
            codes.append(VK[name])
        elif len(name) == 1 and name.isascii() and name.isalnum():
            codes.append(ord(name.upper()))
        else:
            raise ScreenError(f"keys: unknown key {name!r}")
    return codes


def enter_only(text: str) -> bool:
    """Enter, or ctrl+Enter - what sends a chat's message; shift+Enter is a new line."""
    names = [k.strip().lower() for k in text.split("+") if k.strip()]
    return bool(names) and names[-1] in ("enter", "return") and set(names[:-1]) <= {"ctrl", "control"}


def press(codes: list[int]) -> None:
    _send(*[_key(c) for c in codes], *[_key(c, up=True) for c in reversed(codes)])


def type_text(text: str) -> None:
    items = []
    for unit in struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le")):
        items += [_key(scan=unit, unicode=True), _key(scan=unit, unicode=True, up=True)]
    for at in range(0, len(items), 200):
        _send(*items[at : at + 200])


def click_at(x: int, y: int) -> None:
    """A left click at a screen point (absolute, all monitors: MOUSEEVENTF_VIRTUALDESK)."""
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    nx, ny = int((x - left) * 65535 / max(width - 1, 1)), int((y - top) * 65535 / max(height - 1, 1))
    move = 0x0001 | 0x8000 | 0x4000  # MOVE | ABSOLUTE | VIRTUALDESK
    _send(_Input(0, _Union(mi=_MouseInput(nx, ny, 0, move, 0, 0))),
          _Input(0, _Union(mi=_MouseInput(nx, ny, 0, 0x0002 | move, 0, 0))),
          _Input(0, _Union(mi=_MouseInput(nx, ny, 0, 0x0004 | move, 0, 0))))  # fmt: skip


def wheel(steps: int, horizontal: bool = False) -> None:
    flag = 0x1000 if horizontal else 0x0800
    _send(_Input(0, _Union(mi=_MouseInput(0, 0, ctypes.c_ulong(steps * 120).value, flag, 0, 0))))


# ---------------------------------------------------------------- UI Automation, in a thread of its own


class Uia:
    """UI Automation through comtypes, every call in one thread of its own (COM, multithreaded apartment)."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia", initializer=self._start)
        self.U: Any = None
        self.uia: Any = None

    def _start(self) -> None:
        sys.coinit_flags = 0  # COINIT_MULTITHREADED, read by comtypes when it is first imported in this thread
        import comtypes
        import comtypes.client

        comtypes.CoInitializeEx(0)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        self.U = UIAutomationClient
        self.uia = comtypes.client.CreateObject(UIAutomationClient.CUIAutomation,
                                                interface=UIAutomationClient.IUIAutomation)  # fmt: skip

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- in the thread ---

    def elements(self, hwnd: int, part: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        """The window's elements in the control view, depth first: role, name, value, state, place, and the
        element itself (for the actions) - those in sight: an element outside the window, or scrolled out of its
        list (outside its parent), is counted, not listed, with all inside it (a chat's whole history used to fill
        the look before its input field was reached). `part`: an element of the last look - only what is inside it.
        The element with the focus is marked; one the cut left out is added at the end."""
        window = self.uia.ElementFromHandle(wt.HWND(hwnd))
        walker = self.uia.ControlViewWalker
        wx, wy, ww, wh = window_rect(hwnd)
        out: list[dict[str, Any]] = []
        hidden = 0

        def add(element: Any, depth: int, clip: tuple[int, int, int, int]) -> None:
            nonlocal hidden
            child = walker.GetFirstChildElement(element)
            while child and len(out) < MAX_NODES:
                role = child.CurrentControlType
                rect = child.CurrentBoundingRectangle
                box = (rect.left, rect.top, rect.right, rect.bottom)
                sized = box[2] > box[0] and box[3] > box[1]
                inner = overlap(box, clip) if sized else clip  # a group of no size: its children say where they are
                if inner is None:
                    hidden += 1
                elif role not in SKIPPED:
                    out.append(self._describe(child, role, box, wx, wy, depth))
                    add(child, depth + 1, inner)
                child = walker.GetNextSiblingElement(child)

        frame = (wx, wy, wx + ww, wy + wh)
        if part is None:
            add(window, 0, frame)
        else:
            x, y, w, h = part["place"]
            add(part["element"], 0, overlap((wx + x, wy + y, wx + x + w, wy + y + h), frame) or frame)
        self._mark_focus(out, window, frame)
        return out, hidden

    def _describe(self, element: Any, role: int, box: tuple[int, int, int, int], wx: int, wy: int,
                  depth: int) -> dict[str, Any]:  # fmt: skip
        U = self.U
        item = {"role": ROLES.get(role, "element"), "name": " ".join(element.CurrentName.split()),
                "password": bool(element.CurrentIsPassword), "enabled": bool(element.CurrentIsEnabled),
                "place": (box[0] - wx, box[1] - wy, box[2] - box[0], box[3] - box[1]), "depth": depth,
                "element": element, "value": None, "toggled": None, "focus": False}  # fmt: skip
        if not item["password"]:
            value = element.GetCurrentPattern(U.UIA_ValuePatternId)
            if value:
                item["value"] = value.QueryInterface(U.IUIAutomationValuePattern).CurrentValue
        toggle = element.GetCurrentPattern(U.UIA_TogglePatternId)
        if toggle:
            item["toggled"] = toggle.QueryInterface(U.IUIAutomationTogglePattern).CurrentToggleState == 1
        return item

    def _mark_focus(self, out: list[dict[str, Any]], window: Any, frame: tuple[int, int, int, int]) -> None:
        focused = self.uia.GetFocusedElement()
        if not focused or focused.CurrentProcessId != window.CurrentProcessId:
            return
        rect = focused.CurrentBoundingRectangle
        box = (rect.left, rect.top, rect.right, rect.bottom)
        if overlap(box, frame) is None:
            return
        item = self._describe(focused, focused.CurrentControlType, box, frame[0], frame[1], 0)
        same = next((i for i in out if (i["role"], i["name"], i["place"]) == (item["role"], item["name"],
                                                                              item["place"])), None)  # fmt: skip
        if same is not None:
            same["focus"] = True
        elif len(out) >= MAX_NODES:  # the cut left it out: where the keys go is worth a line all the same
            out.append({**item, "focus": True})

    def focused_field(self, hwnd: int) -> dict[str, Any] | None:
        """Where keys go in this window: None when the focus is not in it (unknown); else whether it is a field that
        takes text (not a password), its name and its text (value None: it does not say)."""
        U = self.U
        focused = self.uia.GetFocusedElement()
        window = self.uia.ElementFromHandle(wt.HWND(hwnd))
        if not focused or focused.CurrentProcessId != window.CurrentProcessId:
            return None
        role = ROLES.get(focused.CurrentControlType, "element")
        name = " ".join(focused.CurrentName.split())
        value = None
        found = None if focused.CurrentIsPassword else focused.GetCurrentPattern(U.UIA_ValuePatternId)
        if found:
            pattern = found.QueryInterface(U.IUIAutomationValuePattern)
            value = None if pattern.CurrentIsReadOnly else pattern.CurrentValue
        text = not focused.CurrentIsPassword and (role in ("edit", "document") or value is not None)
        return {"text": text, "role": role, "name": name, "value": value}

    def act(self, item: dict[str, Any], action: str, text: str = "", direction: str = "", amount: int = 1) -> str:
        """An element's own pattern: no mouse and no keys. '' when it has none for this action."""
        U, element = self.U, item["element"]

        def pattern(pid: int, iface: Any) -> Any:
            found = element.GetCurrentPattern(pid)
            return found.QueryInterface(iface) if found else None

        if action == "click":
            for pid, iface, call in (
                (U.UIA_InvokePatternId, U.IUIAutomationInvokePattern, lambda p: p.Invoke()),
                (U.UIA_TogglePatternId, U.IUIAutomationTogglePattern, lambda p: p.Toggle()),
                (U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern, lambda p: p.Select()),
                (U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern, lambda p: p.Expand()),
                (U.UIA_LegacyIAccessiblePatternId, U.IUIAutomationLegacyIAccessiblePattern,
                 lambda p: p.DoDefaultAction()),
            ):  # fmt: skip
                found = pattern(pid, iface)
                if found is not None:
                    call(found)
                    return "clicked"
            return ""
        if action == "type":
            found = pattern(U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
            if found is None or found.CurrentIsReadOnly:
                return ""
            with contextlib.suppress(Exception):  # the focus there too: Enter after typing goes to this field
                element.SetFocus()
            found.SetValue(text)
            return "typed"
        if action == "toggle":
            found = pattern(U.UIA_TogglePatternId, U.IUIAutomationTogglePattern)
            if found is None:
                return ""
            found.Toggle()
            return "toggled: " + ("on" if found.CurrentToggleState == 1 else "off")
        if action == "select":
            found = pattern(U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern)
            if found is None:
                return ""
            found.Select()
            return "selected"
        if action in ("expand", "collapse"):
            found = pattern(U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern)
            if found is None:
                return ""
            found.Expand() if action == "expand" else found.Collapse()
            return action + "ed"
        if action == "scroll":
            found = pattern(U.UIA_ScrollPatternId, U.IUIAutomationScrollPattern)
            if found is None:
                return ""
            step = {"up": 1, "down": 4, "left": 1, "right": 4}[direction or "down"]  # small decrement / increment
            for _ in range(max(1, min(amount, 20))):
                if direction in ("left", "right"):
                    found.Scroll(step, 2)  # ScrollAmount_NoAmount vertically
                else:
                    found.Scroll(2, step)
            return "scrolled"
        return ""

    def focused_is_password(self) -> bool:
        focused = self.uia.GetFocusedElement()
        return bool(focused and focused.CurrentIsPassword)

    def focus_element(self, item: dict[str, Any]) -> None:
        item["element"].SetFocus()


def tree_text(window: dict[str, Any], items: list[dict[str, Any]], hidden: int = 0,
              part: dict[str, Any] | None = None) -> str:  # fmt: skip
    """The look as the model reads it: one numbered line per element; what was cut or is out of sight, and how to
    see it."""
    head = f"Window {window['id']} «{window['title']}» - {window['program']}"
    if part is not None:
        head += f", inside {part['role']} «{part['name'][:NAME_CHARS]}»"
    head += f"; {len(items)} elements"
    if len(items) >= MAX_NODES:
        head += f" (the first {MAX_NODES}: for the rest look inside a part - screen_look with part=[n])"
    if hidden:
        head += f"; {hidden} more out of sight (scrolled away or outside the window) - scroll to see them"
    lines = [head + ":"]
    for n, item in enumerate(items, 1):
        line = f"{'  ' * min(item['depth'], 8)}[{n}] {item['role']}"
        if item["name"]:
            line += f' "{item["name"][:NAME_CHARS]}"'
        if item["password"]:
            line += " (password field: the user's, not yours)"
        elif item["value"] not in (None, "") and item["value"] != item["name"]:
            line += f' value="{" ".join(str(item["value"]).split())[:VALUE_CHARS]}"'
        if item["toggled"] is not None:
            line += " [on]" if item["toggled"] else " [off]"
        if not item["enabled"]:
            line += " (disabled)"
        if item.get("focus"):
            line += " (focus)"
        x, y, w, h = item["place"]
        lines.append(f"{line} at {x},{y} {w}x{h}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the journal


def record(conn: sqlite3.Connection, run_id: str, window: dict[str, Any], action: dict[str, Any], result: str,
           shot: str) -> int:  # fmt: skip
    cur = conn.execute(
        "INSERT INTO computer_actions (conversation_id, window_title, program, action, result, snapshot)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, window["title"], window["program"], json.dumps(action, ensure_ascii=False), result, shot),
    )
    return int(cur.lastrowid)


def journal(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM computer_actions WHERE conversation_id = ? ORDER BY id", (run_id,)).fetchall()
    return [{**dict(r), "action": json.loads(r["action"])} for r in rows]


# ---------------------------------------------------------------- the agent's eyes and hands


Ask = Callable[[str, dict[str, Any], bool], Awaitable[str | None]]  # (tool, shown, cover) -> None or why not
See = Callable[[str, bytes, str], Awaitable[str]]  # (run_id, png, question) -> the vision model's answer


def vision_eyes(db: Database, gateway: Any) -> See:
    """The role «vision» (D39, Q20): a local model with vision answers a question about a snapshot. A cloud one
    never sees the screen here."""

    async def see(_run_id: str, picture: bytes, question: str) -> str:
        def pick(conn):
            roles = model_settings.current(conn, model_settings.ALL)[1].get("roles") or {}
            return store.get_model(conn, roles["vision"]) if roles.get("vision") else None

        model = await db.acall(pick)
        if model is None or not model["enabled"]:
            return "(no model with vision is set: the user picks one for the role «vision» in the models' settings)"
        if model["locality"] != "local":
            return "(the model for «vision» is a cloud one: the screen is shown to a local model only)"
        if not (model.get("capabilities") or {}).get("vision"):
            return f"({model['name']} does not take pictures: the role «vision» needs one that does)"
        provider, ref = gateway.resolve(model["id"])
        image = "data:image/png;base64," + base64.b64encode(picture).decode("ascii")
        prompt = [{"role": "user", "content": [
            {"type": "text", "text": f"This is a snapshot of a program's window. {question}\n"
                                     "Answer briefly, only from what you see."},
            {"type": "image_url", "image_url": {"url": image}}]}]  # fmt: skip
        said: list[str] = []
        async for event in provider.stream_chat(ref, prompt, "low", asyncio.Event()):
            if event.kind == "content":
                said.append(event.text)
        return "".join(said).strip() or "(the vision model said nothing)"

    return see


class Computer:
    """The autonomous agent's hands and eyes on the desktop: one run at a time, the person first."""

    def __init__(self, db: Database, bus: EventBus, folder: Path, *, see: See | None = None,
                 watch_person: bool = True, still_running: Callable[[str], bool] | None = None,
                 watched: Callable[[], bool] | None = None):  # fmt: skip
        self._db = db
        self._bus = bus
        self._folder = folder
        self._see = see
        self._watch = watch_person
        self._running = still_running or (lambda _run: True)
        self._watched = watched or (lambda: True)  # the window is open: the person sees the frame, has «stop»
        dpi_aware()
        self._uia: Uia | None = None
        self._holder: str | None = None  # the run controlling the computer now
        self._looks: dict[tuple[str, int], list[dict[str, Any]]] = {}  # (run, window) -> its last look's elements
        self._ours_at = 0.0  # when our own last real input was sent (time.monotonic)

    def _uia_now(self) -> Uia:
        if self._uia is None:
            self._uia = Uia()
        return self._uia

    def close(self) -> None:
        if self._uia is not None:
            self._uia.close()

    async def release(self, run_id: str) -> None:
        """The run ended (or was stopped): its hold and its looks go."""
        self._looks = {k: v for k, v in self._looks.items() if k[0] != run_id}
        if self._holder == run_id:
            self._holder = None
            await self._bus.publish("computer.released", {"run_id": run_id}, persist=False)

    # --- what the agent may see ---

    async def windows(self) -> list[dict[str, Any]]:
        """The windows of allowed programs, each with its program's name."""
        found = await asyncio.to_thread(top_windows)

        found = await asyncio.to_thread(lambda: [w for w in found if not is_localchat(w["pid"])])

        def allowed(conn) -> list[dict[str, Any]]:
            out = []
            for w in found:
                program = programs.allowed_program(conn, exe=w["exe"], aumid=w["aumid"])
                if program is not None:
                    out.append({**w, "program": program["name"], "program_id": program["id"]})
            return out

        return await self._db.acall(allowed)

    async def window(self, hwnd: Any) -> dict[str, Any]:
        try:
            hwnd = int(hwnd)
        except (TypeError, ValueError) as exc:
            raise ScreenError("window: the id from screen_windows") from exc
        found = next((w for w in await self.windows() if w["id"] == hwnd), None)
        if found is None:
            raise ScreenError(f"no window {hwnd} of an allowed program is open now: see screen_windows")
        return found

    # --- the tools ---

    async def handle(self, run_id: str, call: dict[str, Any], cancel: asyncio.Event, ask: Ask) -> tuple[str, bool]:
        args = call["input"] if isinstance(call["input"], dict) else {}
        try:
            match call["name"]:
                case "screen_windows":
                    return self._list(await self.windows()), False
                case "screen_look":
                    return await self._look(run_id, args), False
                case "screen_act":
                    return await self._act(run_id, args, cancel, ask), False
                case "open_program":
                    return await self._open(run_id, args, ask), False
        except ScreenError as exc:
            return str(exc), True
        except OSError as exc:
            return f"not done: {exc}", True
        except Exception as exc:  # a COM error: the element went away, the window closed
            log.warning("computer %s: %s", call["name"], exc)
            return f"not done: the window changed or closed ({type(exc).__name__}); look again", True
        return f"unknown tool {call['name']}", True

    def _list(self, found: list[dict[str, Any]]) -> str:
        if not found:
            return ("No window of an allowed program is open. open_program starts one the user allowed; the user "
                    "decides which programs you may control (Settings → Programs for the agent).")  # fmt: skip
        lines = [f"- {w['id']}: «{w['title']}» - {w['program']}" + (" (minimized)" if w["minimized"] else "")
                 for w in found]  # fmt: skip
        return "Windows you may control (id: title - program):\n" + "\n".join(lines)

    async def _look(self, run_id: str, args: dict[str, Any]) -> str:
        window = await self.window(args.get("window"))
        part = self._item(run_id, window, {"element": args.get("part")})
        uia = self._uia_now()
        items, hidden = await uia.run(uia.elements, window["id"], part)
        self._looks[(run_id, window["id"])] = items
        text = tree_text(window, items, hidden, part)
        question = " ".join(str(args.get("question") or "").split())
        if question:
            if self._see is None:
                return text + "\n\n(no model with vision is set: the user picks one for the role «vision»)"
            picture = await asyncio.to_thread(snapshot, window["id"])
            answer = await self._see(run_id, picture, question)
            text += f"\n\nThe vision model, on a snapshot of the window, to «{question}»:\n{answer}"
        return text

    async def _check_person(self, run_id: str) -> None:
        """The person's own input just now: the agent waits (D41)."""
        if not self._watch:
            return
        idle = await asyncio.to_thread(last_input_ms)
        ours_ago = time.monotonic() - self._ours_at
        if idle / 1000 < HUMAN_QUIET_S and idle / 1000 < ours_ago - 0.5:
            await self._bus.publish("computer.paused", {"run_id": run_id, "why": "person"})
            raise ScreenError("not done: the user is using the computer right now. Wait (screen_act wait) and try "
                              "again; do not fight for the mouse.")  # fmt: skip

    async def _hold(self, run_id: str) -> None:
        if not self._watched():
            raise ScreenError("not done: the LocalChat window is not open - only there the user sees the frame and "
                              "has «stop» (D41). Ask the user to open it.")  # fmt: skip
        if self._holder and self._holder != run_id and self._running(self._holder):
            raise ScreenError("not done: another task controls the computer now; one at a time (D41)")
        self._holder = run_id

    async def _allow(self, run_id: str, window: dict[str, Any], args: dict[str, Any], item: dict | None,
                     ask: Ask) -> None:  # fmt: skip
        """Never: password fields, payments, terms. Sending: asked every time. The rest: asked once per program."""
        name = (item or {}).get("name", "")
        text = str(args.get("text") or "")
        if item is not None and item["password"]:
            raise ScreenError("not done: a password field is the user's (D41) - ask the user to fill it in")
        if args.get("action") == "type" and item is None and await self._focused_password():
            raise ScreenError("not done: the focus is in a password field - the user's (D41)")
        if FORBIDDEN.search(name) or (args.get("action") == "type" and FORBIDDEN.search(text)):
            raise ScreenError("not done: passwords, card or bank details, payments, purchases and accepting terms "
                              "are the user's to do (D41) - ask the user")  # fmt: skip
        shown = {"program": window["program"], "window": window["title"], "action": args.get("action"),
                 "element": name, "text": text[:300], "keys": args.get("keys") or ""}  # fmt: skip
        sends = args.get("action") == "click" and bool(SENDS.search(name))
        if args.get("action") == "keys" and enter_only(str(args.get("keys") or "")):
            # Enter in a field with text sends it (a chat, a form), as the send button would: asked the same. The
            # window first comes in front - its own focus is where Enter will go; unknown is asked too.
            await asyncio.to_thread(bring_to_front, window["id"])
            uia = self._uia_now()
            field = await uia.run(uia.focused_field, window["id"])
            if field is None:
                sends = True
                shown = {**shown, "element": "окно не впереди — куда уйдёт Enter, не видно"}
            elif field["text"] and field["value"] != "" and not SEARCHES.search(field["name"]):
                sends = True
                shown = {**shown, "element": field["name"] or "поле ввода", "text": str(field["value"] or "")[:300]}
        why = await ask("screen_send" if sends else "screen_act", shown, not sends)
        if why is not None:
            raise ScreenError(why)

    async def _focused_password(self) -> bool:
        uia = self._uia_now()
        return bool(await uia.run(uia.focused_is_password))

    def _item(self, run_id: str, window: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
        if args.get("element") in (None, ""):
            return None
        items = self._looks.get((run_id, window["id"]))
        if not items:
            raise ScreenError("look at the window first (screen_look): the numbers come from it")
        try:
            return items[int(args["element"]) - 1]
        except (ValueError, IndexError) as exc:
            raise ScreenError(f"no element [{args['element']}] in your last look: look again") from exc

    async def _act(self, run_id: str, args: dict[str, Any], cancel: asyncio.Event, ask: Ask) -> str:
        action = str(args.get("action") or "")
        if action == "wait":
            seconds = max(0.0, min(float(args.get("seconds") or 1), WAIT_MAX_S))
            with_cancel = asyncio.ensure_future(cancel.wait())
            await asyncio.wait({with_cancel}, timeout=seconds)
            with_cancel.cancel()
            return f"waited {seconds:g} s"
        window = await self.window(args.get("window"))
        item = self._item(run_id, window, args)
        if (
            action in ("click", "toggle", "select", "expand", "collapse", "scroll")
            and item is None
            and not (action == "click" and args.get("x") is not None and args.get("y") is not None)
        ):
            raise ScreenError(f"{action}: which element ([n] from screen_look)")
        if action not in ("click", "type", "toggle", "select", "expand", "collapse", "scroll", "keys", "focus"):
            raise ScreenError(f"unknown action {action!r}")
        if action == "keys":
            parse_keys(str(args.get("keys") or ""))  # a combination that leaves the program: refused before anything
        await self._hold(run_id)
        await self._check_person(run_id)
        await self._allow(run_id, window, args, item, ask)
        rect = await asyncio.to_thread(visible_rect, window["id"])
        await self._bus.publish("computer.control", {"run_id": run_id, "window": {
            "id": window["id"], "title": window["title"], "rect": list(rect)}, "program": window["program"],
            "action": action}, persist=False)  # fmt: skip
        result = await self._do(window, action, args, item)
        await self._journal(run_id, window, args, item, result)
        return f"{result}. Look again to see the window now."

    async def _do(self, window: dict[str, Any], action: str, args: dict[str, Any], item: dict | None) -> str:
        uia = self._uia_now()
        if item is not None and action != "keys":
            done = await uia.run(uia.act, item, action, str(args.get("text") or ""), str(args.get("direction") or ""),
                                 int(args.get("amount") or 1))  # fmt: skip
            if done:
                return f"{done} [{args['element']}] «{item['name']}»"
            if action not in ("click", "type", "scroll"):
                raise ScreenError(f"{action}: element [{args['element']}] cannot do that")
        # real input: only into this window, in front
        if not await asyncio.to_thread(bring_to_front, window["id"]):
            raise ScreenError("not done: Windows did not let the window come in front; ask the user to click it")
        self._ours_at = time.monotonic()
        if action == "focus":
            return "the window is in front"
        if action == "keys":
            codes = parse_keys(str(args.get("keys") or ""))
            await asyncio.to_thread(press, codes)
            return f"pressed {args.get('keys')}"
        if action == "type":
            if item is not None:
                await uia.run(uia.focus_element, item)
            if await self._focused_password():
                raise ScreenError("not done: the focus is in a password field - the user's (D41)")
            await asyncio.to_thread(type_text, str(args.get("text") or ""))
            return "typed where the focus is"
        _wx, _wy, ww, wh = window["rect"]
        if item is not None:  # the middle of what is in sight of it: a long list reaches far beyond the window
            px, py, pw, ph = item["place"]
            seen = overlap((px, py, px + pw, py + ph), (0, 0, ww, wh)) or (px, py, px + pw, py + ph)
            x, y = (seen[0] + seen[2]) // 2, (seen[1] + seen[3]) // 2
        else:
            x, y = int(args["x"]), int(args["y"])
        if not (0 <= x < ww and 0 <= y < wh):
            raise ScreenError(f"{x},{y} is outside the window ({ww}x{wh})")
        if action == "scroll":
            await asyncio.to_thread(click_at, window["rect"][0] + x, window["rect"][1] + y)
            steps = max(1, min(int(args.get("amount") or 3), 20))
            sign = 1 if (args.get("direction") or "down") in ("up", "left") else -1
            await asyncio.to_thread(wheel, sign * steps, args.get("direction") in ("left", "right"))
            return f"scrolled {args.get('direction') or 'down'}"
        await asyncio.to_thread(click_at, window["rect"][0] + x, window["rect"][1] + y)
        return f"clicked at {x},{y}"

    async def _journal(self, run_id: str, window: dict[str, Any], args: dict[str, Any], item: dict | None,
                       result: str) -> None:  # fmt: skip
        """The action, and a snapshot of the window after it (D41)."""
        shot = ""
        try:
            await asyncio.sleep(0.3)  # the window draws what changed
            picture = await asyncio.to_thread(snapshot, window["id"])
            folder = self._folder / re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{len(list(folder.iterdir())) + 1}.png"
            path.write_bytes(picture)
            shot = str(path)
        except (OSError, ScreenError) as exc:
            log.info("no snapshot after the action: %s", exc)
        action = {k: v for k, v in args.items() if k != "text"} | ({"text": "…"} if args.get("text") else {})
        if item is not None:
            action["element_name"] = item["name"]
        await self._db.acall(lambda conn: record(conn, run_id, window, action, result, shot))

    async def _open(self, run_id: str, args: dict[str, Any], ask: Ask) -> str:
        wanted = " ".join(str(args.get("program") or "").split()).casefold()
        if not wanted:
            raise ScreenError("open_program: the program's name")

        def find(conn):
            rows = [p for p in programs.list_programs(conn) if p.get("decision") == "allow"]
            return next((p for p in rows if p["name"].casefold() == wanted), None) or next(
                (p for p in rows if wanted in p["name"].casefold()), None)  # fmt: skip

        program = await self._db.acall(find)
        if program is None:
            raise ScreenError(f"no allowed program «{args.get('program')}»: the user decides which (D43)")
        link = str(args.get("link") or "").strip()
        if link:
            check_link(link, program, await asyncio.to_thread(link_handler, link.split(":", 1)[0]))
        await self._hold(run_id)
        why = await ask("screen_act", {"program": program["name"], "window": "", "action": "open", "element": "",
                                       "text": link, "keys": ""}, True)  # fmt: skip
        if why is not None:
            raise ScreenError(why)
        if link:
            await asyncio.to_thread(os.startfile, link)
            window = {"title": "", "program": program["name"]}
            await self._db.acall(lambda conn: record(conn, run_id, window, {"action": "open", "link": link}, "opened",
                                                     ""))  # fmt: skip
            return f"«{program['name']}» opens {link}; look at its window in a moment - see screen_windows"
        if program.get("exe"):
            await asyncio.to_thread(os.startfile, program["exe"])
        elif program.get("aumid"):
            await asyncio.to_thread(subprocess.Popen, ["explorer.exe", f"shell:AppsFolder\\{program['aumid']}"])
        else:
            raise ScreenError(f"«{program['name']}» has no file to start")
        window = {"title": "", "program": program["name"]}
        await self._db.acall(lambda conn: record(conn, run_id, window, {"action": "open"}, "started", ""))
        return f"started «{program['name']}»; its window shows in a moment - see screen_windows"
