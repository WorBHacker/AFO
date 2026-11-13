"""The graphics card for models (stage 43, the user's word 2026-11-13): programs that draw on the graphics card go to
the integrated graphics through Windows' own «Графика» setting, so the card's video memory stays the models'.

- The setting: HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences - a value per program's path, its data Windows'
  «Key=Value;» fields: `GpuPreference=1;` («Энергосбережение»: the integrated graphics), `=2;` («Высокая
  производительность»), beside fields of Windows' own (`AppStatus`, `SwapEffectUpgradeEnable`) that stay as they are.
  It holds from the program's next start. No administrator rights.
- What was there before AFO is kept in the hub's database (`gpu.moved`: path -> {before, wrote}); «Вернуть как было»
  gives back exactly that, and only AFO's own values - one the person changed since stays theirs. A program the person
  (or Windows) gave a choice is not AFO's to change.
- The adapters (DXGI, through ctypes): which one Windows gives a program on each choice - moving is worth it only when
  they are two. Each program's video memory on each adapter: Windows' «GPU Process Memory» counters (PDH), as the Task
  Manager shows it; their sum is more than the card holds (evicted memory, shared surfaces) - a difference is right.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import os
import re
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .db import utc_now

log = logging.getLogger("hub.gpu_prefs")

KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
KEY_ENV = "LOCALCHAT_GPU_PREFS_KEY"  # the tests' own key: the person's setting is never touched by them
MOVED_SETTING = "gpu.moved"
POWER_SAVING, HIGH_PERFORMANCE = 1, 2
MICROSOFT = 0x1414  # the Basic Render Driver (software)


# --- the value ---


def _fields(data: str | None) -> list[tuple[str, str]]:
    out = []
    for part in (data or "").split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            out.append((name.strip(), value.strip()))
    return out


def preference(data: str | None) -> int | None:
    """The GpuPreference a value holds; None - none (Windows decides)."""
    found = next((v for k, v in _fields(data) if k == "GpuPreference"), None)
    return int(found) if found is not None and found.isdigit() else None


def with_preference(data: str | None, choice: int | None) -> str | None:
    """The value with GpuPreference set (None - taken out), Windows' fields as they were; None - nothing left."""
    fields = _fields(data)
    if choice is None:
        fields = [(k, v) for k, v in fields if k != "GpuPreference"]
    elif any(k == "GpuPreference" for k, _ in fields):
        fields = [(k, str(choice) if k == "GpuPreference" else v) for k, v in fields]
    else:
        fields.append(("GpuPreference", str(choice)))
    return "".join(f"{k}={v};" for k, v in fields) or None


class Registry:
    """The setting's key (HKCU; LOCALCHAT_GPU_PREFS_KEY in tests)."""

    def __init__(self) -> None:
        self.path = os.environ.get(KEY_ENV) or KEY

    def read(self, exe: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path) as key:
                return str(winreg.QueryValueEx(key, exe)[0])
        except OSError:
            return None

    def write(self, exe: str, data: str | None) -> None:
        """`data` None - the value removed (Windows decides again)."""
        import winreg

        if data is None:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, exe)
            except FileNotFoundError:
                pass
            return
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.path) as key:
            winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, data)

    def all(self) -> dict[str, str]:
        import winreg

        out: dict[str, str] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path) as key:
                i = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    out[name] = str(value)
                    i += 1
        except OSError:
            pass
        return out


def load_moved(conn) -> dict[str, dict[str, Any]]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (MOVED_SETTING,)).fetchone()
    return json.loads(row[0]) if row else {}


def _save_moved(conn, moved: dict[str, dict[str, Any]]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (MOVED_SETTING, json.dumps(moved, ensure_ascii=False), utc_now()),
    )


