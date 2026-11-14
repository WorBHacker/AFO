"""The header of a GGUF model file - what a memory estimate needs (stage 8.5, D39): the architecture, its layers,
attention heads and their sizes, the trained context; of a Mixture-of-Experts model (44.1) what its experts weigh in
each layer. Only the metadata at the start is read, never the weights; a file that is not GGUF (or is cut short) is an
error, not a crash.

The format: "GGUF", a version, the tensor and key counts, then the keys - each a string, a value type and a value
(numbers, strings, arrays of either) - then the table of tensors: each a name, its dimensions, ggml's type and where
its data is. https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""

from __future__ import annotations

import functools
import math
import re
import struct
from pathlib import Path
from typing import Any, BinaryIO

# value type -> struct format of one item (8: string, 9: array are read their own way)
SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
MAX_KEYS = 100_000
MAX_STRING = 1 << 24
MAX_TENSORS = 1_000_000
MAX_DIMS = 8
# ggml's tensor types: values in a block, bytes of a block (ggml.c's type_traits) - what a tensor of each weighs
TYPE_SIZES = {0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34), 9: (32, 36),
              10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210), 15: (256, 292),
              16: (256, 66), 17: (256, 74), 18: (256, 98), 19: (256, 50), 20: (32, 18), 21: (256, 110),
              22: (256, 82), 23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8),
              29: (256, 56), 30: (1, 2), 34: (256, 54), 35: (256, 66), 39: (32, 17)}  # fmt: skip
# the experts' tensors as llama.cpp's --cpu-moe and --n-cpu-moe find them (b11899: «blk\.\d+\.ffn_(up|down|gate_up|
# gate)_(ch|)exps»), with the layer's number
EXPERTS = re.compile(r"^blk\.(\d+)\.ffn_(?:up|down|gate_up|gate)_(?:ch)?exps")
PART = re.compile(r"^(.+)-00001-of-(\d{5})(\.gguf)$", re.IGNORECASE)  # the first of a model's files: gguf-split's


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
        if keep:
            return _string(f)
        (length,) = struct.unpack("<Q", _read(f, 8))  # a string nobody needs (a token): skipped
        if length > MAX_STRING:
            raise GGUFError("a string in the header is too long to be one")
        f.seek(length, 1)
        return None
    if kind == 9:
        (item, count) = struct.unpack("<IQ", _read(f, 12))
        if item in SCALARS and not keep:  # a long array of numbers nobody needs: skipped at once
            f.seek(struct.calcsize(SCALARS[item]) * count, 1)
            return None
        values = [_value(f, item, keep) for _ in range(count)]
        return values if keep else None
    raise GGUFError(f"unknown value type {kind}")


def _keys(f: BinaryIO, name: str, wanted: tuple[str, ...], until_tokenizer: bool) -> dict[str, Any]:
    if _read(f, 4) != b"GGUF":
        raise GGUFError(f"{name} is not a GGUF file")
    (version,) = struct.unpack("<I", _read(f, 4))
    if version < 2:
        raise GGUFError(f"GGUF version {version} is too old")
    tensors, keys = struct.unpack("<QQ", _read(f, 16))
    if keys > MAX_KEYS:
        raise GGUFError("too many keys for a GGUF header")
    out: dict[str, Any] = {"gguf.version": version, "gguf.tensors": tensors}
    for _ in range(keys):
        key = _string(f)
        arch = out.get("general.architecture")
        if until_tokenizer and key.startswith("tokenizer.") and arch and f"{arch}.block_count" in out:
            break  # the model's own keys come before the tokenizer's long lists: they are not read at all
        (kind,) = struct.unpack("<I", _read(f, 4))
        keep = key in wanted if wanted else not key.startswith("tokenizer.")
        value = _value(f, kind, keep)
        if keep:
            out[key] = value
    return out


def read_header(source: Path | BinaryIO, wanted: tuple[str, ...] = (), *, until_tokenizer: bool = False
                ) -> dict[str, Any]:  # fmt: skip
    """The header's keys: those named in `wanted` (or all scalar ones and short arrays when it is empty). The source
    is a file or a stream read from its start (a download's first bytes: 10.1); `until_tokenizer` stops at the
    tokenizer's keys once the architecture's are read."""
    if isinstance(source, Path):
        with open(source, "rb") as f:
            return _keys(f, source.name, wanted, until_tokenizer)
    return _keys(source, "the file", wanted, until_tokenizer)


def header_info(head: dict[str, Any]) -> dict[str, Any]:
    """What a memory estimate needs, from a header's keys (read_header)."""
    arch = head.get("general.architecture") or ""

    def number(name: str) -> int:
        value = head.get(f"{arch}.{name}")
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
        # Mixture of Experts (44.1): the experts in each layer and how many each word uses; the layers that guess the
        # next words themselves (MTP: llama.cpp's draft-mtp)
        "experts": number("expert_count"),
        "experts_used": number("expert_used_count"),
        "mtp_layers": number("nextn_predict_layers"),
    }


