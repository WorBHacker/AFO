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
import ctypes
import json
import logging
import os
import re
from ctypes import wintypes
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

    async def move(self, exe: str) -> bool:
        """`exe` to the integrated graphics; False - a choice is there already (the person's, Windows', AFO's)."""
        async with self._lock:
            before = await asyncio.to_thread(self.store.read, exe)
            if preference(before) is not None:
                return False
            wrote = with_preference(before, POWER_SAVING)
            await asyncio.to_thread(self.store.write, exe, wrote)

            def keep(conn) -> None:
                moved = load_moved(conn)
                moved[exe] = {"before": before, "wrote": wrote}
                _save_moved(conn, moved)

            await self._db.acall(keep)
        log.info("to the integrated graphics: %s", exe)
        return True

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
    _fields_ = [("CStatus", wintypes.DWORD), ("largeValue", ctypes.c_longlong)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2
_INSTANCE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]{8}_0x[0-9a-fA-F]{8})")


def process_memory(counter: str = r"\GPU Process Memory(*)\Dedicated Usage") -> dict[tuple[int, str], float]:
    """(pid, luid) -> MiB of the adapter's own memory the program holds, as Windows counts it; {} - no counters."""
    pdh = ctypes.windll.pdh
    query, handle = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)):
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(query, counter, None, ctypes.byref(handle)) or pdh.PdhCollectQueryData(query):
            return {}
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count),
                                                  None) & 0xFFFFFFFF  # fmt: skip
        if status != PDH_MORE_DATA or not size.value:
            return {}
        buffer = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer):
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_Item))
        out: dict[tuple[int, str], float] = {}
        for i in range(count.value):
            found = _INSTANCE.search(items[i].szName or "")
            if found and items[i].FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA, NEW_DATA
                key = (int(found.group(1)), found.group(2).lower())
                out[key] = out.get(key, 0.0) + items[i].FmtValue.largeValue / 2**20
        return out
    finally:
        pdh.PdhCloseQuery(query)
