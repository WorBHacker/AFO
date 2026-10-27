"""The header of a GGUF model file - what a memory estimate needs (stage 8.5, D39): the architecture, its layers,
attention heads and their sizes, the trained context. Only the metadata at the start is read, never the weights; a
file that is not GGUF (or is cut short) is an error, not a crash.

The format: "GGUF", a version, the tensor and key counts, then the keys - each a string, a value type and a value
(numbers, strings, arrays of either). https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""

from __future__ import annotations

import functools
import struct
from pathlib import Path
from typing import Any, BinaryIO

# value type -> struct format of one item (8: string, 9: array are read their own way)
SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
MAX_KEYS = 100_000
MAX_STRING = 1 << 24


class GGUFError(ValueError):
    pass


def _read(f: BinaryIO, size: int) -> bytes:
    data = f.read(size)
    if len(data) != size:
        raise GGUFError("the file ends inside its header")
    return data


def _string(f: BinaryIO) -> str:
    (length,) = struct.unpack("<Q", _read(f, 8))
    if length > MAX_STRING:
        raise GGUFError("a string in the header is too long to be one")
    return _read(f, length).decode("utf-8", "replace")


def _value(f: BinaryIO, kind: int, keep: bool) -> Any:
    if kind in SCALARS:
        fmt = SCALARS[kind]
        return struct.unpack(fmt, _read(f, struct.calcsize(fmt)))[0]
    if kind == 8:
        return _string(f)
    if kind == 9:
        (item, count) = struct.unpack("<IQ", _read(f, 12))
        if item in SCALARS and not keep:  # a long array of numbers nobody needs: skipped at once
            f.seek(struct.calcsize(SCALARS[item]) * count, 1)
            return None
        values = [_value(f, item, keep) for _ in range(count)]
        return values if keep else None
    raise GGUFError(f"unknown value type {kind}")


def read_header(path: Path, wanted: tuple[str, ...] = ()) -> dict[str, Any]:
    """The header's keys: those named in `wanted` (or all scalar ones and short arrays when it is empty)."""
    with open(path, "rb") as f:
        if _read(f, 4) != b"GGUF":
            raise GGUFError(f"{path.name} is not a GGUF file")
        (version,) = struct.unpack("<I", _read(f, 4))
        if version < 2:
            raise GGUFError(f"GGUF version {version} is too old")
        _tensors, keys = struct.unpack("<QQ", _read(f, 16))
        if keys > MAX_KEYS:
            raise GGUFError("too many keys for a GGUF header")
        out: dict[str, Any] = {"gguf.version": version}
        for _ in range(keys):
            key = _string(f)
            (kind,) = struct.unpack("<I", _read(f, 4))
            keep = key in wanted if wanted else not key.startswith("tokenizer.")
            value = _value(f, kind, keep)
            if keep:
                out[key] = value
        return out


@functools.lru_cache(maxsize=32)
def _model_info(path: str, _size: int, _mtime: float) -> dict[str, Any]:
    head = read_header(Path(path), ("general.architecture", "general.name"))
    arch = head.get("general.architecture") or ""
    names = ("block_count", "context_length", "embedding_length", "attention.head_count", "attention.head_count_kv",
             "attention.key_length", "attention.value_length", "full_attention_interval")  # fmt: skip
    found = read_header(Path(path), tuple(f"{arch}.{n}" for n in names))

    def number(name: str) -> int:
        value = found.get(f"{arch}.{name}")
        if isinstance(value, list):  # per layer (some architectures): the largest
            value = max((v for v in value if isinstance(v, int | float)), default=0)
        return int(value or 0)

    layers, heads = number("block_count"), number("attention.head_count")
    kv_heads = number("attention.head_count_kv") or heads
    embd = number("embedding_length")
    head_dim = embd // heads if heads else 0
    interval = number("full_attention_interval")  # hybrid models: only every n-th layer keeps a KV cache
    return {
        "architecture": arch,
        "name": head.get("general.name") or "",
        "layers": layers,
        "kv_layers": layers // interval if interval > 1 else layers,
        "context_trained": number("context_length"),
        "kv_heads": kv_heads,
        "key_length": number("attention.key_length") or head_dim,
        "value_length": number("attention.value_length") or head_dim,
    }


def model_info(path: Path) -> dict[str, Any]:
    """What a memory estimate needs from a model file, and its size; cached while the file stays the same."""
    stat = path.stat()
    return {**_model_info(str(path), stat.st_size, stat.st_mtime), "size_bytes": stat.st_size}