class Prefs:
    """Programs moved to the integrated graphics and given back; one writer at a time."""

    def __init__(self, db, store: Registry | None = None):
        self._db = db
        self.store = store or Registry()
        self._lock = asyncio.Lock()

    async def move(self, exe: str, **meta: Any) -> bool:
        """`exe` to the integrated graphics; False - a choice is there already (the person's, Windows', AFO's).
        `meta` is kept beside (its name, the video memory it held)."""
        async with self._lock:
            before = await asyncio.to_thread(self.store.read, exe)
            if preference(before) is not None:
                return False
            wrote = with_preference(before, POWER_SAVING)
            await asyncio.to_thread(self.store.write, exe, wrote)

            def keep(conn) -> None:
                moved = load_moved(conn)
                moved[exe] = {"before": before, "wrote": wrote, **meta}
                _save_moved(conn, moved)

            await self._db.acall(keep)
        log.info("to the integrated graphics: %s", exe)
        return True

    async def give_back_one(self, exe: str) -> bool:
        """One program back as it was before AFO - when the value is still AFO's; its record goes either way."""
        async with self._lock:
            moved = await self._db.acall(load_moved)
            record = next((r for e, r in moved.items() if e.lower() == exe.lower()), None)
            if record is None:
                return False
            name = next(e for e in moved if e.lower() == exe.lower())
            now = await asyncio.to_thread(self.store.read, name)
            if now == record["wrote"]:
                await asyncio.to_thread(self.store.write, name, record["before"])
            del moved[name]
            await self._db.acall(lambda conn: _save_moved(conn, moved))
            return now == record["wrote"]

    async def give_back(self) -> dict[str, list[str]]:
        """«Вернуть как было»: each value AFO wrote, as it was before - when it is still AFO's; one the person changed
        since stays. {given_back, left}."""
        async with self._lock:
            moved = await self._db.acall(load_moved)
            back, left = [], []
            for exe, record in moved.items():
                now = await asyncio.to_thread(self.store.read, exe)
                if now == record["wrote"]:
                    await asyncio.to_thread(self.store.write, exe, record["before"])
                    back.append(exe)
                else:
                    left.append(exe)
            await self._db.acall(lambda conn: _save_moved(conn, {}))
        log.info("given back to Windows' choice: %d; left as the person set them: %d", len(back), len(left))
        return {"given_back": back, "left": left}


# --- the adapters (DXGI) ---


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _Desc1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", wintypes.UINT)]  # fmt: skip


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]  # fmt: skip


def _guid(text: str) -> _GUID:
    import uuid

    raw = uuid.UUID(text).bytes_le
    return _GUID.from_buffer_copy(raw)


IID_FACTORY6 = "c1b6694f-ff09-44a9-b03c-77900a0a1d17"
IID_ADAPTER1 = "29038f61-3839-4626-91fd-086879011a05"
_RELEASE, _ENUM_ADAPTERS1, _BY_PREFERENCE, _GET_DESC1 = 2, 12, 29, 10
DXGI_ERROR_NOT_FOUND = 0x887A0002


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    """A COM method by its place in the vtable; its HRESULT as a number (DXGI_ERROR_NOT_FOUND ends a listing)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[_RELEASE])(obj)


def luid_text(luid: _LUID) -> str:
    """As Windows' counters name it: 0x<high>_0x<low>, eight hex digits each."""
    return f"0x{luid.HighPart & 0xFFFFFFFF:08x}_0x{luid.LowPart:08x}"


def _describe(adapter: ctypes.c_void_p) -> dict[str, Any]:
    desc = _Desc1()
    _method(adapter, _GET_DESC1, ctypes.POINTER(_Desc1))(adapter, ctypes.byref(desc))
    return {"name": desc.Description, "vendor": desc.VendorId, "luid": luid_text(desc.AdapterLuid),
            "dedicated_mib": desc.DedicatedVideoMemory // 2**20, "software": bool(desc.Flags & 2)}  # fmt: skip


def _factory() -> ctypes.c_void_p:
    factory = ctypes.c_void_p()
    iid = _guid(IID_FACTORY6)
    ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory))
    if not factory:
        raise OSError("DXGI 1.6 is not there (Windows 10 1803 and later have it)")
    return factory


