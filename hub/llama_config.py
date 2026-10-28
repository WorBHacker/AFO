"""llama-swap's config.yaml as the window edits it (stage 8.5, D39, Q25): a local model's launch settings - context,
layers on the GPU, KV cache type, flash attention, parallel slots, and `ttl` (unload after idle seconds).

The hub changes only those, in the text itself, and leaves every other line - comments, macros, other models - as
it was. Each write is preceded by a copy beside the file (config.yaml.bak-<date-time>, the last ten kept); llama-swap
reloads the file by itself (--watch-config), and the write counts only when llama-swap lists every model again -
otherwise the copy comes back. Models served by Strata are only shown: their profiles are Strata's to write; their
`ttl` is llama-swap's and can be set.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from common.paths import PROJECT_ROOT, engines_home

CONFIG_ENV = "LOCALCHAT_LLAMA_SWAP_CONFIG"
CONFIG_SETTING = "llama_swap.config"  # the file chosen in the window (the settings table)
DEFAULT_CONFIG = PROJECT_ROOT.parent / "llama-swap" / "config.yaml"
configured: Path | None = None  # the chosen file, as the hub read it from its settings
KEEP_BACKUPS = 10
RELOAD_S = 15.0  # llama-swap rereads the file within a second or two (--watch-config)
NOT_ANSWERING = "llama-swap не отвечает"

# the launch settings: the flags that carry each (the first is written when the flag is new)
FLAGS = {
    "context": ("-c", "--ctx-size"),
    "gpu_layers": ("-ngl", "--n-gpu-layers", "--gpu-layers"),
    "fit": ("--fit",),
    "flash_attn": ("-fa", "--flash-attn"),
    "cache_k": ("-ctk", "--cache-type-k"),
    "cache_v": ("-ctv", "--cache-type-v"),
    "parallel": ("-np", "--parallel"),
    "mmproj": ("--mmproj",),
    "model": ("-m", "--model"),
}
CACHE_TYPES = ("f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0")
_TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')
_MODEL_KEY = re.compile(r'^  (?:"([^"]+)"|\'([^\']+)\'|([^\s:#][^:]*)):\s*(?:#.*)?$')


class ConfigError(ValueError):
    """The config cannot be read or changed as asked; the message says why, in words for the window."""


def config_path() -> Path:
    """llama-swap's config: LOCALCHAT_LLAMA_SWAP_CONFIG; the file chosen in the window's settings (10.3); the one
    beside LocalChat's folder on the computer it was built on; else the one the first-run wizard writes (10.2)."""
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    if configured is not None:
        return configured
    return DEFAULT_CONFIG if DEFAULT_CONFIG.is_file() else engines_home() / "llama-swap" / "config.yaml"


def config_source() -> str:
    """Where config_path() comes from: env | chosen | default."""
    return "env" if os.environ.get(CONFIG_ENV) else "chosen" if configured is not None else "default"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _section(lines: list[str], name: str) -> tuple[int, int] | None:
    """The lines of a top-level section (`models:`): from after its key to the next top-level key."""
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{name}:\s*(#.*)?$", line)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace()
                and not lines[i].startswith("#")), len(lines))  # fmt: skip
    return start + 1, end


def model_blocks(text: str) -> dict[str, tuple[int, int]]:
    """Each model's lines in the `models:` section: id -> (first line, end)."""
    lines = text.splitlines()
    section = _section(lines, "models")
    if section is None:
        raise ConfigError("в config.yaml нет раздела models")
    found: list[tuple[str, int]] = []
    for i in range(*section):
        match = _MODEL_KEY.match(lines[i])
        if match:
            found.append((next(g for g in match.groups() if g).strip(), i))
    out = {}
    for n, (model_id, start) in enumerate(found):
        end = found[n + 1][1] if n + 1 < len(found) else section[1]
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        out[model_id] = (start, end)
    return out


def macros(text: str) -> dict[str, str]:
    lines = text.splitlines()
    section = _section(lines, "macros")
    out = {}
    for i in range(*(section or (0, 0))):
        match = re.match(r"^\s+([\w-]+):\s*(.+?)\s*$", lines[i])
        if match:
            out[match.group(1)] = _unquote(match.group(2))
    return out