# a model's vocabulary (42.3): what llama.cpp compares before it takes a draft model for guessing ahead
TOKENIZER = ("tokenizer.ggml.model", "tokenizer.ggml.pre", "tokenizer.ggml.tokens", "tokenizer.ggml.bos_token_id",
             "tokenizer.ggml.eos_token_id", "tokenizer.ggml.add_bos_token", "tokenizer.ggml.add_eos_token")  # fmt: skip
VOCAB_SLACK = 128  # llama.cpp's SPEC_VOCAB_MAX_SIZE_DIFFERENCE: a draft's vocabulary this much longer or shorter
VOCAB_FROM = 5  # SPEC_VOCAB_CHECK_START_TOKEN_ID: the first tokens are not compared


def tokenizer_info(source: Path | BinaryIO) -> dict[str, Any]:
    """A model's vocabulary from its header (a file, or the first bytes of a download): its kind, the tokens, the
    begin and end tokens and whether they are added. The whole header is read - the tokens come after the model's
    keys."""
    head = read_header(source, TOKENIZER)
    tokens = head.get("tokenizer.ggml.tokens")
    return {"model": head.get("tokenizer.ggml.model") or "", "pre": head.get("tokenizer.ggml.pre") or "",
            "tokens": tokens if isinstance(tokens, list) else [], "bos": head.get("tokenizer.ggml.bos_token_id"),
            "eos": head.get("tokenizer.ggml.eos_token_id"), "add_bos": head.get("tokenizer.ggml.add_bos_token"),
            "add_eos": head.get("tokenizer.ggml.add_eos_token")}  # fmt: skip


def same_vocabulary(model: dict[str, Any], draft: dict[str, Any]) -> bool:
    """Whether llama.cpp takes this draft for this model (common_speculative_are_compatible): the same kind, the same
    begin and end tokens, at most VOCAB_SLACK tokens more or less, each token from VOCAB_FROM on the same."""
    a, b = model["tokens"], draft["tokens"]
    if not a or not b or model["model"] != draft["model"] or abs(len(a) - len(b)) > VOCAB_SLACK:
        return False
    if any(model[key] != draft[key] for key in ("bos", "eos", "add_bos", "add_eos")):
        return False
    n = min(len(a), len(b))
    return a[VOCAB_FROM:n] == b[VOCAB_FROM:n]


def _tensors(f: BinaryIO, count: int) -> list[tuple[str, int, int | None]]:
    """The table of tensors after the keys: each one's name, its values, and its bytes (None: a type TYPE_SIZES
    does not know)."""
    if count > MAX_TENSORS:
        raise GGUFError("too many tensors for a GGUF file")
    out: list[tuple[str, int, int | None]] = []
    for _ in range(count):
        name = _string(f)
        (dims,) = struct.unpack("<I", _read(f, 4))
        if dims > MAX_DIMS:
            raise GGUFError(f"the tensor {name} has too many dimensions")
        values = math.prod(struct.unpack(f"<{dims}Q", _read(f, 8 * dims)))
        kind, _offset = struct.unpack("<IQ", _read(f, 12))
        block = TYPE_SIZES.get(kind)
        out.append((name, values, values * block[1] // block[0] if block else None))
    return out


def _expert_bytes(files: list[Path]) -> list[int]:
    """What the experts weigh in each layer (44.1), from the tables of the model's files: the whole header is read,
    a few megabytes. A tensor of a type ggml has added since - its share, by its values, of the file's bytes the
    others do not take."""
    layers: dict[int, float] = {}
    for file in files:
        with open(file, "rb") as f:
            table = _tensors(f, _keys(f, file.name, (), False)["gguf.tensors"])
            data = file.stat().st_size - f.tell()
        known = sum(size for _, _, size in table if size is not None)
        unknown = sum(values for _, values, size in table if size is None)
        for name, values, size in table:
            if match := EXPERTS.match(name):
                layer = int(match.group(1))
                share = max(data - known, 0) * values / unknown if size is None else size
                layers[layer] = layers.get(layer, 0.0) + share
    return [round(layers.get(i, 0.0)) for i in range(max(layers, default=-1) + 1)]


def parts(path: Path) -> list[Path]:
    """A model's files: those of a model split by gguf-split (`…-00001-of-00003.gguf`, 44.1) that are on the disk,
    else the file itself."""
    match = PART.match(path.name)
    if match is None:
        return [path]
    rest = (path.with_name(f"{match.group(1)}-{i:05d}-of-{match.group(2)}{match.group(3)}")
            for i in range(2, int(match.group(2)) + 1))  # fmt: skip
    return [path, *(p for p in rest if p.is_file())]


@functools.lru_cache(maxsize=32)
def _model_info(files: tuple[tuple[str, int, float], ...]) -> dict[str, Any]:
    info = header_info(read_header(Path(files[0][0]), until_tokenizer=True))
    if info["experts"]:
        info["expert_bytes"] = _expert_bytes([Path(name) for name, _, _ in files])
    return info


def model_info(path: Path) -> dict[str, Any]:
    """What a memory estimate needs from a model file, and its size - of each of its files when it is split; cached
    while its files stay the same."""
    files = []
    for part in parts(path):
        stat = part.stat()
        files.append((str(part), stat.st_size, stat.st_mtime))
    return {**_model_info(tuple(files)), "size_bytes": sum(size for _, size, _ in files)}