def adapters() -> list[dict[str, Any]]:
    """Each graphics adapter: name, vendor (PCI id), luid (as the counters name it), dedicated_mib, software."""
    factory = _factory()
    out = []
    try:
        enum = _method(factory, _ENUM_ADAPTERS1, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        i = 0
        while True:
            adapter = ctypes.c_void_p()
            if enum(factory, i, ctypes.byref(adapter)) or not adapter:
                break
            try:
                out.append(_describe(adapter))
            finally:
                _release(adapter)
            i += 1
    finally:
        _release(factory)
    return out


def _by_preference(factory: ctypes.c_void_p, which: int) -> dict[str, Any]:
    adapter = ctypes.c_void_p()
    iid = _guid(IID_ADAPTER1)
    call = _method(factory, _BY_PREFERENCE, wintypes.UINT, wintypes.UINT, ctypes.POINTER(_GUID),
                   ctypes.POINTER(ctypes.c_void_p))  # fmt: skip
    call(factory, 0, which, ctypes.byref(iid), ctypes.byref(adapter))
    try:
        return _describe(adapter)
    finally:
        _release(adapter)


def choice() -> dict[str, Any]:
    """The adapter Windows gives a program on «Энергосбережение» and on «Высокая производительность»; `two` - they
    differ (an integrated graphics beside a card: moving programs is worth it)."""
    factory = _factory()
    try:
        low = _by_preference(factory, 1)  # DXGI_GPU_PREFERENCE_MINIMUM_POWER
        high = _by_preference(factory, 2)  # DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE
    finally:
        _release(factory)
    return {"power_saving": low, "high_performance": high, "two": low["luid"] != high["luid"]}


# --- each program's video memory (PDH) ---


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]  # the union, read as a double


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
_INSTANCE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]{8}_0x[0-9a-fA-F]{8})")


def _counter(path: str, wait_s: float = 0.0) -> list[tuple[str, float]]:
    """Each instance of a wildcard counter, as (its name, its value); a rate (a percentage) is measured over `wait_s`
    between two looks. [] - Windows has no such counter."""
    import time

    pdh = ctypes.windll.pdh
    query, handle = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)):
        return []
    try:
        if pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(handle)) or pdh.PdhCollectQueryData(query):
            return []
        if wait_s:
            time.sleep(wait_s)
            pdh.PdhCollectQueryData(query)
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count),
                                                  None) & 0xFFFFFFFF  # fmt: skip
        if status != PDH_MORE_DATA or not size.value:
            return []
        buffer = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buffer):
            return []
        items = ctypes.cast(buffer, ctypes.POINTER(_Item))
        return [(items[i].szName or "", items[i].FmtValue.value) for i in range(count.value)
                if items[i].FmtValue.CStatus in (0, 1)]  # PDH_CSTATUS_VALID_DATA, NEW_DATA  # fmt: skip
    finally:
        pdh.PdhCloseQuery(query)


def process_memory(counter: str = r"\GPU Process Memory(*)\Dedicated Usage") -> dict[tuple[int, str], float]:
    """(pid, luid) -> MiB of the adapter's own memory the program holds, as Windows counts it; {} - no counters."""
    out: dict[tuple[int, str], float] = {}
    for name, value in _counter(counter):
        found = _INSTANCE.search(name)
        if found:
            key = (int(found.group(1)), found.group(2).lower())
            out[key] = out.get(key, 0.0) + value / 2**20
    return out


def busy_3d(luids: set[str], wait_s: float = 1.0) -> dict[int, float]:
    """pid -> how busy its drawing (the 3D engines) keeps these adapters, % over `wait_s`."""
    out: dict[int, float] = {}
    for name, value in _counter(r"\GPU Engine(*)\Utilization Percentage", wait_s):
        found = _INSTANCE.search(name)
        if found and found.group(2).lower() in luids and name.lower().endswith("engtype_3d"):
            pid = int(found.group(1))
            out[pid] = out.get(pid, 0.0) + value
    return out


# --- which programs go (43.6) ---