def _cmd_lines(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """The `cmd: |` block scalar of a model: its lines (the first after `cmd:` to the end of the indent)."""
    head = next((i for i in range(start, end) if re.match(r"^\s+cmd:\s*[|>]", lines[i])), None)
    if head is None:
        raise ConfigError("у модели нет команды запуска (cmd: |)")
    indent = len(lines[head]) - len(lines[head].lstrip()) + 1
    last = head + 1
    while last < end and (not lines[last].strip() or len(lines[last]) - len(lines[last].lstrip()) >= indent):
        last += 1
    while last > head + 1 and not lines[last - 1].strip():
        last -= 1
    return head + 1, last


def _flag(tokens: list[str], names: tuple[str, ...]) -> int | None:
    return next((i for i, t in enumerate(tokens) if t in names), None)


def model_view(text: str, model_id: str) -> dict[str, Any]:
    """What the window shows of a model: its engine, files and launch settings (llama.cpp), or its profile (Strata)."""
    blocks = model_blocks(text)
    if model_id not in blocks:
        raise ConfigError(f"в config.yaml нет модели {model_id}")
    lines = text.splitlines()
    start, end = blocks[model_id]
    block = lines[start:end]
    first, last = _cmd_lines(lines, start, end)
    cmd = " ".join(line.strip() for line in lines[first:last])
    for name, value in macros(text).items():
        cmd = cmd.replace(f"${{{name}}}", value)
    tokens = _TOKEN.findall(cmd)
    engine = "llama.cpp" if any("llama-server" in t for t in tokens) else "strata" if "serve" in tokens else "other"

    def value(setting: str) -> str | None:
        at = _flag(tokens, FLAGS[setting])
        return _unquote(tokens[at + 1]) if at is not None and at + 1 < len(tokens) else None

    ttl = next((int(m.group(1)) for line in block if (m := re.match(r"^\s{4}ttl:\s*(\d+)", line))), 0)
    name = next((_unquote(m.group(1)) for line in block if (m := re.match(r"^\s{4}name:\s*(.+?)\s*$", line))),
                model_id)  # fmt: skip
    view: dict[str, Any] = {"id": model_id, "name": name, "engine": engine, "editable": engine == "llama.cpp",
                            "ttl": ttl, "command": cmd}  # fmt: skip
    if engine == "strata":
        view["profile"] = value("model") or next((_unquote(tokens[i + 1]) for i, t in enumerate(tokens)
                                                  if t == "--profile" and i + 1 < len(tokens)), "")  # fmt: skip
    if engine == "llama.cpp":
        layers = value("gpu_layers")
        fit = value("fit")
        view.update({
            "file": value("model") or "",
            "mmproj": value("mmproj") or "",
            "mmproj_on_gpu": "--no-mmproj-offload" not in tokens,
            "context": int(value("context") or 0),
            "gpu_layers": "auto" if fit == "on" and layers is None else "all" if layers and int(layers) >= 99
                          else int(layers) if layers else "auto",
            "flash_attn": value("flash_attn") or "auto",
            "cache_type": value("cache_k") or "f16",
            "parallel": int(value("parallel") or 1),
        })  # fmt: skip
    return view


def check_changes(changes: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    """The settings asked for, checked; a Strata model takes only its ttl."""
    allowed = {"ttl"} if not view["editable"] else {"ttl", "context", "gpu_layers", "flash_attn", "cache_type",
                                                    "parallel"}  # fmt: skip
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key not in allowed:
            raise ConfigError(f"«{key}» у этой модели не меняется" + (" (её профиль пишет Strata)"
                                                                      if not view["editable"] else ""))  # fmt: skip
        if key in ("ttl", "context", "parallel"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{key}: нужно целое число")
            low, high = {"ttl": (0, 7 * 86400), "context": (512, 4_194_304), "parallel": (1, 64)}[key]
            if not low <= value <= high:
                raise ConfigError(f"{key}: от {low} до {high}")
        elif key == "gpu_layers":
            if value not in ("auto", "all") and (isinstance(value, bool) or not isinstance(value, int)
                                                 or not 0 <= value <= 999):  # fmt: skip
                raise ConfigError("слои на видеокарте: auto, all или число от 0 до 999")
        elif key == "flash_attn" and value not in ("on", "off", "auto"):
            raise ConfigError("flash attention: on, off или auto")
        elif key == "cache_type" and value not in CACHE_TYPES:
            raise ConfigError(f"сжатие KV-кэша: одно из {', '.join(CACHE_TYPES)}")
        out[key] = value
    return out


def _set_flag(lines: list[str], first: int, last: int, names: tuple[str, ...], value: str | None) -> None:
    """Sets (or with None removes) a flag and its value in the cmd lines, keeping each line's indent; a new flag
    goes to the line with the other options (the one with -c / -ngl / -fa, else the one before --host)."""
    for i in range(first, last):
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        tokens = _TOKEN.findall(lines[i])
        at = _flag(tokens, names)
        if at is None:
            continue
        if value is None:
            del tokens[at : at + 2]
        elif at + 1 < len(tokens):
            tokens[at + 1] = value
        else:
            tokens.append(value)
        lines[i] = indent + " ".join(tokens)
        return
    if value is None:
        return
    home = next((i for i in range(first, last) if any(_flag(_TOKEN.findall(lines[i]), FLAGS[k]) is not None
                                                      for k in ("context", "gpu_layers", "flash_attn", "fit"))),
                None)  # fmt: skip
    if home is None:
        home = next((i - 1 for i in range(first + 1, last) if "--host" in lines[i]), last - 1)
    lines[home] = lines[home].rstrip() + f" {names[0]} {value}"


def edit(text: str, model_id: str, changes: dict[str, Any]) -> str:
    """The config with this model's settings changed; every other line as it was."""
    view = model_view(text, model_id)
    changes = check_changes(changes, view)
    lines = text.splitlines()
    start, end = model_blocks(text)[model_id]
    if "ttl" in changes:
        at = next((i for i in range(start, end) if re.match(r"^\s{4}ttl:", lines[i])), None)
        if changes["ttl"] == 0 and at is not None:  # never unloaded by a timer: llama-swap's global setting
            del lines[at]
            end -= 1
        elif changes["ttl"]:
            line = f"    ttl: {changes['ttl']}"
            if at is not None:
                comment = re.search(r"\s+#.*$", lines[at])
                lines[at] = line + (comment.group(0) if comment else "")
            else:
                cmd_at = next(i for i in range(start, end) if re.match(r"^\s+cmd:", lines[i]))
                lines.insert(cmd_at, line)
                end += 1
    first, last = _cmd_lines(lines, start, end)
    if "context" in changes:
        _set_flag(lines, first, last, FLAGS["context"], str(changes["context"]))
        meta = next((i for i in range(start, end) if re.match(r"^\s{6}context:\s*\d+", lines[i])), None)
        if meta is not None:  # what llama-swap tells about the model (the hub's catalog reads it)
            lines[meta] = re.sub(r"(context:\s*)\d+", rf"\g<1>{changes['context']}", lines[meta])
    if "gpu_layers" in changes:
        layers = changes["gpu_layers"]
        if layers == "auto":
            _set_flag(lines, first, last, FLAGS["gpu_layers"], None)
            _set_flag(lines, first, last, FLAGS["fit"], "on")
        else:
            _set_flag(lines, first, last, FLAGS["fit"], None)
            _set_flag(lines, first, last, FLAGS["gpu_layers"], "99" if layers == "all" else str(layers))
    if "flash_attn" in changes:
        _set_flag(lines, first, last, FLAGS["flash_attn"], changes["flash_attn"])
    if "cache_type" in changes:
        _set_flag(lines, first, last, FLAGS["cache_k"], changes["cache_type"])
        _set_flag(lines, first, last, FLAGS["cache_v"], changes["cache_type"])
    if "parallel" in changes:
        _set_flag(lines, first, last, FLAGS["parallel"], str(changes["parallel"]))
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if set(model_blocks(out)) != set(model_blocks(text)):  # the edit must leave every model in place
        raise ConfigError("правка задела другие модели — не записано")
    return out


def backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.bak-*"))


def write(path: Path, text: str, *, now: datetime | None = None) -> Path:
    """A copy of the file as it was beside it, then the new text in its place (whole or not at all)."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    partial = path.with_name(f"{path.name}.part")
    partial.write_bytes(text.encode("utf-8"))
    os.replace(partial, path)
    for old in backups(path)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return backup


def restore(path: Path, backup: Path) -> None:
    """The copy back in the file's place (the copy itself stays)."""
    partial = path.with_name(f"{path.name}.part")
    shutil.copy2(backup, partial)
    os.replace(partial, path)


async def reloaded(base_url: str, ids: list[str], context: tuple[str, int] | None = None, *,
                   timeout_s: float | None = None) -> tuple[bool, str]:  # fmt: skip
    """Whether llama-swap read the file: it lists every model again (and the new context, when that changed)."""
    url = f"{base_url.rstrip('/')}/v1/models"
    deadline = time.monotonic() + (timeout_s or RELOAD_S)
    seen = ""
    async with httpx.AsyncClient(timeout=3.0) as http:
        while time.monotonic() < deadline:
            try:
                listed = {m["id"]: m for m in (await http.get(url)).json().get("data", [])}
            except httpx.HTTPError, ValueError:
                seen = NOT_ANSWERING
            else:
                missing = [m for m in ids if m not in listed]
                said = context and ((listed.get(context[0]) or {}).get("meta") or {}).get("llamaswap", {}).get(
                    "context")  # fmt: skip
                if not missing and (context is None or said == context[1]):
                    return True, ""
                seen = f"llama-swap не видит: {', '.join(missing)}" if missing else "llama-swap ещё не перечитал файл"
            await asyncio.sleep(0.5)
    return False, seen


# --- a model the installer adds and takes away (stage 10.1): its own block at the end of `models:` ---


def server_command(text: str) -> str:
    """How this config starts llama-server: its macro (`${llama_server}`), else the program an llama.cpp model
    runs; a config without either has no llama.cpp to give a new model."""
    for name, value in macros(text).items():
        if "llama-server" in value:
            return f"${{{name}}}"
    for model_id in model_blocks(text):
        view = model_view(text, model_id)
        if view["engine"] == "llama.cpp":
            program = next(t for t in _TOKEN.findall(view["command"]) if "llama-server" in t)
            return program if program.startswith(("'", '"')) else f'"{program}"' if " " in program else program
    raise ConfigError("в config.yaml нет llama-server: сначала нужна сборка llama.cpp (мастер первого запуска)")


def quoted(value: str) -> str:
    """A YAML double-quoted string with nothing to escape in it (llama-swap and this module's reader agree on it):
    double quotes become single ones, backslashes forward slashes."""
    return '"' + value.replace('"', "'").replace("\\", "/").replace("\n", " ") + '"'


def free_id(text: str, base: str) -> str:
    """A model id the config does not have yet: lower case letters, digits, dots, dashes."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-.") or "model"
    taken = set(model_blocks(text))
    found, n = slug, 2
    while found in taken:
        found, n = f"{slug}-{n}", n + 1
    return found


def model_block(model_id: str, *, name: str, description: str, server: str, model: str, mmproj: str = "",
                mmproj_on_gpu: bool = True, context: int, gpu_layers: str | int, cache_type: str = "q8_0",
                ttl: int = 0, metadata: dict[str, Any] | None = None) -> str:  # fmt: skip
    """A llama.cpp model's block for `models:`, written the way the user's own models are: one option per line of
    the `cmd`, the metadata llama-swap passes on to the hub's catalog."""
    layers = "--fit on" if gpu_layers == "auto" else f"-ngl {99 if gpu_layers == 'all' else int(gpu_layers)}"
    lines = [f"  {quoted(model_id)}:", f"    name: {quoted(name)}", f"    description: {quoted(description)}"]
    if ttl:
        lines.append(f"    ttl: {ttl}")
    lines += ["    cmd: |", f"      {server}", f"      -m {quoted(model.replace(os.sep, '/'))}"]
    if mmproj:
        lines.append(f"      --mmproj {quoted(mmproj.replace(os.sep, '/'))}" + ("" if mmproj_on_gpu
                                                                             else " --no-mmproj-offload"))  # fmt: skip
    lines += [
        f"      {layers} -fa on -c {context} -ctk {cache_type} -ctv {cache_type} -np 1",
        "      --jinja",
        "      --host 127.0.0.1 --port ${PORT}",
        "    checkEndpoint: /health",
        "    metadata:",
        "      engine: llama.cpp",
        f"      context: {context}",
        f"      vision: {'true' if mmproj else 'false'}",
    ]
    for key, value in (metadata or {}).items():
        lines.append(f"      {key}: {quoted(str(value))}")
    return "\n".join(lines)


def _in_groups(lines: list[str], model_id: str) -> bool:
    section = _section(lines, "groups")
    if section is None:
        return False
    named = re.compile(rf"(?<![\w.-]){re.escape(model_id)}(?![\w.-])")  # a member: a list item or in [a, b]
    return any(named.search(line.split("#", 1)[0]) for line in lines[section[0] : section[1]])


def add_model(text: str, model_id: str, block: str) -> str:
    """The config with a new model's block after the last model; every other line as it was."""
    if model_id in model_blocks(text):
        raise ConfigError(f"модель {model_id} в config.yaml уже есть")
    lines = text.splitlines()
    section = _section(lines, "models")
    if section is None:
        raise ConfigError("в config.yaml нет раздела models")
    at = section[1]
    while at > section[0] and not lines[at - 1].strip():
        at -= 1
    tail = [""] if at < len(lines) else []
    lines[at:at] = ["", *block.splitlines(), *tail]
    out = "\n".join(lines) + "\n"
    if set(model_blocks(out)) != {*model_blocks(text), model_id}:
        raise ConfigError("новая модель задела другие — не записано")
    return out


def remove_model(text: str, model_id: str) -> str:
    """The config without this model's block (and the blank line before it)."""
    blocks = model_blocks(text)
    if model_id not in blocks:
        raise ConfigError(f"в config.yaml нет модели {model_id}")
    lines = text.splitlines()
    if _in_groups(lines, model_id):
        raise ConfigError(f"модель {model_id} входит в группу (groups:) — уберите её оттуда в config.yaml сами")
    start, end = blocks[model_id]
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    del lines[start:end]
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if set(model_blocks(out)) != set(blocks) - {model_id}:
        raise ConfigError("удаление задело другие модели — не записано")
    return out
