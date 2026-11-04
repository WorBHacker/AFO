"""The agent's scripts (run_python, D40) in a Windows AppContainer (D46, step 13.14): the network closed by Windows
itself, not by a patched socket module, and nothing outside the script's own folder.

Before, "no network" was the socket module made to refuse - a script got round it with ctypes or by starting
curl.exe - and a script read whatever the user can. An AppContainer is the sandbox Windows gives any program without
administrator rights: a process in it gets no network unless given the internetClient capability (never the hub on
127.0.0.1: loopback stays closed to it either way), and reads and writes only where an access rule names its
container. Two folders do: the script's own (data/sandbox/<run>) and the scripts' Python.

The scripts' Python is the core of the hub's own - python.exe, its DLLs and the standard library, ~50 MB - copied
once into LocalChat's folder (`app_home()/scripts-python/<version>`), and the container may read only that copy. The
user's Python installation is not touched, nor its site-packages: a script has the standard library.

Windows keeps the container's profile (its name, NAME) like an installed app's; `delete_profile` removes it.
"""

from __future__ import annotations

import ctypes
import functools
import os
import platform
import shutil
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO

from . import winjob

NAME = "LocalChat.Scripts"
INTERNET_CLIENT = "S-1-15-3-1"  # the capability "internetClient": the internet, not the local network
LEFT_OUT = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip"}  # of the standard library
ALREADY_EXISTS = 0x800700B7
SECURITY_CAPABILITIES = 0x00020009
HANDLE_LIST = 0x00020002
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
SE_GROUP_ENABLED = 0x00000004
WAIT_TIMEOUT = 0x00000102
_copying = threading.Lock()


class ContainerError(RuntimeError):
    """The container could not be made or started; the message is fit to show."""


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]  # fmt: skip


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]  # fmt: skip


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]  # fmt: skip


@functools.cache
def _api():
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                  ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(ctypes.c_void_p)]  # fmt: skip
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                                         ctypes.POINTER(ctypes.c_size_t)]  # fmt: skip
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                 ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]  # fmt: skip
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                      wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                      ctypes.c_void_p, ctypes.POINTER(_ProcessInformation)]  # fmt: skip
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    return userenv, advapi, kernel


@functools.cache
def _sid() -> tuple[int, str]:
    """The container's SID (made with its profile the first time), as a pointer and as text."""
    if os.name != "nt":
        raise ContainerError("an AppContainer is Windows only")
    userenv, advapi, kernel = _api()
    sid = ctypes.c_void_p()
    made = userenv.CreateAppContainerProfile(NAME, NAME, "AFO: the agent's scripts", None, 0,
                                             ctypes.byref(sid)) & 0xFFFFFFFF  # fmt: skip
    if made == ALREADY_EXISTS:
        made = userenv.DeriveAppContainerSidFromAppContainerName(NAME, ctypes.byref(sid)) & 0xFFFFFFFF
    if made != 0 or not sid.value:
        raise ContainerError(f"Windows did not make the container (0x{made:08x})")
    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ContainerError(f"the container's SID did not read ({ctypes.get_last_error()})")
    value = text.value or ""
    kernel.LocalFree(text)
    return sid.value, value


def grant(folder: Path, rights: str) -> None:
    """Lets the container into `folder` and what it holds: "RX" read and run, "M" change too (icacls)."""
    done = subprocess.run(["icacls", str(folder), "/grant", f"*{_sid()[1]}:(OI)(CI)({rights})", "/Q"],
                          capture_output=True, creationflags=CREATE_NO_WINDOW)  # fmt: skip
    if done.returncode != 0:
        said = done.stdout.decode("oem", "replace").strip() + done.stderr.decode("oem", "replace").strip()
        raise ContainerError(f"no access rule for the container on {folder}: {said[-200:]}")


