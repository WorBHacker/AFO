"""The programs of this computer, and which of them the autonomous agent may control (D43, step 9.6a).

The hub finds the programs a person can start: the Start menu (Get-StartApps - desktop programs and Microsoft Store
apps alike) with the Store packages' folders, and the registry's installed programs for publisher and version. Help
files, links, documents and consoles (.msc) in the Start menu are left out: only what runs as a program. Any other
.exe is added by hand.

Each program has a decision: allowed, denied or none - and none is no (D41: the agent controls allowed programs
only). Only the desktop app decides. A desktop program is matched by its exact .exe (a folder such as System32 holds
many programs), a Store app by its package folder.

The hub looks at start, every 10 minutes and soon after a shortcut lands in a Start menu folder. A program found
after the first look is `fresh` until the user answers or puts it off, and goes out as `programs.found`: the window
asks about it. The first look asks nothing; it offers the list once. A program that is gone keeps its decision:
installed again, it is decided already.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import functools
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .db.database import utc_now

log = logging.getLogger("hub.programs")

START_DELAY_S = 10.0  # the first look waits for the hub to settle
SOON_S = 5.0  # after a new shortcut, installers write a few files: look once they are done
SCAN_TIMEOUT_S = 90
FIRST_SCAN = "programs.first_scan"  # settings: when the first look was made and what it found
OFFER = "programs.offer"  # settings: the list is still to be offered after the first look
DECISIONS = ("allow", "deny")

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)  # steam://, http://, ms-settings: - links, not programs
_EXE_IN_ID = re.compile(r"([\w-]+\.exe)", re.IGNORECASE)  # Microsoft.Office.WINWORD.EXE.15 -> WINWORD.EXE
_CN = re.compile(r"(?:^|,\s*)(?:O|CN)=([^,]+)")

POWERSHELL = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$start = @(Get-StartApps | Select-Object Name, AppID)
$targets = @{}
$apps = (New-Object -ComObject Shell.Application).NameSpace('shell:::{4234d49b-0245-4df3-b780-3893943456e1}')
foreach ($item in $apps.Items()) {
    $targets[$item.Path] = $item.ExtendedProperty('System.Link.TargetParsingPath')
}
foreach ($entry in $start) { $entry | Add-Member -NotePropertyName Target -NotePropertyValue $targets[$entry.AppID] }
$packages = @(Get-AppxPackage | Where-Object { -not $_.IsFramework } |
    Select-Object PackageFamilyName, InstallLocation, Version, Publisher)
@{ start = $start; packages = $packages } | ConvertTo-Json -Depth 3 -Compress
"""


@dataclass(frozen=True)
class Found:
    """A program one look found. `key` is what makes it the same program next time."""

    key: str
    name: str
    kind: str  # desktop / store / manual
    exe: str = ""
    folder: str = ""
    aumid: str = ""
    publisher: str = ""
    version: str = ""


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def program_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- what a look finds (pure, testable)


def from_start(
    entries: list[dict[str, Any]], packages: list[dict[str, Any]], resolve: Callable[[str], str]
) -> list[Found]:
    """Start menu entries as programs. `resolve` turns a desktop AppID into a path ('' when it cannot)."""
    by_family = {}
    for p in packages:
        family = str(p.get("PackageFamilyName") or "")
        if family:
            by_family[family.lower()] = p
    out: dict[str, Found] = {}
    for entry in entries:
        name, app_id = str(entry.get("Name") or "").strip(), str(entry.get("AppID") or "").strip()
        if not name or not app_id:
            continue
        if "!" in app_id and "\\" not in app_id:  # a Store app: PackageFamilyName!App
            package = by_family.get(app_id.split("!", 1)[0].lower(), {})
            match = _CN.search(str(package.get("Publisher") or ""))
            found = Found(
                f"aumid:{app_id.lower()}",
                name,
                "store",
                folder=str(package.get("InstallLocation") or ""),
                aumid=app_id,
                publisher=match.group(1).strip() if match else "",
                version=str(package.get("Version") or ""),
            )
        elif _SCHEME.match(app_id) and not re.match(r"^[a-z]:\\", app_id, re.IGNORECASE):
            continue
        else:
            path = resolve(app_id)
            if path:
                if not path.lower().endswith(".exe"):
                    continue  # help (.chm), a document, a console (.msc), a web page
                found = Found(f"exe:{norm(path)}", name, "desktop", exe=path, folder=os.path.dirname(path))
            elif "\\" in app_id or "/" in app_id:
                continue  # a path that is not there (left behind by an uninstall)
            else:  # a desktop program known by its id only (an AppUserModelID): its windows may carry the id,
                # or not (Telegram's do not) - then its shortcut's .exe tells them
                target = str(entry.get("Target") or "")
                exe = target if target.lower().endswith(".exe") and os.path.isfile(target) else ""
                found = Found(f"aumid:{app_id.lower()}", name, "desktop", aumid=app_id, exe=exe,
                              folder=os.path.dirname(exe) if exe else "")  # fmt: skip
        out.setdefault(found.key, found)
    return list(out.values())


