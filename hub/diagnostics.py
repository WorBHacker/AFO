"""«Проверить систему» and the logs for support (stage 10.6, D37): what works and what does not, in plain words with
what to do, and one file to send to whoever helps.

The check looks at the hub, llama-swap (does it answer, its config, llama-server's program), the local models, the
graphics card and its driver against the llama.cpp build, RAM and the page file, the disks the models live on, the
internet (Hugging Face, GitHub). Each line: ok (true), a warning (null) or a problem (false), its words and advice.

The report is a zip in <data>/reports: the check, the versions, the last part of each log with secrets taken out
(hub/secret_filter.py) and llama-swap's config - never the database, keys or tokens.
"""

from __future__ import annotations

import contextlib
import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from common import __version__, hardware
from common.disks import drives
from common.i18n import decimal, tr

from . import autostart, engines, hf, llama_config, search, secret_filter
from .setup import page_file
from .system_status import memory_sample

LOG_TAIL = 2 * 1024 * 1024  # the last 2 MB of each log go into a report
REPORT_KEEP = 5
LOW_DISK_GIB = 10.0


def _n(value: float) -> str:
    """A number as the language writes it: 15,9 in Russian, 15.9 in English."""
    return decimal(f"{value:g}")


def _line(key: str, title: str, ok: bool | None, text: str, advice: str = "") -> dict[str, Any]:
    return {"id": key, "title": title, "ok": ok, "text": text, "advice": advice}


def _server_program(text: str) -> Path | None:
    """llama-server.exe as the config names it (its macro or the program of a model)."""
    with contextlib.suppress(llama_config.ConfigError):
        command = llama_config.server_command(text)
        name = command.strip("${}")
        value = llama_config.macros(text).get(name, command) if command.startswith("${") else command
        return Path(value.strip("'\""))
    return None


def _build_of(program: Path | None) -> str:
    """Which llama.cpp build a llama-server is: by the libraries beside it."""
    if program is None or not program.parent.is_dir():
        return ""
    names = {p.name.lower() for p in program.parent.iterdir()}
    if any(n.startswith("ggml-cuda") for n in names):
        return "cuda"
    if any(n.startswith("ggml-vulkan") for n in names):
        return "vulkan"
    return "cpu" if names else ""


def model_files(text: str) -> list[Path]:
    out = []
    for model_id in llama_config.model_blocks(text):
        with contextlib.suppress(llama_config.ConfigError):
            view = llama_config.model_view(text, model_id)
            if view.get("file"):
                out.append(Path(view["file"]))
    return out


