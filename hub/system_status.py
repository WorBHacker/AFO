"""What the machine is doing for the models: the GPU, memory, and which local model llama-swap has loaded.

The hub samples this every couple of seconds and publishes `system.status` (not stored) when it changes, so
every client shows the same picture without polling (D31). Probes are best effort: a missing NVIDIA driver
or a stopped llama-swap gives `null` / `reachable: false`, never an error.

- GPU: NVML through ctypes (nvml.dll ships with the NVIDIA driver), no extra package and no nvidia-smi process
- memory: GlobalMemoryStatusEx - physical RAM and the commit charge against the commit limit (RAM + page
  file), which is what large MoE models run into (ARCHITECTURE.md, section 2)
- llama-swap: GET /running
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import sys
from typing import Any

import httpx

log = logging.getLogger("hub.status")

MIB = 1024 * 1024
GIB = 1024 * MIB
DEFAULT_INTERVAL_S = 2.0


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvmlMemoryV2(ctypes.Structure):
    """v2 counts the driver's reserved memory apart: `used` is then what nvidia-smi calls used."""

    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]  # fmt: skip


NVML_MEMORY_V2 = ctypes.sizeof(_NvmlMemoryV2) | (2 << 24)  # NVML_STRUCT_VERSION(Memory, 2)


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class GpuProbe:
    """The first NVIDIA GPU through NVML. `sample()` is None when there is no driver or no GPU."""

    def __init__(self) -> None:
        self._nvml: Any = None
        self._handle = ctypes.c_void_p()
        self.name = ""
        try:
            nvml = ctypes.CDLL("nvml.dll" if os.name == "nt" else "libnvidia-ml.so.1")
            if nvml.nvmlInit_v2() != 0:
                return
            if nvml.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(0), ctypes.byref(self._handle)) != 0:
                return
            name = ctypes.create_string_buffer(96)
            if nvml.nvmlDeviceGetName(self._handle, name, ctypes.c_uint(96)) == 0:
                self.name = name.value.decode("utf-8", "replace")
            self._nvml = nvml
        except OSError as exc:
            log.info("no NVML (%s); GPU status is not shown", exc)

    def _memory(self) -> _NvmlMemory | _NvmlMemoryV2 | None:
        """VRAM as nvidia-smi shows it (v2: without the driver's reserve); v1 on a driver without v2."""
        memory = _NvmlMemoryV2(version=NVML_MEMORY_V2)
        try:
            if self._nvml.nvmlDeviceGetMemoryInfo_v2(self._handle, ctypes.byref(memory)) == 0:
                return memory
        except AttributeError:
            pass
        older = _NvmlMemory()
        return older if self._nvml.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(older)) == 0 else None

    def sample(self) -> dict[str, Any] | None:
        if self._nvml is None:
            return None
        memory = self._memory()
        if memory is None:
            return None
        utilization = _NvmlUtilization()
        util_ok = self._nvml.nvmlDeviceGetUtilizationRates(self._handle, ctypes.byref(utilization)) == 0
        temperature = ctypes.c_uint()
        temp_ok = self._nvml.nvmlDeviceGetTemperature(self._handle, ctypes.c_int(0), ctypes.byref(temperature)) == 0
        return {
            "name": self.name,
            "vram_used_mib": memory.used // MIB,
            "vram_total_mib": memory.total // MIB,
            "util_pct": utilization.gpu if util_ok else None,
            "temp_c": temperature.value if temp_ok else None,
        }


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_sample() -> dict[str, float] | None:
    """RAM and commit charge in GiB (Windows only)."""
    if sys.platform != "win32":
        return None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {
        "ram_used_gib": round((status.ullTotalPhys - status.ullAvailPhys) / GIB, 1),
        "ram_total_gib": round(status.ullTotalPhys / GIB, 1),
        "commit_used_gib": round((status.ullTotalPageFile - status.ullAvailPageFile) / GIB, 1),
        "commit_limit_gib": round(status.ullTotalPageFile / GIB, 1),
    }


async def llama_swap_sample(base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=transport) as client:
            response = await client.get(f"{base_url.rstrip('/')}/running")
            response.raise_for_status()
    except httpx.HTTPError:
        return {"reachable": False, "running": []}
    running = [
        {"model": item.get("model", ""), "state": item.get("state", "")} for item in response.json().get("running", [])
    ]
    return {"reachable": True, "running": running}


class StatusMonitor:
    """Samples the machine on an interval; publishes `system.status` when the picture changes."""

    def __init__(self, bus, llama_swap_url: str, interval_s: float = DEFAULT_INTERVAL_S, gpu: GpuProbe | None = None):
        self._bus = bus
        self._llama_swap_url = llama_swap_url
        self._interval = interval_s
        self._gpu = gpu
        self._task: asyncio.Task | None = None
        self.latest: dict[str, Any] | None = None

    async def sample(self) -> dict[str, Any]:
        if self._gpu is None:
            self._gpu = await asyncio.to_thread(GpuProbe)
        gpu = await asyncio.to_thread(self._gpu.sample)
        status = {
            "gpu": gpu,
            "memory": await asyncio.to_thread(memory_sample),
            "local": await llama_swap_sample(self._llama_swap_url),
        }
        if status != self.latest:
            self.latest = status
            await self._bus.publish("system.status", status, persist=False)
        return status

    def start(self) -> None:
        if self._interval > 0:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self.sample()
            except Exception:
                log.exception("status sample failed")
            await asyncio.sleep(self._interval)
