"""Every name of a file (D46, step 13.12). NTFS lets one file have several - hard links - and a check by name sees only
the name it is given: `notes.txt` made a hard link of `.env` is `.env` all the same, and `resolve()` does not tell (it
follows symbolic links and junctions, not hard links). Windows lists a file's names (FindFirstFileNameW); elsewhere a
file with several names is one whose names cannot be listed.

Most files have one name: then the check costs one stat, often one the caller has made anyway (`nlink`).
"""

from __future__ import annotations

import functools
import os
import stat
from pathlib import Path

NAME_CHARS = 32_768  # a Windows path's longest, in UTF-16 units


def other_names(path: Path, nlink: int | None = None) -> list[Path] | None:
    """The names of the file at `path` other than `path` itself: [] for a file with one name, or for what is not a
    file; None when it has several and they cannot be listed. `nlink` - its link count, when the caller has stat'ed
    it already (os.stat: os.scandir's entries say 0 on Windows)."""
    if nlink is None:
        try:
            found = os.stat(path)
        except OSError:
            return []
        if not stat.S_ISREG(found.st_mode):
            return []
        nlink = found.st_nlink
    if nlink <= 1:
        return []
    real = os.path.realpath(path)
    names = _names(real) if os.name == "nt" else None
    if names is None:
        return None
    me = os.path.normcase(real)
    return [Path(n) for n in names if os.path.normcase(n) != me]


@functools.cache
def _kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.FindFirstFileNameW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                          wintypes.LPWSTR]  # fmt: skip
    kernel.FindFirstFileNameW.restype = wintypes.HANDLE
    kernel.FindNextFileNameW.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    kernel.FindNextFileNameW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    kernel.FindClose.restype = wintypes.BOOL
    return kernel


def _names(path: str) -> list[str] | None:
    """All the names of a file, as full paths on its volume; None when Windows would not list them."""
    import ctypes
    from ctypes import wintypes

    kernel = _kernel()
    volume = ctypes.create_unicode_buffer(NAME_CHARS)
    if not kernel.GetVolumePathNameW(path, volume, NAME_CHARS):
        return None
    root = volume.value.rstrip("\\")  # D:\ or a folder the volume is mounted in; the names come from its root
    name = ctypes.create_unicode_buffer(NAME_CHARS)
    size = wintypes.DWORD(NAME_CHARS)
    handle = kernel.FindFirstFileNameW(path, 0, ctypes.byref(size), name)
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    names = [root + name.value]
    try:
        while True:
            size = wintypes.DWORD(NAME_CHARS)
            if not kernel.FindNextFileNameW(handle, ctypes.byref(size), name):
                break
            names.append(root + name.value)
    finally:
        kernel.FindClose(handle)
    return names
