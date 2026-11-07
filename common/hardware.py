"""This computer's graphics cards for the first-run wizard (stage 10.2, D37, Q19): which ones there are, their video
memory and driver, and so which llama.cpp build fits - CUDA for NVIDIA, Vulkan for AMD and Intel, the processor when
there is no card worth it.

Windows only, without administrator rights and without extra packages: the display adapters' keys in the registry
(HKLM\\...\\Class\\{4d36e968-...}\\0000, 0001...) carry each card's name, vendor (PCI\\VEN_xxxx), driver version and
memory (HardwareInformation.qwMemorySize - the true size, where WMI's AdapterRAM stops at 4 GB); NVIDIA's driver
says through NVML which CUDA version it runs. Virtual adapters (no PCI vendor) are left out.

Pictures and videos (stage 29.2, D55) need more: DXGI's dedicated and shared memory (an integrated card has little of
the first and works in the second), NVIDIA's compute capability (stable-diffusion.cpp's CUDA build starts at Pascal,
6.1), whether Vulkan's loader is installed, and the processor's vector units.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import sys
from typing import Any

from .i18n import tr

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
        device, major, minor = ctypes.c_void_p(), ctypes.c_int(), ctypes.c_int()
        found = nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(device)) == 0
        found = found and nvml.nvmlDeviceGetCudaComputeCapability(device, ctypes.byref(major), ctypes.byref(minor)) == 0
        return {"version": version.value.decode("ascii", "replace"), "cuda": cuda.value,
                "compute": [major.value, minor.value] if found else None}  # the first card's  # fmt: skip
    except OSError, AttributeError:
        return None


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", ctypes.c_uint * 2), ("Flags", ctypes.c_uint)]  # fmt: skip


DXGI_SOFTWARE = 2  # DXGI_ADAPTER_FLAG_SOFTWARE: Windows' own renderer on the processor


def _dxgi_card(name: str, vendor: int, dedicated: int, shared: int, flags: int) -> dict[str, Any] | None:
    if flags & DXGI_SOFTWARE:
        return None
    return {"name": name, "vendor": VENDORS.get(f"{vendor:04x}", "other"), "dedicated_gib": round(dedicated / GIB, 1),
            "shared_gib": round(shared / GIB, 1)}  # fmt: skip


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    """A COM object's method by its place in the vtable, called with the object as the first argument."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def dxgi_adapters() -> list[dict[str, Any]]:
    """The hardware adapters as DXGI sees them: name, vendor, dedicated video memory and the system memory each may
    share, in GiB; [] where DXGI does not answer."""
    if sys.platform != "win32":
        return []
    iid = (ctypes.c_byte * 16).from_buffer_copy(bytes.fromhex("78ae0a776ff2ba4da829253c83d1b387"))  # IDXGIFactory1
    factory = ctypes.c_void_p()
    try:
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return []
    except OSError, AttributeError:
        return []
    out, seen = [], set()
    try:
        for i in range(16):
            adapter = ctypes.c_void_p()
            if _method(factory, 12, ctypes.c_uint, ctypes.c_void_p)(factory, i, ctypes.byref(adapter)) != 0:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            desc = _AdapterDesc1()
            try:
                if _method(adapter, 10, ctypes.c_void_p)(adapter, ctypes.byref(desc)) != 0:  # GetDesc1
                    continue
                card = _dxgi_card(desc.Description, desc.VendorId, desc.DedicatedVideoMemory, desc.SharedSystemMemory,
                                  desc.Flags)  # fmt: skip
                # One card can be listed twice under two LUIDs (seen here with the driver restarted); two cards of
                # one model are then one entry too - an engine runs on one card.
                model = (desc.VendorId, desc.DeviceId, desc.SubSysId, desc.Revision)
                if card and model not in seen:
                    seen.add(model)
                    out.append(card)
            finally:
                _method(adapter, 2)(adapter)  # Release
    finally:
        _method(factory, 2)(factory)
    return out


def vulkan_loader() -> bool:
    """Whether Vulkan's loader is installed: the graphics drivers of NVIDIA, AMD and Intel put it in System32."""
    system = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
    return sys.platform == "win32" and os.path.isfile(os.path.join(system, "vulkan-1.dll"))


PASCAL = (6, 1)  # stable-diffusion.cpp's CUDA build starts here (its arch list, 61-120)
CUDA_12 = 12000  # the build's CUDA 12.8 runs on drivers from CUDA 12.0 on (528.33)
INTEGRATED_GIB = 1.0  # less memory of its own than this: an integrated card, it works in shared memory
# By 29.1's measurements (docs/visual.md §2a): FLUX.2-klein draws 512² from a 1 GB budget at a 2.1 GB peak - more
# than a 2 GB card has - and 1024² from 2-3 GB; Z-Image-Turbo wants more; Wan2.2's Turbo video at 480p fits a 4 GB
# budget, but a 4 GB card takes 10+ minutes, a 6 GB one minutes; the weights out of the card take up to 9 GB of RAM;
# tiles are 4 times faster than a retry on a small budget and twice as slow on a whole big card.
MAIN_GIB, QUALITY_GIB = 3.0, 6.0
VIDEO_GIB, TRY_VIDEO_GIB, VIDEO_RAM_GIB = 6.0, 4.0, 16.0
NO_TILES_GIB = 10.0