BUSY_3D = 15.0  # % of the card's drawing over a second: a game, or another program drawing 3D, now
CHECK_S = 30.0
ON_SETTING, CHOICES_SETTING, GAMES_SETTING = "gpu.on", "gpu.choices", "gpu.games"
# programs that need the graphics card: NVENC, CUDA, 3D work; AFO's own engines
NEEDS_CARD = frozenset({"obs64.exe", "obs32.exe", "blender.exe", "resolve.exe", "afterfx.exe",
                        "adobe premiere pro.exe", "unity.exe", "unrealeditor.exe", "ue4editor.exe", "houdini.exe",
                        "houdinifx.exe", "maya.exe", "3dsmax.exe", "llama-server.exe", "llama-swap.exe",
                        "llama-bench.exe", "sd-server.exe", "sd.exe", "strata.exe", "ollama.exe",
                        "ollama app.exe", "lm studio.exe"})  # fmt: skip
NOT_GAMES = frozenset({"wallpaper32.exe", "wallpaper64.exe", "steamwebhelper.exe"})  # Steam's own software
# a folder on its path says «game» (the stores', «Games», a game's own build - «GameDevelop»), or Unreal's build
GAME_PATH = re.compile(r"(?i)\\[^\\]*game[^\\]*\\|\\binaries\\win64\\|-shipping\.exe$")
ENGINE_FILES = ("UnityPlayer.dll", "GameAssembly.dll")  # a Unity game beside its program


def why_kept(program: dict[str, Any], facts: dict[str, Any]) -> str | None:
    """Why a program on the card stays there: "person" (they chose the card), "windows" (Windows' own parts, packaged
    apps - the setting goes by their package), "tool" (it needs the card), "game", "chosen" (the person or Windows
    gave it a choice already); None - it goes to the integrated graphics. The person's choice comes first."""
    exe = program["exe"]
    low, name = exe.lower(), (program.get("name") or exe.rsplit("\\", 1)[-1]).lower()
    choice = facts["choices"].get(low)
    if choice == "card":
        return "person"
    if choice == "integrated":
        return None
    if low.startswith(facts["windows"]) or "\\windowsapps\\" in low:
        return "windows"
    if (name in NEEDS_CARD or name.startswith(("python", "godot")) or "\\nvidia corporation\\" in low
            or any(low.startswith(own) for own in facts["own"])):  # fmt: skip
        return "tool"
    if name in NOT_GAMES:
        return None
    value = facts["values"].get(low)
    if (low in facts["games"] or any(low.startswith(f) for f in facts["game_folders"]) or GAME_PATH.search(low)
            or program.get("busy_3d", 0.0) >= BUSY_3D or "appstatus" in (value or "").lower()
            or any(os.path.isfile(os.path.join(os.path.dirname(exe), f)) for f in ENGINE_FILES)):  # fmt: skip
        return "game"
    if preference(value) is not None and low not in facts["moved"]:
        return "chosen"
    return None


def windows_games() -> set[str]:
    """The games Windows itself knows (Game Bar's list, GameConfigStore), their files lowercased."""
    import winreg

    out: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore\Children") as children:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(children, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(children, child) as key:
                        out.add(str(winreg.QueryValueEx(key, "MatchedExeFullPath")[0]).lower())
                except OSError:
                    continue
    except OSError:
        pass
    return out


def store_folders() -> list[str]:
    """The game stores' folders (lowercased, ending in \\): Steam's libraries, Epic's games, GOG's."""
    from . import programs

    folders: list[str] = []
    root = programs.steam_root()
    if root:
        libraries = [root]
        listed = programs.vdf(programs._read(Path(root) / "steamapps" / "libraryfolders.vdf")).get("libraryfolders")
        for entry in listed.values() if isinstance(listed, dict) else []:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                libraries.append(entry["path"])
        folders += [os.path.join(os.path.normpath(lib), "steamapps", "common") for lib in libraries]
    manifests = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Epic" / "EpicGamesLauncher" / "Data"
    for item in (manifests / "Manifests").glob("*.item") if (manifests / "Manifests").is_dir() else []:
        try:
            place = json.loads(item.read_text(encoding="utf-8")).get("InstallLocation")
        except OSError, ValueError:
            continue
        if isinstance(place, str) and place:
            folders.append(os.path.normpath(place))
    folders += _gog_folders()
    return list(dict.fromkeys(os.path.normpath(f).lower().rstrip("\\") + "\\" for f in folders if f))


