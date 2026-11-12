"""How much video memory and RAM a llama.cpp model will take with its launch settings (stage 8.5, D39) - shown before
the settings are saved. An estimate from the model file's header (common/gguf.py): the weights split by the layers
on the GPU, the KV cache for the context, a compute buffer; the window says whether it fits.
"""

from __future__ import annotations

from typing import Any

from .i18n import tr

GIB = 1024**3
# bytes per cached value: llama.cpp's KV cache types (a block of 32 values with its scale)
CACHE_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32,
               "q4_1": 20 / 32, "q4_0": 18 / 32}  # fmt: skip
# llama.cpp's compute buffers on the GPU, roughly - one figure for the installer, tuning and the window (38.1)
COMPUTE_GIB = 0.8
# what a model may not count on, of the computer's own memory (38.1, the user's word): the video memory Windows and
# the desktop keep, the RAM Windows 11 keeps
RESERVE_GIB = 1.0
RAM_RESERVE_GIB = 3.0


def kv_bytes(info: dict[str, Any], context: int, cache_type: str) -> float:
    per_token = info["kv_layers"] * info["kv_heads"] * (info["key_length"] + info["value_length"])
    return context * per_token * CACHE_BYTES.get(cache_type, 2.0)


def estimate(
    info: dict[str, Any],
    *,
    context: int,
    cache_type: str = "f16",
    gpu_layers: str | int = "all",
    mmproj_bytes: int = 0,
    mmproj_on_gpu: bool = True,
    vram_total_mib: int | None = None,
    reserve_gib: float = RESERVE_GIB,
) -> dict[str, Any]:
    """{vram_gb, ram_gb, kv_gb, weights_gb, verdict, text}. `gpu_layers`: "all", a number, or "auto" (llama.cpp's
    --fit: it puts on the GPU what fits and the rest in RAM). `reserve_gib` - the video memory a model may not count
    on: the user's (38.3), else the default."""
    weights = float(info["size_bytes"])
    kv = kv_bytes(info, context, cache_type)
    blocks = max(info["layers"], 1) + 1  # the layers and the output
    total = vram_total_mib * 1024**2 / GIB if vram_total_mib else None
    vision_gpu = mmproj_bytes if mmproj_on_gpu else 0
    if gpu_layers == "auto":
        need = (weights + kv) / GIB + COMPUTE_GIB + vision_gpu / GIB
        room = (total - reserve_gib) if total else need
        vram = min(need, room)
        spill = need - vram  # the layers that did not fit: in RAM
        ram = spill + (mmproj_bytes - vision_gpu) / GIB
    else:
        share = 1.0 if gpu_layers == "all" else min(max(int(gpu_layers), 0), blocks) / blocks
        vram = (weights + kv) * share / GIB + (COMPUTE_GIB if share else 0) + vision_gpu / GIB
        spill = (weights + kv) * (1 - share) / GIB
        ram = spill + (mmproj_bytes - vision_gpu) / GIB  # the vision projector kept in RAM is not slower layers
    out = {"vram_gb": round(vram, 1), "ram_gb": round(ram, 1), "kv_gb": round(kv / GIB, 2),
           "weights_gb": round(weights / GIB, 1), "total_gb": round(total, 1) if total else None}  # fmt: skip
    where = tr("≈ {vram} ГБ видеопамяти из {total:g}", vram=out["vram_gb"], total=out["total_gb"]) if total else \
        tr("≈ {vram} ГБ видеопамяти", vram=out["vram_gb"])  # fmt: skip
    where += tr(" и {ram} ГБ ОЗУ", ram=out["ram_gb"]) if out["ram_gb"] >= 0.1 else ""
    where += tr(" (KV-кэш {kv} ГБ)", kv=out["kv_gb"])
    if total is None:
        out["verdict"] = "unknown"
    elif gpu_layers == "auto":
        out["verdict"] = "auto" if spill > 0.1 else "fits"
    elif vram <= total - reserve_gib:
        out["verdict"] = "fits" if spill < 0.1 else "split"
    else:
        out["verdict"] = "over"
    out["text"] = (
        where
        + {
            "fits": tr(" — помещается в видеопамять целиком"),
            "split": tr(" — часть слоёв в ОЗУ: работать будет медленнее"),
            "auto": tr(" — llama.cpp сам положит в видеопамять, что поместится, остальное — в ОЗУ (медленнее)"),
            "over": tr(
                " — не поместится в видеопамять: меньше слоёв на видеокарте, меньше контекст или сжатие KV-кэша"
            ),
            "unknown": "",
        }[out["verdict"]]
    )
    return out


def short_now(need: dict[str, Any], status: dict[str, Any] | None, own: dict[str, float] | None = None) -> str:
    """Words when the memory free right now is less than a model about to load needs (38.2): what fits is judged by
    the computer, and other programs hold more than the reserve counts on. Not a refusal - the model loads all the
    same, slower. `need` is estimate()'s; `own` - what llama-swap's models hold that unload for this one; '' - enough
    is free, or nothing sampled."""
    own = own or {}
    gpu = (status or {}).get("gpu") or {}
    memory = (status or {}).get("memory") or {}
    parts = []
    if gpu.get("vram_total_mib") and need.get("vram_gb"):
        total = gpu["vram_total_mib"] / 1024
        free = total - (gpu.get("vram_used_mib") or 0) / 1024 + own.get("vram_gb", 0.0)
        if need["vram_gb"] > free:
            parts.append(tr("{free} ГБ видеопамяти из {total:g}", free=round(max(free, 0.0), 1), total=round(total, 1)))
    if memory.get("ram_total_gib") and need.get("ram_gb", 0.0) >= 0.1:
        free = memory["ram_total_gib"] - (memory.get("ram_used_gib") or 0.0) + own.get("ram_gb", 0.0)
        if need["ram_gb"] > free:
            parts.append(tr("{free} ГБ ОЗУ", free=round(max(free, 0.0), 1)))
    if not parts:
        return ""
    return tr("сейчас свободно {what} — закройте лишние программы, иначе модель будет работать медленно",
              what=", ".join(parts))  # fmt: skip
