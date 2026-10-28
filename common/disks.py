"""This computer's disks as a model installer sees them (stage 10.1, D37): each drive letter, its free and total space,
and what it is - NVMe, SSD, HDD or USB - since a model loads from an HDD many times slower (ARCHITECTURE.md, Q11).

Windows only, through kernel32: GetLogicalDrives and GetDiskFreeSpaceExW, and IOCTL_STORAGE_QUERY_PROPERTY on the
volume (`\\\\.\\C:`, opened without read or write rights - no administrator needed) for the bus type and whether the
disk has a seek penalty (a spinning one). A drive that cannot be asked is "unknown", never an error.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from ctypes import wintypes
from typing import Any

GIB = 1024**3
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
DEVICE_PROPERTY, SEEK_PENALTY_PROPERTY = 0, 7
BUS_USB, BUS_NVME = 7, 17
DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
# the order a model's files prefer: the fastest first
SPEED = {"nvme": 0, "ssd": 1, "unknown": 2, "hdd": 3, "usb": 4}
WORDS = {"nvme": "NVMe", "ssd": "SSD", "hdd": "жёсткий диск (медленно)", "usb": "USB-диск (медленно)",
         "unknown": "диск"}  # fmt: skip


class _Query(ctypes.Structure):
    _fields_ = [("PropertyId", ctypes.c_int), ("QueryType", ctypes.c_int), ("Additional", ctypes.c_ubyte * 1)]


class _SeekPenalty(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("IncursSeekPenalty", wintypes.BOOLEAN)]


def _ask(handle: int, prop: int, out: ctypes.Array | ctypes.Structure) -> bool:
    query = _Query(prop, 0)
    returned = wintypes.DWORD()
    return bool(ctypes.windll.kernel32.DeviceIoControl(
        wintypes.HANDLE(handle), IOCTL_STORAGE_QUERY_PROPERTY, ctypes.byref(query), ctypes.sizeof(query),
        ctypes.byref(out), ctypes.sizeof(out), ctypes.byref(returned), None))  # fmt: skip


def _media(letter: str) -> tuple[str, str]:
    """(kind, the disk's product name) of the disk under a drive letter."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(f"\\\\.\\{letter}:", 0, 3, None, 3, 0, None)  # share read|write, open existing
    if not handle or handle == wintypes.HANDLE(-1).value:
        return "unknown", ""
    try:
        descriptor = (ctypes.c_ubyte * 1024)()
        bus, product = None, ""
        if _ask(handle, DEVICE_PROPERTY, descriptor):
            raw = bytes(descriptor)
            bus = int.from_bytes(raw[28:32], "little")  # STORAGE_DEVICE_DESCRIPTOR.BusType
            offset = int.from_bytes(raw[16:20], "little")  # ProductIdOffset
            if 0 < offset < len(raw):
                product = raw[offset:].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        if bus == BUS_NVME:
            return "nvme", product
        if bus == BUS_USB:
            return "usb", product
        penalty = _SeekPenalty()
        if _ask(handle, SEEK_PENALTY_PROPERTY, penalty):
            return ("hdd" if penalty.IncursSeekPenalty else "ssd"), product
        return "unknown", product
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _label(root: str) -> str:
    name = ctypes.create_unicode_buffer(261)
    if ctypes.windll.kernel32.GetVolumeInformationW(root, name, 261, None, None, None, None, 0):
        return name.value
    return ""


def drives() -> list[dict[str, Any]]:
    """The fixed and removable drives with a file system: letter, label, free and total GiB, kind, product, whether
    Windows lives there - the fastest first, then the one with more room."""
    if sys.platform != "win32":
        return []
    kernel32 = ctypes.windll.kernel32
    system = (os.environ.get("SYSTEMDRIVE") or "C:")[0].upper()
    out = []
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(65 + i)
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) not in (DRIVE_FIXED, DRIVE_REMOVABLE):
            continue
        free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if not kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(total), None):
            continue  # an empty card reader
        kind, product = "unknown", ""
        with contextlib.suppress(OSError):
            kind, product = _media(letter)
        out.append({"letter": letter, "root": root, "label": _label(root), "free_gib": round(free.value / GIB, 1),
                    "total_gib": round(total.value / GIB, 1), "kind": kind, "words": WORDS[kind],
                    "product": product, "system": letter == system})  # fmt: skip
    return sorted(out, key=lambda d: (SPEED[d["kind"]], -d["free_gib"]))
