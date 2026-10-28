"""This computer's graphics cards for the first-run wizard (stage 10.2, D37, Q19): which ones there are, their video
memory and driver, and so which llama.cpp build fits - CUDA for NVIDIA, Vulkan for AMD and Intel, the processor when
there is no card worth it.

Windows only, without administrator rights and without extra packages: the display adapters' keys in the registry
(HKLM\\...\\Class\\{4d36e968-...}\\0000, 0001...) carry each card's name, vendor (PCI\\VEN_xxxx), driver version and
memory (HardwareInformation.qwMemorySize - the true size, where WMI's AdapterRAM stops at 4 GB); NVIDIA's driver
says through NVML which CUDA version it runs. Virtual adapters (no PCI vendor) are left out.
"""

from __future__ import annotations

import contextlib
import ctypes
import re
import sys
from typing import Any

GIB = 1024**3
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
MIN_VRAM_GIB = 3.5  # below this a card (an integrated one, sharing RAM) runs a model no faster than the processor


def _values(key) -> dict[str, Any]:
    import winreg

    out: dict[str, Any] = {}
    i = 0
    while True:
        try:
            name, value, kind = winreg.EnumValue(key, i)
        except OSError:
            return out
        i += 1
        out[name] = int.from_bytes(value[:8], "little") if kind == winreg.REG_BINARY else value


def adapters() -> list[dict[str, Any]]:
    """The physical graphics cards: name, vendor (nvidia | amd | intel | other), video memory in GiB, driver."""
    if sys.platform != "win32":
        return []
    import winreg

    out = []
    with contextlib.suppress(OSError), winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    values = _values(key)
            except OSError:
                continue
            vendor = re.search(r"(?i)ven_([0-9a-f]{4})", str(values.get("MatchingDeviceId") or ""))
            if not vendor:  # a virtual display (remote desktop, a screen-sharing driver)
                continue
            memory = values.get("HardwareInformation.qwMemorySize") or values.get("HardwareInformation.MemorySize")
            out.append({"name": str(values.get("DriverDesc") or "?"), "vendor": VENDORS.get(vendor.group(1).lower(),
                        "other"), "vram_gib": round(int(memory or 0) / GIB, 1),
                        "driver": str(values.get("DriverVersion") or "")})  # fmt: skip
    return sorted(out, key=lambda a: -a["vram_gib"])


def nvidia_driver() -> dict[str, Any] | None:
    """NVIDIA's driver: its version and the CUDA version it runs (13030 = 13.3); None without one."""
    try:
        nvml = ctypes.CDLL("nvml.dll" if sys.platform == "win32" else "libnvidia-ml.so.1")
        if nvml.nvmlInit_v2() != 0:
            return None
        cuda = ctypes.c_int()
        version = ctypes.create_string_buffer(80)
        if nvml.nvmlSystemGetCudaDriverVersion_v2(ctypes.byref(cuda)) != 0:
            return None
        nvml.nvmlSystemGetDriverVersion(version, 80)
        return {"version": version.value.decode("ascii", "replace"), "cuda": cuda.value}
    except OSError, AttributeError:
        return None


def build_for(cards: list[dict[str, Any]], nvidia: dict[str, Any] | None) -> dict[str, str]:
    """The llama.cpp build for this computer: {build: cuda-13 | cuda-12 | vulkan | cpu, card, why}."""
    usable = [c for c in cards if c["vram_gib"] >= MIN_VRAM_GIB]
    green = next((c for c in usable if c["vendor"] == "nvidia"), None)
    if green and nvidia and nvidia["cuda"] >= 13000:
        return {"build": "cuda-13", "card": green["name"], "why": "видеокарта NVIDIA: сборка с CUDA — быстрее всего"}
    if green and nvidia and nvidia["cuda"] >= 12040:
        return {"build": "cuda-12", "card": green["name"], "why": "видеокарта NVIDIA со старым драйвером: CUDA 12"}
    if usable:
        card = usable[0]
        why = "драйвер NVIDIA не ответил — Vulkan" if card["vendor"] == "nvidia" else \
            "видеокарта AMD или Intel: сборка с Vulkan"  # fmt: skip
        return {"build": "vulkan", "card": card["name"], "why": why}
    return {"build": "cpu", "card": "", "why": "подходящей видеокарты нет: модели будут работать на процессоре — "
                                                 "только маленькие и медленнее"}  # fmt: skip