async def check(state: Any) -> list[dict[str, Any]]:
    """The lines of «Проверить систему»."""
    lines = []
    paths = state.settings.paths
    url = state.settings.llama_swap_url
    lines.append(_line("hub", tr("Хаб"), True, tr("AFO {version} работает, данные в {folder}", version=__version__,
                                                  folder=paths.root)))  # fmt: skip

    answering = await autostart.answers(url)
    lines.append(_line("llama-swap", tr("Локальные модели (llama-swap)"), answering,
                       tr("отвечает на {url}", url=url) if answering else tr("не отвечает на {url}", url=url),
                       "" if answering else tr("Включите «Хаб сам запускает llama-swap» в «Настройки → Запуск» и "
                       "перезапустите хаб; если движка ещё нет — «Мастер первого запуска».")))  # fmt: skip

    config = llama_config.config_path()
    text = ""
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        advice = tr("Укажите его в «Настройки → Запуск» или поставьте движок в «Мастере первого запуска».")
        lines.append(_line("config", tr("Конфиг llama-swap"), False, tr("нет файла {path}", path=config), advice))
    program = _server_program(text) if text else None
    if text:
        found = program is not None and program.is_file()
        count = len(llama_config.model_blocks(text)) if "models:" in text else 0
        said = tr("{config}: моделей — {count}; llama-server — {program}", config=config, count=count,
                  program=program or tr("не указан"))  # fmt: skip
        lines.append(_line("config", tr("Конфиг llama-swap"), found, said + ("" if found else tr(" (файла нет)")),
                           "" if found else tr("Программа llama-server не найдена: обновите движок в «Мастере "
                           "первого запуска».")))  # fmt: skip

    cards = hardware.adapters()
    nvidia = hardware.nvidia_driver()
    wanted = hardware.build_for(cards, nvidia)
    build = _build_of(program)
    names = ", ".join(tr("{name} ({gib} ГБ)", name=c["name"], gib=_n(c["vram_gib"])) for c in cards) or \
        tr("видеокарта не найдена")  # fmt: skip
    driver = tr("; драйвер NVIDIA {version}, CUDA {cuda}", version=nvidia["version"],
                cuda=f"{nvidia['cuda'] // 1000}.{nvidia['cuda'] % 1000 // 10}") if nvidia else ""  # fmt: skip
    mismatch = build == "cpu" and wanted["build"] != "cpu"
    advice = tr("llama.cpp здесь — сборка для процессора, а видеокарта есть: поставьте сборку для неё в «Мастере "
                "первого запуска» — будет в разы быстрее.") if mismatch else ""  # fmt: skip
    lines.append(_line("gpu", tr("Видеокарта"), None if mismatch else True, names + driver, advice))

    memory = memory_sample()
    page = page_file(memory)
    if memory:
        said = tr("ОЗУ {total} ГБ, занято {used} ГБ; файл подкачки ≈ {page} ГБ", total=_n(memory["ram_total_gib"]),
                  used=_n(memory["ram_used_gib"]), page=_n(page["gib"]))  # fmt: skip
        lines.append(_line("memory", tr("Память"), None if page["advice"] else True, said, page["advice"]))

    files = model_files(text) if text else []
    by_drive = {d["letter"]: d for d in drives()}
    for letter in sorted({f.drive[:1].upper() for f in files if f.drive}):
        disk = by_drive.get(letter)
        if disk is None:
            continue
        slow = disk["kind"] in ("hdd", "usb")
        low = disk["free_gib"] < LOW_DISK_GIB
        advice = []
        if slow:
            advice.append(tr("Модели на жёстком диске загружаются в разы медленнее — перенесите их на SSD или NVMe."))
        if low:
            advice.append(tr("На диске мало места: новая модель может не поместиться."))
        said = tr("{kind}, свободно {free} ГБ — тут модели", kind=disk["words"], free=_n(disk["free_gib"]))
        title = tr("Диск {letter}:", letter=letter)
        lines.append(_line(f"disk-{letter}", title, None if advice else True, said, " ".join(advice)))
    missing = [f for f in files if not f.exists()]
    if missing:
        advice = tr("Модель удалили или перенесли: поправьте путь в config.yaml или удалите её запись.")
        said = tr("нет файлов: {files}", files=", ".join(str(f) for f in missing[:3]))
        lines.append(_line("files", tr("Файлы моделей"), False, said, advice))

    rg = search.ripgrep()
    trouble = getattr(getattr(state, "ripgrep", None), "error", "")  # the hub's last try to fetch it (hub/ripgrep.py)
    advice = ""
    if not rg:
        advice = tr("Хаб скачает ripgrep с GitHub сам, когда будет связь (сейчас: {trouble}).", trouble=trouble) \
            if trouble else tr("Хаб скачает ripgrep с GitHub сам, когда будет связь.")  # fmt: skip
    said = f"ripgrep: {rg}" if rg else tr("ripgrep нет — поиск идёт медленнее, обходом файлов")
    lines.append(_line("ripgrep", tr("Поиск по проектам"), True if rg else None, said, advice))
    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as http:
        for key, site in (("hf", hf.base_url()), ("github", engines.github_api())):
            try:
                ok = (await http.head(site)).status_code < 500
            except httpx.HTTPError:
                ok = False
            lines.append(_line(f"net-{key}", tr("Связь с {site}", site=site.split("//")[1]), ok,
                               tr("есть") if ok else tr("нет"),
                               "" if ok else tr("Без неё не скачать модели и движок; работающие локальные модели "
                               "это не трогает.")))  # fmt: skip
    return lines


def _tail(path: Path) -> str:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - LOG_TAIL))
        data = f.read().decode("utf-8", "replace")
    return data if size <= LOG_TAIL else data.split("\n", 1)[-1]


def report(paths: Any, lines: list[dict[str, Any]]) -> Path:
    """The zip for support (blocking: in a thread): the check, versions, the logs' last part without secrets, the
    llama-swap config. The last few reports are kept."""
    folder = paths.root / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"afo-report-{datetime.now():%Y-%m-%d-%H%M%S}.zip"
    versions = {"afo": __version__, "python": sys.version, "windows": platform.platform(),
                "machine": platform.machine(), "made": datetime.now().isoformat(timespec="seconds")}  # fmt: skip
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("check.json", json.dumps(lines, ensure_ascii=False, indent=2))
        zf.writestr("versions.json", json.dumps(versions, ensure_ascii=False, indent=2))
        for log in sorted(paths.logs.glob("*.log")):
            with contextlib.suppress(OSError):
                zf.writestr(f"logs/{log.name}", secret_filter.clean(_tail(log)))
        config = llama_config.config_path()
        with contextlib.suppress(OSError):
            zf.writestr("llama-swap/config.yaml", secret_filter.clean(config.read_text(encoding="utf-8")))
        swap_log = config.parent / "llama-swap.log"
        with contextlib.suppress(OSError):
            zf.writestr("llama-swap/llama-swap.log", secret_filter.clean(_tail(swap_log)))
    made = [*folder.glob("afo-report-*.zip"), *folder.glob("localchat-report-*.zip")]  # before 10.4b too
    for old in sorted(made, key=lambda p: p.name.split("-report-")[-1])[:-REPORT_KEEP]:
        old.unlink(missing_ok=True)
    return target