def real_version(value: str) -> str:
    """A version, not an installer's unfilled template (%version%, $VERSION) or nothing but zeros."""
    value = value.strip()
    return "" if not value or "%" in value or "$" in value or not value.strip("0. ") else value


def installed_exe(value: str) -> str:
    """The .exe in DisplayIcon ("C:\\x\\app.exe",0) or UninstallString ("C:\\x\\uninst.exe" /S), or ''."""
    value = value.strip()
    if value.startswith('"'):
        path = value[1:].split('"', 1)[0]
    else:
        path = value.split(",")[0]
        end = path.lower().find(".exe")
        path = path[: end + 4] if end >= 0 else path
    path = path.strip()
    return path if path.lower().endswith(".exe") else ""


def _own_folder(path: str) -> str:
    """A folder that is one program's: not a drive, not Program Files itself, not Windows'."""
    folder = norm(path).rstrip(os.sep)
    windows = norm(os.environ.get("WINDIR", r"C:\Windows"))
    if not folder or len(folder.split(os.sep)) <= 2 or folder == windows or folder.startswith(windows + os.sep):
        return ""
    return folder


def enrich(found: list[Found], installed: list[dict[str, str]]) -> list[Found]:
    """Publisher and version from the registry's installed programs: by the program's .exe as the entry's icon, by
    the .exe inside the entry's folder (its install folder, or the folder of its icon or uninstaller; the deepest
    wins), or else by the same name."""
    icons: dict[str, dict[str, str]] = {}
    folders: list[tuple[str, dict[str, str]]] = []
    names: dict[str, dict[str, str]] = {}
    for entry in installed:
        icon = installed_exe(entry.get("icon", ""))
        if icon:
            icons.setdefault(norm(icon), entry)
        uninstaller = installed_exe(entry.get("uninstall", ""))
        for candidate in (
            entry.get("location", "").strip().strip('"'),
            os.path.dirname(icon) if icon else "",
            os.path.dirname(uninstaller) if uninstaller else "",
        ):
            folder = _own_folder(candidate) if candidate else ""
            if folder:
                folders.append((folder, entry))
        names.setdefault(entry.get("name", "").casefold(), entry)
    folders.sort(key=lambda item: len(item[0]), reverse=True)
    out = []
    for program in found:
        exe = norm(program.exe)
        match = icons.get(exe) if exe else None
        if match is None and exe:
            match = next((e for folder, e in folders if exe.startswith(folder + os.sep)), None)
        if match is None:
            match = names.get(program.name.casefold())
        if match is not None:
            icon = installed_exe(match.get("icon", ""))
            program = replace(
                program,
                publisher=program.publisher or match.get("publisher", ""),
                version=program.version or real_version(match.get("version", "")),
                # known by an id only, found by name: its .exe is the entry's icon (Google Chrome's chrome.exe)
                exe=program.exe or (icon if program.kind == "desktop" and os.path.isfile(icon) else ""),
            )
        out.append(program)
    return out


def with_file_info(found: list[Found], info: Callable[[str], dict[str, str]]) -> list[Found]:
    """What the registry did not say, from the program's own .exe: its company and version."""
    out = []
    for program in found:
        if program.exe and not (program.publisher and program.version):
            facts = info(program.exe)
            program = replace(
                program,
                publisher=program.publisher or facts.get("CompanyName", ""),
                version=program.version or real_version(facts.get("ProductVersion", "")),
            )
        out.append(program)
    return out


# ---------------------------------------------------------------- Windows