def runtime(home: Path) -> Path:
    """python.exe of the scripts' own Python: the core of the hub's (no site-packages), copied the first time into
    `home`/scripts-python/<version> - a folder the container was let into before the files came, so they inherit
    its rule - then kept."""
    folder = home / "scripts-python" / platform.python_version()
    python = folder / "python.exe"
    with _copying:
        if python.is_file():
            return python
        base = Path(sys.base_prefix)
        part = folder.with_name(folder.name + ".part")
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        grant(part, "RX")
        try:
            for item in base.iterdir():
                if item.is_file() and item.suffix.lower() in (".exe", ".dll"):
                    shutil.copy2(item, part / item.name)
            shutil.copytree(base / "DLLs", part / "DLLs")
            top = base / "Lib"
            shutil.copytree(top, part / "Lib", ignore=lambda where, names: LEFT_OUT & set(names) if Path(where) == top
                            else [])  # fmt: skip
            part.rename(folder)
        except OSError as exc:
            shutil.rmtree(part, ignore_errors=True)
            raise ContainerError(f"the scripts' Python was not copied: {exc}") from exc
    return python


def run(argv: list[str], cwd: Path, env: dict[str, str], *, network: bool, job: winjob.Job, timeout_s: float,
        output: BinaryIO) -> int | None:  # fmt: skip
    """`argv` started in the container - suspended until it is in `job` - its output and errors into `output` (a file
    of the hub's: the container needs no rule for it), no input. Waits up to `timeout_s`: its exit code, or None when
    the time ran out (the caller stops the job)."""
    import msvcrt

    _userenv, advapi, kernel = _api()
    sid, _text = _sid()
    capabilities = (_SidAndAttributes * 1)()
    if network:
        pointer = ctypes.c_void_p()
        if not advapi.ConvertStringSidToSidW(INTERNET_CLIENT, ctypes.byref(pointer)):
            raise ContainerError(f"the network capability did not read ({ctypes.get_last_error()})")
        capabilities[0].Sid, capabilities[0].Attributes = pointer.value, SE_GROUP_ENABLED
    security = _SecurityCapabilities(sid, capabilities if network else None, 1 if network else 0, 0)
    with open(os.devnull, "rb") as nothing:
        handles = (wintypes.HANDLE * 2)(msvcrt.get_osfhandle(nothing.fileno()), msvcrt.get_osfhandle(output.fileno()))
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            raise ContainerError(f"no attribute list ({ctypes.get_last_error()})")
        try:
            for attribute, value, length in ((SECURITY_CAPABILITIES, ctypes.byref(security), ctypes.sizeof(security)),
                                             (HANDLE_LIST, handles, ctypes.sizeof(handles))):  # fmt: skip
                if not kernel.UpdateProcThreadAttribute(attributes, 0, attribute, value, length, None, None):
                    raise ContainerError(f"the container's attribute was not set ({ctypes.get_last_error()})")
            info = _StartupInfoEx()
            info.StartupInfo.cb = ctypes.sizeof(info)
            info.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            info.StartupInfo.hStdInput, info.StartupInfo.hStdOutput = handles[0], handles[1]
            info.StartupInfo.hStdError = handles[1]
            info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            pairs = sorted(env.items(), key=lambda item: item[0].upper())  # Windows wants the block sorted
            block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in pairs) + "\0")
            line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            process = _ProcessInformation()
            flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
            if not kernel.CreateProcessW(None, line, None, None, True, flags, block, str(cwd), ctypes.byref(info),
                                         ctypes.byref(process)):  # fmt: skip
                raise ContainerError(f"the script did not start in its container ({ctypes.get_last_error()})")
        finally:
            kernel.DeleteProcThreadAttributeList(attributes)
            for handle in handles:
                os.set_handle_inheritable(handle, False)
    try:
        try:
            job.add_handle(process.hProcess)  # before its first instruction: what it starts is in the job too
        except OSError:
            kernel.TerminateProcess(process.hProcess, 1)
            raise
        kernel.ResumeThread(process.hThread)
        if kernel.WaitForSingleObject(process.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT:
            return None
        code = wintypes.DWORD()
        kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel.CloseHandle(process.hThread)
        kernel.CloseHandle(process.hProcess)


def delete_profile() -> bool:
    """Removes the container's profile from Windows (the access rules on its two folders stay until they go)."""
    if os.name != "nt":
        return False
    return _api()[0].DeleteAppContainerProfile(NAME) == 0
