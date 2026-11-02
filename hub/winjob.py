"""A Windows job for a process the hub starts for an agent (D46): when the hub stops it, every process it started goes
too, and all of them together get no more memory than given. Elsewhere than Windows it does nothing and the process
runs as it would.

The process joins the job right after it starts - before its interpreter reaches the script, so before the script
can start one of its own.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _Basic(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _Io(ctypes.Structure):  # IO_COUNTERS: operations, then bytes - read, written, other
    _fields_ = [(f"{kind}{unit}", ctypes.c_ulonglong) for unit in ("Ops", "Bytes") for kind in ("R", "W", "O")]


class _Extended(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _Basic),
        ("IoInfo", _Io),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class Job:
    """`with Job(memory) as job: job.add(process)`; `kill()` stops every process in it; leaving the block does too."""

    def __init__(self, memory_bytes: int):
        self._handle = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32 = kernel32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Extended()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY
        limits.JobMemoryLimit = memory_bytes
        if not kernel32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                                ctypes.sizeof(limits)):  # fmt: skip
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._handle = handle

    def add(self, process: subprocess.Popen) -> None:
        if self._handle is not None and not self._kernel32.AssignProcessToJobObject(self._handle, process._handle):  # type: ignore[attr-defined]
            raise ctypes.WinError(ctypes.get_last_error())

    def kill(self) -> None:
        if self._handle is not None:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)  # KILL_ON_JOB_CLOSE: whatever still runs goes now
            self._handle = None

    def __enter__(self) -> Job:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
