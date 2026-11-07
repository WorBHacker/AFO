"""«Проверить систему» and the logs for support (stage 10.6, D37): what works and what does not, in plain words with
what to do, and one file to send to whoever helps.

The check looks at the hub (and the window's and its own crashes of the last days), llama-swap (does it answer, its
config, llama-server's program), the local models, the graphics card and its driver against the llama.cpp build, RAM
and the page file, the disks the models live on, the internet (Hugging Face, GitHub). Each line: ok (true), a warning
(null) or a problem (false), its words and advice.

The report is a zip in <data>/reports: the check, the versions, the last part of each log with secrets taken out
(hub/secret_filter.py) and llama-swap's config - never the database, keys or tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import platform
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from common import __version__, crashes, hardware
from common.disks import drives
from common.i18n import decimal, tr

from . import autostart, engines, hf, llama_config, search, secret_filter, store
from .gateway.base import PRICES_FRESH_DAYS
from .gateway.registry import PRICE_PAGES
from .setup import page_file
from .system_status import memory_sample

LOG_TAIL = 2 * 1024 * 1024  # the last 2 MB of each log go into a report
REPORT_KEEP = 5
LOW_DISK_GIB = 10.0
CRASH_DAYS = 7


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


def prices_line(models: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """The paid models' prices (stage 20.1): a provider's table checked more than PRICES_FRESH_DAYS ago, or a
    promotion that ended after it was checked - compare with the provider's page. None: nothing paid here."""
    paid = [m for m in models if m.get("locality") == "cloud" and m.get("prices")]
    dated = [m for m in paid if (m.get("capabilities") or {}).get("prices_asof")]  # OpenRouter's are live
    if not paid:
        return None
    oldest: dict[str, str] = {}
    for model in dated:
        asof = model["capabilities"]["prices_asof"]
        provider = model.get("provider") or model["id"].partition("/")[0]
        oldest[provider] = min(asof, oldest.get(provider, asof))
    problems, pages = [], []
    for provider, asof in sorted(oldest.items()):
        if (today - date.fromisoformat(asof)).days > PRICES_FRESH_DAYS:
            problems.append(tr("{provider}: цены на {date}, старше {days} дней", provider=provider, date=asof,
                               days=PRICES_FRESH_DAYS))  # fmt: skip
            pages.append(provider)
    for model in dated:
        ended = model["capabilities"].get("promo_ended")
        if ended:
            problems.append(tr("{model}: акция кончилась {date}", model=model["id"], date=ended))
            pages.append(model.get("provider") or model["id"].partition("/")[0])
    title = tr("Цены облачных моделей")
    if not problems:
        said = tr("сверены: {dates}", dates=", ".join(f"{p} — {d}" for p, d in sorted(oldest.items()))) if oldest \
            else tr("приходят от поставщика")  # fmt: skip
        return _line("prices", title, True, said)
    links = ", ".join(dict.fromkeys(PRICE_PAGES[p] for p in pages if p in PRICE_PAGES))
    advice = tr("Сверьте с ценами поставщика ({pages}) и поправьте цену в «Модели», если она другая: бюджеты и "
                "оценки идут по ней.", pages=links)  # fmt: skip
    return _line("prices", title, None, "; ".join(problems), advice)


def crashes_line(logs: Path, now: datetime) -> dict[str, Any]:
    """The window's and the hub's crashes of the last days (23.2): records their sessions did not live through, as
    the next start found them (common/crashes.py); an exception Windows caught is not one."""
    since = now - timedelta(days=CRASH_DAYS)
    found = [(when, who, what) for name, who in (("app", tr("окно")), ("hub", tr("хаб")))
             for when, what in crashes.found(logs / f"{name}-crash.log", since)]  # fmt: skip
    title = tr("Вылеты окна и хаба")
    if not found:
        return _line("crashes", title, True, tr("за {days} дней не было", days=CRASH_DAYS))
    said = "; ".join(f"{who} — {when:%d.%m %H:%M}: {what}" for when, who, what in sorted(found))
    advice = tr("Если повторяется — «Собрать журналы для поддержки» и отправьте архив тому, кто помогает: следы "
                "вылета — в нём.")  # fmt: skip
    return _line("crashes", title, None, said, advice)


def llama_swap_line(url: str, answering: bool, taken: dict[str, Any] | None) -> dict[str, Any]:
    """Does llama-swap answer - and when another program holds its port (23.4), which one and where its file is."""
    title = tr("Локальные модели (llama-swap)")
    if taken:
        said = tr("порт {port} занят: {name} (pid {pid}, {exe})", port=taken["port"], name=taken["name"],
                  pid=taken["pid"], exe=taken.get("exe") or "?")  # fmt: skip
        advice = tr("llama-swap не может слушать этот порт. Закройте {name} — это другая программа с моделью "
                    "(например, Strata или LM Studio): хаб запустит llama-swap сам.", name=taken["name"])  # fmt: skip
        return _line("llama-swap", title, False, said, advice)
    if answering:
        return _line("llama-swap", title, True, tr("отвечает на {url}", url=url))
    return _line("llama-swap", title, False, tr("не отвечает на {url}", url=url),
                 tr("Включите «Хаб сам запускает llama-swap» в «Настройки → Запуск» и перезапустите хаб; если движка "
                    "ещё нет — «Мастер первого запуска»."))  # fmt: skip


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
    lines.append(crashes_line(paths.logs, datetime.now()))

    taken = await asyncio.to_thread(autostart.port_taken, url)
    lines.append(llama_swap_line(url, taken is None and await autostart.answers(url), taken))

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
    pictures = hardware.pictures_for(hardware.dxgi_adapters(), nvidia, vulkan=hardware.vulkan_loader(),
                                     ram_gib=(memory or {}).get("ram_total_gib") or 0)  # fmt: skip
    lines.append(_line("pictures", tr("Картинки и видео"), True, pictures["why"], ""))

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
    if getattr(state, "db", None) is not None:
        prices = prices_line(await state.db.acall(store.list_models), date.today())
        if prices is not None:
            lines.append(prices)
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