@functools.lru_cache(maxsize=64)
def _known_folder(guid: str) -> str:
    """A known folder ({6D809377-...} is Program Files) as a path; '' when Windows does not know it."""

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    try:
        value = uuid.UUID(guid)
    except ValueError:
        return ""
    folder_id = GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))
    path = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path)) != 0:
        return ""
    try:
        return path.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


def _app_path(exe_name: str) -> str:
    """The registry's App Paths: where a program named like WINWORD.EXE lives."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}") as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip().strip('"'))
        if os.path.isfile(path):
            return path
    return ""


def resolve_app_id(app_id: str) -> str:
    """A desktop AppID as a path: {KNOWNFOLDER}\\rest, a full path, or an id naming its .exe (App Paths)."""
    match = re.match(r"^\{([0-9A-Fa-f-]{36})\}\\(.+)$", app_id)
    if match:
        base = _known_folder(match.group(1))
        path = os.path.join(base, match.group(2)) if base else ""
        return path if path and os.path.exists(path) else ""
    if re.match(r"^[a-z]:\\", app_id, re.IGNORECASE):
        return app_id if os.path.exists(app_id) else ""
    named = _EXE_IN_ID.search(app_id)
    return _app_path(named.group(1)) if named else ""


def installed_programs() -> list[dict[str, str]]:
    """The registry's installed programs (both views of the machine's, and the user's own)."""
    import winreg

    places = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0),
    ]
    out = []
    for hive, path, view in places:
        try:
            root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                        values = {}
                        for name in ("DisplayName", "Publisher", "DisplayVersion", "InstallLocation", "DisplayIcon",
                                     "UninstallString", "ParentKeyName"):  # fmt: skip
                            with contextlib.suppress(OSError):
                                values[name] = str(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    continue
                if values.get("DisplayName") and not values.get("ParentKeyName"):  # an update belongs to another
                    out.append({
                        "name": values["DisplayName"],
                        "publisher": values.get("Publisher", ""),
                        "version": values.get("DisplayVersion", ""),
                        "location": values.get("InstallLocation", ""),
                        "icon": values.get("DisplayIcon", ""),
                        "uninstall": values.get("UninstallString", ""),
                    })  # fmt: skip
    return out


def start_and_store() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Get-StartApps and the Store packages, through one hidden PowerShell."""
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", POWERSHELL],
        capture_output=True,
        timeout=SCAN_TIMEOUT_S,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = done.stdout.decode("utf-8", "replace").lstrip("\ufeff").strip()
    if not text:
        raise RuntimeError(f"PowerShell said nothing (exit {done.returncode}): {done.stderr[-300:]!r}")
    data = json.loads(text)

    def listed(value: Any) -> list[dict[str, Any]]:  # ConvertTo-Json turns a list of one into the item itself
        return value if isinstance(value, list) else [value] if isinstance(value, dict) else []

    return listed(data.get("start")), listed(data.get("packages"))


def scan_windows() -> list[Found]:
    """One look at this computer's programs."""
    if sys.platform != "win32":
        return []
    entries, packages = start_and_store()
    found = enrich(from_start(entries, packages, resolve_app_id), installed_programs())
    return with_file_info(found, file_info)


FILE_FACTS = ("FileDescription", "CompanyName", "ProductVersion")


def file_info(path: str) -> dict[str, str]:
    """What a program says of itself in its version resource: FileDescription, CompanyName, ProductVersion."""
    if sys.platform != "win32":
        return {}
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return {}
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, data):
        return {}
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if not version.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(pointer), ctypes.byref(length)):
        return {}
    if length.value < 4:
        return {}
    language, codepage = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
    facts = {}
    for name in FILE_FACTS:
        block = rf"\StringFileInfo\{language:04x}{codepage:04x}\{name}"
        if version.VerQueryValueW(data, block, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
            facts[name] = ctypes.wstring_at(pointer.value, length.value).rstrip("\0").strip()
    return facts


# ---------------------------------------------------------------- the catalog (database)


def program_json(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "publisher": row["publisher"],
        "version": row["version"],
        "kind": row["kind"],
        "exe": row["exe"],
        "folder": row["folder"],
        "aumid": row["aumid"],
        "decision": row["decision"],
        "decided_at": row["decided_at"],
        "first_seen": row["first_seen"],
        "removed": row["removed_at"] is not None,
        "fresh": bool(row["fresh"]),
    }


def list_programs(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM programs ORDER BY name COLLATE NOCASE").fetchall()
    return [program_json(row) for row in rows]


def get_program(conn, pid: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM programs WHERE id = ?", (pid,)).fetchone()
    return program_json(row) if row else None


def _setting(conn, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _set_setting(conn, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), now),
    )


def apply_scan(conn, found: list[Found], now: str) -> dict[str, Any]:
    """What one look found, into the catalog: new programs (fresh after the first look), seen ones refreshed, the
    ones not found marked removed (hand-added ones are never removed by a look)."""
    first = _setting(conn, FIRST_SCAN) is None
    known = {row["key"]: row for row in conn.execute("SELECT key, kind, removed_at FROM programs")}
    new, seen = [], set()
    for f in found:
        if f.key in seen:
            continue
        seen.add(f.key)
        if f.key in known:
            conn.execute(
                "UPDATE programs SET name = ?, publisher = ?, version = ?, kind = ?, exe = ?, folder = ?, aumid = ?,"
                " last_seen = ?, removed_at = NULL WHERE key = ?",
                (f.name, f.publisher, f.version, f.kind, f.exe, f.folder, f.aumid, now, f.key),
            )
        else:
            pid = program_id(f.key)
            conn.execute(
                "INSERT INTO programs (id, key, name, publisher, version, kind, exe, folder, aumid, fresh, first_seen,"
                " last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, f.key, f.name, f.publisher, f.version, f.kind, f.exe, f.folder, f.aumid, 0 if first else 1,
                 now, now),
            )  # fmt: skip
            if not first:
                new.append(pid)
    removed = [
        key for key, row in known.items() if key not in seen and row["kind"] != "manual" and row["removed_at"] is None
    ]
    for key in removed:
        conn.execute("UPDATE programs SET removed_at = ?, fresh = 0 WHERE key = ?", (now, key))
    if first:
        _set_setting(conn, FIRST_SCAN, {"at": now, "count": len(seen)}, now)
        _set_setting(conn, OFFER, True, now)
    found_rows = [get_program(conn, pid) for pid in new]
    return {"first": first, "count": len(seen), "new": found_rows, "removed": len(removed)}


def decide(conn, pid: str, decision: str | None, by: str, now: str) -> dict[str, Any] | None:
    """allow / deny / None (not decided: the same as no). An answer is no longer a question."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS} or null")
    done = conn.execute(
        "UPDATE programs SET decision = ?, decided_by = ?, decided_at = ?, fresh = 0 WHERE id = ?",
        (decision, by if decision else None, now if decision else None, pid),
    )
    return get_program(conn, pid) if done.rowcount else None


def put_off(conn, pid: str) -> dict[str, Any] | None:
    """Decide later: no longer asked about, still not allowed."""
    done = conn.execute("UPDATE programs SET fresh = 0 WHERE id = ?", (pid,))
    return get_program(conn, pid) if done.rowcount else None


def add_manual(conn, path: str, name: str, now: str) -> tuple[dict[str, Any], bool]:
    """A program the scan does not see (portable, no shortcut): (it, whether it is new)."""
    key = f"exe:{norm(path)}"
    row = conn.execute("SELECT id FROM programs WHERE key = ?", (key,)).fetchone()
    if row:
        return get_program(conn, row["id"]), False
    pid = program_id(key)
    conn.execute(
        "INSERT INTO programs (id, key, name, kind, exe, folder, fresh, first_seen, last_seen)"
        " VALUES (?, ?, ?, 'manual', ?, ?, 0, ?, ?)",
        (pid, key, name, path, os.path.dirname(path), now, now),
    )
    return get_program(conn, pid), True


def remove_manual(conn, pid: str) -> bool:
    return conn.execute("DELETE FROM programs WHERE id = ? AND kind = 'manual'", (pid,)).rowcount > 0


def offer(conn) -> dict[str, Any] | None:
    """After the first look: {count} until the list was offered once."""
    if not _setting(conn, OFFER):
        return None
    first = _setting(conn, FIRST_SCAN) or {}
    return {"count": int(first.get("count", 0))}


def dismiss_offer(conn) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (OFFER,))


def allowed_program(conn, *, exe: str = "", aumid: str = "") -> dict[str, Any] | None:
    """The allowed program a window belongs to, by its process's .exe or its AppUserModelID; None - not allowed.
    A desktop program by its exact .exe; a Store app by its package folder."""
    rows = conn.execute(
        "SELECT * FROM programs WHERE decision = 'allow' AND (removed_at IS NULL OR kind = 'manual')"
    ).fetchall()
    target = norm(exe)
    windows = norm(os.environ.get("WINDIR") or "C:\\Windows")
    for row in rows:
        if aumid and row["aumid"] and row["aumid"].lower() == aumid.lower():
            return program_json(row)
        if not target:
            continue
        if row["kind"] == "store":
            folder = norm(row["folder"]).rstrip(os.sep)
            # a package of Windows itself hosts many parts (the input host beside «Архивация Windows»): its
            # folder tells nothing - such an app is known by its windows' id only
            if folder and target.startswith(folder + os.sep) and not folder.startswith(windows + os.sep):
                return program_json(row)
        elif row["exe"] and norm(row["exe"]) == target:
            return program_json(row)
    return None


# ---------------------------------------------------------------- the hub's part


class ProgramCatalog:
    """Looks for programs now and then, and tells the bus what it found."""

    def __init__(self, bus, db, interval_s: float, scan: Callable[[], list[Found]] | None = None):
        self._bus = bus
        self._db = db
        self._interval = interval_s
        self.scan_source = scan or scan_windows
        self._task: asyncio.Task | None = None
        self._soon: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer = None
        self._lock = asyncio.Lock()
        self.scanning = False
        self.scanned_at = ""
        self.error = ""

    def start(self) -> None:
        """Only for the real hub (interval > 0): a look soon after start, then every interval, and after new
        Start menu shortcuts."""
        if self._interval <= 0:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())
        self._watch_start_menu()

    async def stop(self) -> None:
        if self._soon is not None:
            self._soon.cancel()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._observer is not None:
            observer, self._observer = self._observer, None
            observer.stop()
            await asyncio.to_thread(observer.join, 5)

    async def _run(self) -> None:
        await asyncio.sleep(START_DELAY_S)
        while True:
            await self.scan()
            await asyncio.sleep(self._interval)

    def soon(self) -> None:
        """A look in a few seconds (called from any thread)."""
        if self._loop is None:
            return

        def arm() -> None:
            if self._soon is not None:
                self._soon.cancel()
            self._soon = self._loop.call_later(SOON_S, lambda: asyncio.ensure_future(self.scan()))

        self._loop.call_soon_threadsafe(arm)

    def _watch_start_menu(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        catalog = self

        class Shortcuts(FileSystemEventHandler):
            def on_any_event(self, event) -> None:
                path = os.fsdecode(event.dest_path or event.src_path)
                if path.lower().endswith(".lnk") and event.event_type in ("created", "moved", "deleted"):
                    catalog.soon()

        folders = [
            Path(os.environ.get(name, "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for name in ("ProgramData", "APPDATA")
            if os.environ.get(name)
        ]
        observer = Observer()
        watched = 0
        for folder in folders:
            if folder.is_dir():
                observer.schedule(Shortcuts(), str(folder), recursive=True)
                watched += 1
        if watched:
            observer.daemon = True
            observer.start()
            self._observer = observer

    async def scan(self) -> dict[str, Any] | None:
        """One look; None when it failed (the catalog stays as it was)."""
        if self._lock.locked():
            return None
        async with self._lock:
            self.scanning = True
            await self._bus.publish("programs.scanning", {}, persist=False)
            try:
                found = await asyncio.to_thread(self.scan_source)
            except Exception as exc:
                log.warning("programs: the look failed: %s", exc)
                self.error = str(exc) or exc.__class__.__name__
                found = None
            finally:
                self.scanning = False
            if not found:  # nothing at all is a failed look, not a computer without programs
                await self._bus.publish("programs.scanned", {"failed": True, "error": self.error}, persist=False)
                return None
            self.error = ""
            now = utc_now()
            result = await self._db.acall(lambda conn: apply_scan(conn, found, now))
            self.scanned_at = now
        if result["new"]:
            await self._bus.publish("programs.found", {"programs": result["new"]})
        await self._bus.publish(
            "programs.scanned",
            {"first": result["first"], "count": result["count"], "new": len(result["new"]),
             "removed": result["removed"]},
            persist=False,
        )  # fmt: skip
        return result