def _gog_folders() -> list[str]:
    import winreg

    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games") as games:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(games, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(games, child) as key:
                        out.append(os.path.normpath(str(winreg.QueryValueEx(key, "path")[0])))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def machine_facts() -> dict[str, Any]:
    """What does not change between looks: the games Windows and the stores know, Windows' folder, AFO's own."""
    import sys

    from common.paths import engines_home

    own = {os.path.dirname(sys.executable), str(engines_home())}
    return {"games": windows_games(), "game_folders": store_folders(),
            "windows": (os.environ.get("SYSTEMROOT") or r"C:\Windows").lower().rstrip("\\") + "\\",
            "own": [o.lower().rstrip("\\") + "\\" for o in own if o]}  # fmt: skip


def look_now(luids: set[str]) -> list[dict[str, Any]]:
    """The programs holding these adapters' memory now (Windows' counters): exe, name, pids, MiB, how busy their 3D
    keeps the card. A program Windows does not let us look at (its own services) is left out."""
    import psutil

    memory = process_memory()
    drawing = busy_3d(luids)
    out: dict[str, dict[str, Any]] = {}
    for (pid, luid), mib in memory.items():
        if luid not in luids or mib <= 0:
            continue
        try:
            process = psutil.Process(pid)
            exe, name = process.exe(), process.name()
        except psutil.Error, OSError:
            continue
        if not exe:
            continue
        entry = out.setdefault(exe.lower(), {"exe": exe, "name": name, "pids": [], "mib": 0.0, "busy_3d": 0.0})
        entry["pids"].append(pid)
        entry["mib"] += mib
        entry["busy_3d"] = max(entry["busy_3d"], drawing.get(pid, 0.0))
    return list(out.values())


def _get(conn, key: str, default: Any) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(conn, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), utc_now()),
    )


