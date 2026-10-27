"""How much video memory and RAM a llama.cpp model will take with its launch settings (stage 8.5, D39) - shown before
the settings are saved. An estimate from the model file's header (common/gguf.py): the weights split by the layers
on the GPU, the KV cache for the context, a compute buffer; the window says whether it fits.
"""

from __future__ import annotations

from typing import Any

GIB = 1024**3
# bytes per cached value: llama.cpp's KV cache types (a block of 32 values with its scale)
CACHE_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32,
               "q4_1": 20 / 32, "q4_0": 18 / 32}  # fmt: skip
COMPUTE_GIB = 0.8  # llama.cpp's compute buffers on the GPU, roughly
RESERVE_GIB = 0.6  # what Windows and other programs keep of the video memory


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
) -> dict[str, Any]:
    """{vram_gb, ram_gb, kv_gb, weights_gb, verdict, text}. `gpu_layers`: "all", a number, or "auto" (llama.cpp's
    --fit: it puts on the GPU what fits and the rest in RAM)."""
    weights = float(info["size_bytes"])
    kv = kv_bytes(info, context, cache_type)
    blocks = max(info["layers"], 1) + 1  # the layers and the output
    total = vram_total_mib * 1024**2 / GIB if vram_total_mib else None
    vision_gpu = mmproj_bytes if mmproj_on_gpu else 0
    if gpu_layers == "auto":
        need = (weights + kv) / GIB + COMPUTE_GIB + vision_gpu / GIB
        room = (total - RESERVE_GIB) if total else need
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
    where = f"≈ {out['vram_gb']} ГБ видеопамяти" + (f" из {out['total_gb']:g}" if total else "")
    where += f" и {out['ram_gb']} ГБ ОЗУ" if out["ram_gb"] >= 0.1 else ""
    where += f" (KV-кэш {out['kv_gb']} ГБ)"
    if total is None:
        out["verdict"] = "unknown"
    elif gpu_layers == "auto":
        out["verdict"] = "auto" if spill > 0.1 else "fits"
    elif vram <= total - RESERVE_GIB:
        out["verdict"] = "fits" if spill < 0.1 else "split"
    else:
        out["verdict"] = "over"
    out["text"] = (
        where
        + {
            "fits": " — помещается в видеопамять целиком",
            "split": " — часть слоёв в ОЗУ: работать будет медленнее",
            "auto": " — llama.cpp сам положит в видеопамять, что поместится, остальное — в ОЗУ (медленнее)",
            "over": " — не поместится в видеопамять: меньше слоёв на видеокарте, меньше контекст или сжатие KV-кэша",
            "unknown": "",
        }[out["verdict"]]
    )
    return out