def pictures_for(cards: list[dict[str, Any]], nvidia: dict[str, Any] | None, *, vulkan: bool,
                 ram_gib: float) -> dict[str, Any]:  # fmt: skip
    """What pictures and videos this computer can make, before its trial run (D55, 29.2): the engine's build
    (cuda12 | vulkan | cpu), the card it runs on and its memory, the pictures' tier (main: FLUX.2-klein; light: SDXS
    and the main one slowly), whether Z-Image-Turbo is offered, the video (local | slow | cloud), whether a video's
    VAE goes in tiles. `cards` - dxgi_adapters()."""
    discrete = sorted((c for c in cards if c["dedicated_gib"] >= INTEGRATED_GIB), key=lambda c: -c["dedicated_gib"])
    green = next((c for c in discrete if c["vendor"] == "nvidia"), None)
    compute = tuple((nvidia or {}).get("compute") or (0, 0))
    if green and nvidia and compute >= PASCAL and nvidia["cuda"] >= CUDA_12:
        build, card = "cuda12", green
    elif vulkan and (discrete or cards):
        build, card = "vulkan", (discrete or sorted(cards, key=lambda c: -c["shared_gib"]))[0]
    else:
        build, card = "cpu", None
    on_card = card is not None and card["dedicated_gib"] >= INTEGRATED_GIB
    memory = card["dedicated_gib"] if on_card else card["shared_gib"] if card else round(ram_gib, 1)
    enough_ram = ram_gib >= VIDEO_RAM_GIB
    if build == "cuda12" and memory >= VIDEO_GIB and enough_ram:
        video = "local"
    elif on_card and enough_ram and memory >= (TRY_VIDEO_GIB if build == "cuda12" else VIDEO_GIB):
        video = "slow"
    else:
        video = "cloud"
    plan = {"build": build, "card": card["name"] if card else "", "memory_gib": memory,
            "pictures": "main" if on_card and memory >= MAIN_GIB else "light",
            "quality": on_card and memory >= QUALITY_GIB, "video": video,
            "tiles": build != "cuda12" or memory < NO_TILES_GIB}  # fmt: skip
    return plan | {"why": _pictures_why(plan, card, compute, nvidia)}


def _pictures_why(plan: dict[str, Any], card: dict[str, Any] | None, compute: tuple, nvidia: dict | None) -> str:
    if plan["build"] == "cpu":
        where = tr("подходящей видеокарты нет — картинки на процессоре: маленькая модель за секунды, большая — минуты")
    elif plan["pictures"] == "light":
        where = tr("{card} работает в общей памяти — маленькая модель за секунды, большая — минуты; пробная картинка "
                   "решит, видеокарта это будет или процессор", card=plan["card"])  # fmt: skip
    elif plan["build"] == "vulkan" and card and card["vendor"] == "nvidia":
        old = compute < PASCAL if nvidia else False
        where = tr("{card}: сборка Vulkan — {reason}", card=plan["card"],
                   reason=tr("для CUDA карта слишком старая") if old else tr("драйвер NVIDIA старше нужного для CUDA "
                                                                              "12 (от 528.33)"))  # fmt: skip
    else:
        where = tr("{card}: картинки 1024 × 1024 за секунды", card=plan["card"])
    if plan["video"] == "local":
        video = tr("видео — здесь, минуты на ролик")
    elif plan["video"] == "slow":
        video = tr("видео здесь — только попробовать: десятки минут на ролик")
    else:
        video = tr("видео — в облаке: здесь нужна видеокарта NVIDIA от 6 ГБ и 16 ГБ ОЗУ")
    return f"{where}; {video}"


def cpu_features() -> dict[str, bool]:
    """The processor's vector units: the engines on the processor are several times faster with AVX2."""
    present = ctypes.windll.kernel32.IsProcessorFeaturePresent if sys.platform == "win32" else (lambda _: 0)
    # PF_AVX_INSTRUCTIONS_AVAILABLE, PF_AVX2_..., PF_AVX512F_...
    return {"avx": bool(present(39)), "avx2": bool(present(40)), "avx512": bool(present(41))}


def build_for(cards: list[dict[str, Any]], nvidia: dict[str, Any] | None) -> dict[str, str]:
    """The llama.cpp build for this computer: {build: cuda-13 | cuda-12 | vulkan | cpu, card, why}."""
    usable = [c for c in cards if c["vram_gib"] >= MIN_VRAM_GIB]
    green = next((c for c in usable if c["vendor"] == "nvidia"), None)
    if green and nvidia and nvidia["cuda"] >= 13000:
        return {
            "build": "cuda-13",
            "card": green["name"],
            "why": tr("видеокарта NVIDIA: сборка с CUDA — быстрее всего"),
        }
    if green and nvidia and nvidia["cuda"] >= 12040:
        return {"build": "cuda-12", "card": green["name"], "why": tr("видеокарта NVIDIA со старым драйвером: CUDA 12")}
    if usable:
        card = usable[0]
        why = tr("драйвер NVIDIA не ответил — Vulkan") if card["vendor"] == "nvidia" else \
            tr("видеокарта AMD или Intel: сборка с Vulkan")  # fmt: skip
        return {"build": "vulkan", "card": card["name"], "why": why}
    return {"build": "cpu", "card": "", "why": tr("подходящей видеокарты нет: модели будут работать на процессоре — "
                                                    "только маленькие и медленнее")}  # fmt: skip