class Watcher:
    """«Видеокарта — моделям» (43.6, the user's word): while it is on and there are two adapters, every CHECK_S the
    programs on the card that go are moved to the integrated graphics - it holds from their next start; they stay
    there after «Выход» too, and only «Вернуть как было» gives them back. The state says what each program is."""

    def __init__(self, db, bus, prefs: Prefs, *, interval_s: float = CHECK_S, look=None, facts=None, choice=None,
                 adapters=None):  # fmt: skip
        self._db = db
        self._bus = bus
        self.prefs = prefs
        self._interval = interval_s
        self._look = look or look_now
        self._facts = facts or machine_facts
        self._choice = choice or globals()["choice"]
        self._adapters = adapters or globals()["adapters"]
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.state: dict[str, Any] = {"available": False, "on": True, "integrated": "", "card": "", "programs": [],
                                      "freed_mib": 0.0}  # fmt: skip

    def start(self) -> None:
        if self._interval > 0 and os.name == "nt":
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("the look at the graphics card failed")
            await asyncio.sleep(self._interval)

    async def check(self) -> dict[str, Any]:
        """One look: the programs on the card, each moved or kept (and why); the state published when it changed."""
        async with self._lock:
            try:
                found = await asyncio.to_thread(self._choice)
            except OSError as exc:
                log.info("no adapters' choice: %s", exc)
                found = {"two": False, "power_saving": {}, "high_performance": {}}
            on = await self._db.acall(lambda conn: _get(conn, ON_SETTING, True))
            high = found.get("high_performance") or {}
            luids = {a["luid"] for a in await asyncio.to_thread(self._adapters)
                     if a.get("vendor") == high.get("vendor") and a["luid"] != (found.get("power_saving") or {}).get(
                         "luid")} | ({high["luid"]} if high.get("luid") else set())  # fmt: skip
            seen = await asyncio.to_thread(self._look, luids) if found.get("two") else []
            facts = await asyncio.to_thread(self._facts)
            choices = await self._db.acall(lambda conn: _get(conn, CHOICES_SETTING, {}))
            games = set(await self._db.acall(lambda conn: _get(conn, GAMES_SETTING, [])))
            moved = await self._db.acall(load_moved)
            values = {k.lower(): v for k, v in (await asyncio.to_thread(self.prefs.store.all)).items()}
            facts = {**facts, "choices": choices, "values": values, "moved": {m.lower() for m in moved},
                     "games": facts["games"] | games}  # fmt: skip
            moved_now = []
            rows: dict[str, dict[str, Any]] = {}
            for program in seen:
                low = program["exe"].lower()
                why = why_kept(program, facts)
                if why == "game" and program.get("busy_3d", 0.0) >= BUSY_3D and low not in games:
                    games.add(low)  # remembered: a game in its menu is still a game
                    remembered = sorted(games)
                    await self._db.acall(lambda conn, g=remembered: _put(conn, GAMES_SETTING, g))
                goes = why is None and low not in facts["moved"] and on and found.get("two")
                if goes and await self.prefs.move(program["exe"], name=program["name"],
                                                  held_mib=round(program["mib"], 1)):  # fmt: skip
                    moved_now.append(program)
                    facts["moved"].add(low)
                where = "waits" if low in facts["moved"] else "kept" if why else "card"
                rows[low] = {"exe": program["exe"], "name": program["name"], "mib": round(program["mib"], 1),
                             "where": where, "why": why or ""}  # fmt: skip
            moved = await self._db.acall(load_moved)
            freed = 0.0
            for exe, record in moved.items():
                if exe.lower() not in rows:
                    rows[exe.lower()] = {"exe": exe, "name": record.get("name") or exe.rsplit("\\", 1)[-1],
                                         "mib": record.get("held_mib") or 0.0, "where": "moved", "why": ""}  # fmt: skip
                    freed += record.get("held_mib") or 0.0
            state = {"available": bool(found.get("two")), "on": bool(on),
                     "integrated": (found.get("power_saving") or {}).get("name", ""), "card": high.get("name", ""),
                     "programs": sorted(rows.values(), key=lambda r: (r["where"] != "waits", -r["mib"])),
                     "freed_mib": round(freed, 1)}  # fmt: skip
        if moved_now:  # each waits for its restart: the window says so
            said = [{"exe": p["exe"], "name": p["name"], "mib": round(p["mib"], 1)} for p in moved_now]
            await self._bus.publish("gpu.moved", {"programs": said})
        if state != self.state:
            self.state = state
            await self._bus.publish("gpu.prefs", {"state": state}, persist=False)
        return state

    async def set_on(self, on: bool) -> dict[str, Any]:
        """The switch: off moves nothing more and gives nothing back («Вернуть как было» does)."""
        await self._db.acall(lambda conn: _put(conn, ON_SETTING, bool(on)))
        return await self.check()

    async def choose(self, exe: str, where: str) -> dict[str, Any]:
        """The person's choice for a program: "card" (a moved one comes back at once), "integrated" (moved now, even
        a game), "" (AFO decides again)."""
        low = exe.lower()

        def keep(conn) -> None:
            choices = _get(conn, CHOICES_SETTING, {})
            if where:
                choices[low] = where
            else:
                choices.pop(low, None)
            _put(conn, CHOICES_SETTING, choices)

        await self._db.acall(keep)
        if where == "card":
            await self.prefs.give_back_one(exe)
        elif where == "integrated":
            await self.prefs.move(exe, name=exe.rsplit("\\", 1)[-1])
        return await self.check()

    async def give_back(self) -> dict[str, Any]:
        """«Вернуть как было»: every value AFO wrote back as it was, and the switch off (or they would go again)."""
        await self._db.acall(lambda conn: _put(conn, ON_SETTING, False))
        back = await self.prefs.give_back()
        return {**back, "state": await self.check()}
