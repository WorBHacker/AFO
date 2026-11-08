"""Installing local models in the window (stage 10.1, D37): the sheet «Установка моделей». The curated list with the
variant this computer can run best, a search of Hugging Face, one repository's variants judged against the memory,
the drive the files go to, and the installs - their progress, pause, «Продолжить», the trial run, removal.

The hub does the work (hub/model_install.py); this only shows it and asks. Words are plain: «помещается в
видеопамять — быстро», not layers and quantization; the technical name of a variant is beside it for those who know.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.i18n import N_, decimal, language, tr
from common.plain_errors import explain

if TYPE_CHECKING:
    from .controller import Controller

PURPOSES = [("", N_("Все")), ("chat", N_("Разговоры")), ("code", N_("Код")), ("vision", N_("Понимать картинки")),
            ("image", N_("Рисовать"))]  # fmt: skip
PICTURES = "pictures:"  # a picture model's row (29.3): no repository of its own - a set of files from several
TONES = {"gpu": "ok", "split": "warn", "split_moe": "warn", "cpu": "warn", "no": "error"}
TONES_PICTURES = {"fast": "ok", "slow": "warn", "no": "error"}  # no: a video model where videos are the cloud's
STATUS = {"queued": N_("в очереди"), "downloading": N_("загружается"), "paused": N_("на паузе"),
          "configuring": N_("записываю в llama-swap"), "trial": N_("пробный запуск"), "done": N_("установлена"),
          "failed": N_("не вышло"), "cancelled": N_("отменена"), "removed": N_("удалена")}  # fmt: skip
ACTIONS = {
    "queued": [("pause", N_("Пауза"), "plain"), ("cancel", N_("Отменить"), "danger")],
    "downloading": [("pause", N_("Пауза"), "plain"), ("cancel", N_("Отменить"), "danger")],
    "paused": [("resume", N_("Продолжить"), "primary"), ("cancel", N_("Отменить"), "danger")],
    "failed": [("resume", N_("Повторить"), "primary"), ("forget", N_("Убрать"), "danger")],
    "cancelled": [("forget", N_("Убрать"), "plain")],
    "done": [("remove", N_("Удалить"), "danger")],
}
LIVE = ("queued", "downloading", "configuring", "trial")
WHERE = {"gpu": N_("целиком в видеопамяти"), "split": N_("частью в ОЗУ"),
         "cpu": N_("на процессоре — видеокарта здесь не нарисовала")}  # fmt: skip
PICKS = {"fast": N_("быстрее"), "quality": N_("качественнее")}  # «Быстрее ↔ Качественнее» beside the recommended one
# the search's filters (32.6; the hub's /v1/install/search): what for - the curated list's purposes but pictures
SEARCH_TASKS = [p for p in PURPOSES if p[0] != "image"]
SEARCH_SIZES = [("", N_("Любой")), ("fits", ""), ("small", N_("до 4 млрд")), ("medium", N_("4–15 млрд")),
                ("large", N_("15–40 млрд")), ("huge", N_("больше 40 млрд"))]  # fmt: skip
SEARCH_SORTS = [("downloads", N_("Популярные")), ("trending", N_("В тренде")), ("likes", N_("Больше ♥")),
                ("updated", N_("Недавно обновлённые"))]  # fmt: skip


def status_words(status: str) -> str:
    return tr(STATUS[status]) if status in STATUS else status


def gb(size: float, *, of_bytes: bool = True) -> str:
    """A size as people read it: «8,1 ГБ», a small one «52 МБ» (decimal units, as Hugging Face shows them)."""
    value = size / 1e9 if of_bytes else size
    if value < 0.1:
        return tr("{size:.0f} МБ", size=max(value * 1000, 1))
    return tr("{size} ГБ", size=decimal(f"{value:.1f}"))


def count(n: int) -> str:
    if n >= 1_000_000:
        return tr("{count} млн", count=decimal(f"{n / 1e6:.1f}"))
    if n >= 1000:
        return tr("{count:.0f} тыс.", count=n / 1e3)
    return str(n)


def speed_text(speed: float) -> str:
    """Bytes a second as «21,7 МБ/с»."""
    return tr("{speed} МБ/с", speed=decimal(f"{speed / 1e6:.1f}"))


def left_text(seconds: float) -> str:
    """The time an install has left: «ещё ≈ 3 мин», «ещё ≈ 25 с»."""
    if seconds >= 60:
        return tr("ещё ≈ {minutes} мин", minutes=max(1, round(seconds / 60)))
    return tr("ещё ≈ {seconds} с", seconds=max(1, round(seconds)))


def free_text(gib: float) -> str:
    return tr(" · свободно {size}", size=gb(gib, of_bytes=False))


def about(words: dict[str, str]) -> str:
    """A curated model's description in the window's language (the catalog has it in Russian and English)."""
    return words.get(language()) or words.get("ru", "")


def system_text(system: dict[str, Any]) -> str:
    space = system.get("space") or {}
    if space.get("vram_total_gib"):
        parts = [tr("{gpu}: для новой модели ≈ {size} видеопамяти", gpu=space.get("gpu") or tr("Видеокарта"),
                    size=gb(space["vram_gib"], of_bytes=False))]  # fmt: skip
    else:
        parts = [tr("Видеокарта NVIDIA не найдена — модели будут работать в ОЗУ, медленно")]
    parts.append(tr("ОЗУ свободно ≈ {size}", size=gb(space.get("ram_gib") or 0, of_bytes=False)))
    if space.get("loaded"):
        parts.append(tr("сейчас загружена «{models}» — на время работы новой она выгрузится",
                        models=", ".join(space["loaded"])))  # fmt: skip
    return " · ".join(parts)


def verdict_of(variant: dict[str, Any] | None) -> tuple[str, str]:
    if not variant:
        return "", ""
    return variant.get("words", ""), TONES.get(variant.get("verdict", ""), "")


def catalog_rows(models: list[dict[str, Any]], purpose: str = "") -> list[dict[str, Any]]:
    out = []
    for m in models:
        if purpose and purpose not in m.get("purposes", []):
            continue
        best = m.get("recommended")
        installed = bool(m.get("installed"))
        if best:
            words, tone = verdict_of(best)
            offer = f"{best['quant']} · {gb(best['size'])}"
        elif m.get("smallest"):
            words = tr("не поместится: даже самый маленький вариант — {size}", size=gb(m["smallest"]["size"]))
            tone = "error"
            offer = ""
        else:
            words, tone, offer = m.get("error", ""), "error", ""
        out.append({"id": m["id"], "repo": m["repo"], "quant": best["quant"] if best else "", "name": m["name"],
                    "about": about(m.get("about") or {}),
                    "purposes": " · ".join(m.get("purpose_words") or []), "license": m.get("license", ""),
                    "gated": bool(m.get("gated")), "words": words, "tone": tone, "offer": offer,
                    "installed": installed, "variants": True,
                    "canInstall": bool(best) and not m.get("installed_recommended")})  # fmt: skip
    return out


def picture_rows(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The picture models (29.3, D55) as the catalog's rows: one set each, nothing to choose between."""
    out = []
    for m in models:
        installed = bool(m.get("installed"))
        words, tone = (m["error"], "error") if m.get("error") else (m.get("words", ""), TONES_PICTURES[m["verdict"]])
        out.append({"id": m["id"], "repo": PICTURES + m["id"], "quant": "", "name": m["name"],
                    "about": about(m.get("about") or {}), "purposes": " · ".join(m.get("purpose_words") or []),
                    "license": m.get("license", ""), "gated": False, "words": words, "tone": tone,
                    "offer": gb(m["size"]) if m.get("size") else "", "installed": installed, "variants": False,
                    "canInstall": not installed and not m.get("error") and m["verdict"] != "no"})  # fmt: skip
    return out


def billions(params: float) -> str:
    """A parameter count in billions as people read it: «27,3», a small one «0,8»."""
    return decimal(f"{params / 1e9:.1f}")


def size_rows(system: dict[str, Any]) -> list[dict[str, Any]]:
    """The search's sizes; «fits» says where and how much: «Поместится в видеопамять (до 22 млрд)»."""
    where = tr("Поместится в видеопамять") if (system.get("space") or {}).get("vram_total_gib") else \
        tr("Поместится в ОЗУ")  # fmt: skip
    fits = (system.get("fits_params") or 0) / 1e9
    if fits:
        most = decimal(f"{fits:.0f}" if fits >= 10 else f"{fits:.1f}")
        where = tr("{where} (до {count} млрд)", where=where, count=most)
    return [{"id": sid, "name": where if sid == "fits" else tr(name)} for sid, name in SEARCH_SIZES]


def search_rows(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in models:
        note = [f"↓ {count(m.get('downloads') or 0)}", f"♥ {count(m.get('likes') or 0)}"]
        if m.get("params"):
            note.append(tr("{count} млрд параметров", count=billions(m["params"])))
        if m.get("license"):
            note.append(tr("лицензия {license}", license=m["license"]))
        if m.get("updated"):
            note.append(tr("обновлена {date}", date=m["updated"]))
        badges = ([tr("снятые ограничения")] if m.get("unrestricted") else []) + (
            [tr("лицензия на сайте")] if m.get("gated") else [])  # fmt: skip
        out.append({"repo": m["repo"], "note": " · ".join(note), "badges": badges})
    return out


def variant_rows(details: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for v in details.get("variants") or []:
        words, tone = verdict_of(v)
        bits = tr("≈ {bits} бита на вес", bits=decimal(f"{v['bits']:.1f}")) if v.get("bits") else ""
        out.append({"quant": v["quant"], "size": gb(v["size"]), "bits": bits, "words": words, "tone": tone,
                    "recommended": bool(v.get("recommended")), "parts": len(v.get("files") or []),
                    "pick": tr(PICKS[v["pick"]]) if v.get("pick") in PICKS else ""})  # fmt: skip
    return out


def drive_rows(drives: list[dict[str, Any]], chosen: str, home: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Where a model may go: LocalChat's own `model` folder first (the default, 2026-10-28), then each drive."""
    out = []
    if home and home.get("folder"):
        free = free_text(home["free_gib"]) if home.get("free_gib") is not None else ""
        out.append({"id": "home", "selected": chosen in ("", "home"), "slow": home.get("kind") in ("hdd", "usb"),
                    "name": tr("Папка AFO") + (f" ({home['letter']}: {home['words']})" if home.get("letter")
                                                     else "") + free})  # fmt: skip
    return out + [{"id": d["letter"], "selected": d["letter"] == chosen, "slow": d.get("kind") in ("hdd", "usb"),
                   "name": f"{d['letter']}: {d.get('words', '')}" + free_text(d["free_gib"])}
                  for d in drives]  # fmt: skip


def target_folder(system: dict[str, Any], chosen: str, own: str, repo: str) -> str:
    """The folder the model's files will land in, as the person chose: their own folder, a drive, LocalChat's."""
    if own:
        base = own.rstrip("\\/")
    elif chosen and chosen != "home":
        base = next((d.get("folder", "") for d in system.get("drives") or [] if d["letter"] == chosen), "")
    else:
        base = (system.get("home") or {}).get("folder") or system.get("folder") or ""
    if not base:
        return ""
    return base + ("\\" + repo.replace("/", "\\") if repo else "")


def progress_text(job: dict[str, Any]) -> str:
    total, done = job.get("total_bytes") or 0, job.get("done_bytes") or 0
    if job.get("status") not in ("downloading", "paused", "queued") or not total:
        return ""
    parts = [tr("{done} из {total}", done=gb(done), total=gb(total))]
    speed = job.get("speed") or 0
    if job.get("status") == "downloading" and speed > 0:
        parts.append(speed_text(speed))
        parts.append(left_text((total - done) / speed))
    return " · ".join(parts)


def percent_of(job: dict[str, Any]) -> float:
    total = job.get("total_bytes") or 0
    return float(job.get("percent") or (round(100 * (job.get("done_bytes") or 0) / total, 1) if total else 0.0))


def job_state(job: dict[str, Any] | None) -> dict[str, Any]:
    """An install where its model's «Установить» was (2026-10-28, the user's word): pause or go on, ✕ to cancel (or
    to take a failed one away), and its progress in the line under the name."""
    if not job:
        return {}
    actions = {a: tr(label) for a, label, _kind in ACTIONS.get(job["status"], [])}
    primary = next((a for a in ("pause", "resume") if a in actions), "")
    pct = percent_of(job)
    progress = progress_text(job)
    words = status_words(job["status"])
    if job["status"] == "downloading" and progress:
        line = f"{pct:.0f} % · {progress}"
    elif job["status"] == "paused":
        line = f"{words} · {pct:.0f} %" + (f" · {progress}" if progress else "")
    elif job["status"] == "failed":
        line = tr("не вышло: {error}", error=job.get("error") or "").rstrip(": ")
    else:
        line = words
    return {"id": job["id"], "status": job["status"], "live": job["status"] in (*LIVE, "paused"), "percent": pct,
            "line": line, "primary": primary, "primaryLabel": actions.get(primary, ""),
            "dismiss": "cancel" if "cancel" in actions else "forget" if "forget" in actions else "",
            "installed": job["status"] == "done"}  # fmt: skip


def status_line(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """The status bar's word on the install going on: its name, percent, speed, time left; '' - none goes on."""
    live = [j for j in jobs if j["status"] in LIVE]
    if not live:
        return {"name": "", "text": "", "short": "", "percent": 0.0, "tip": ""}
    job = live[0]
    pct = percent_of(job)
    if job["status"] == "downloading":
        speed = job.get("speed") or 0
        bits = [f"{pct:.0f} %"]
        if speed > 0:
            left = ((job.get("total_bytes") or 0) - (job.get("done_bytes") or 0)) / speed
            bits += [speed_text(speed), left_text(left)]
        text = " · ".join(bits)
    else:
        text = status_words(job["status"])
    more = tr(" (и ещё {count})", count=len(live) - 1) if len(live) > 1 else ""
    tip = tr("Установка «{name}»: {state}. Нажмите — «Установки»", name=job["name"],
             state=progress_text(job) or status_words(job["status"]))  # fmt: skip
    short = f"{pct:.0f} %" if job["status"] == "downloading" else text  # a narrow status bar
    return {"name": f"{job['name']}{more}", "text": text, "short": short, "percent": pct / 100, "tip": tip}


def with_hint(error: str) -> str:
    """An install's error with what to do, when it is one of the usual (10.6)."""
    plain = explain(error) if error else None
    return f"{error}\n{plain[1]}" if plain and plain[1] not in error else error


def trial_text(job: dict[str, Any]) -> str:
    trial = job.get("trial") or {}
    if trial.get("skipped"):
        return trial["skipped"]
    if job.get("status") == "done" and trial.get("seconds") is not None:
        parts = [tr("загрузилась и ответила за {seconds} с", seconds=decimal(str(trial["seconds"])))]
        if trial.get("tok_s"):
            parts.append(tr("{speed} ток/с", speed=round(trial["tok_s"])))
        if trial.get("where") in WHERE:
            parts.append(tr(WHERE[trial["where"]]))
        if len(trial.get("tries") or []) > 1:
            parts.append(tr("с {count}-й попытки — настройки уменьшены", count=len(trial["tries"])))
        return " · ".join(parts)
    return ""


def local_rows(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The models found on the drives, as «На этом компьютере» lists them (15.1)."""
    out = []
    for m in found.get("models") or []:
        words, tone = verdict_of(m)
        note = [gb(m["size"])]
        if m.get("quant"):
            note.append(m["quant"])
        if m.get("projector"):
            note.append(tr("понимает картинки"))
        if m.get("source") == "ollama":
            note.append("Ollama")
        out.append({"name": m["name"], "path": m["path"], "projector": m.get("projector") or "",
                    "note": " · ".join(note), "words": words, "tone": tone,
                    "inConfig": m.get("in_config") or ""})  # fmt: skip
    return out


def scan_text(found: dict[str, Any]) -> str:
    """What the search on the drives is doing, or what it found."""
    job = found.get("job") or {}
    status = job.get("status")
    if status == "scanning":
        return tr("Ищу на дисках: {drive} — просмотрено папок: {folders}, найдено файлов: {files}…",
                  drive=job.get("drive") or "", folders=job.get("folders", 0), files=job.get("files", 0))  # fmt: skip
    if status == "failed":
        return tr("Поиск не удался: {error}", error=job.get("error", ""))
    if status == "cancelled":
        return tr("Поиск остановлен — список прежний.")
    if found.get("at"):
        return tr("Найдено моделей: {count}. Список хранится до следующего поиска.",
                  count=len(found.get("models") or []))  # fmt: skip
    return tr("Модели, что уже лежат на ваших дисках: LM Studio, Ollama, скачанные вручную. Поиск идёт по всем дискам "
              "в фоне и может занять несколько минут; модель добавляется в llama-swap прямо оттуда, где лежит, без "
              "копии.")  # fmt: skip


def job_rows(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": j["id"], "name": j["name"], "modelId": j["model_id"], "repo": j["repo"], "status": j["status"],
             "ownFiles": bool(j.get("own_files", 1)),
             "statusWords": status_words(j["status"]), "percent": j.get("percent") or 0.0,
             "progress": progress_text(j), "error": with_hint(j.get("error") or ""), "trial": trial_text(j),
             "live": j["status"] in ("queued", "downloading", "configuring", "trial"),
             "actions": [{"id": a, "label": tr(label), "kind": kind}
                         for a, label, kind in ACTIONS.get(j["status"], [])]}
            for j in jobs if j["status"] != "removed"]  # fmt: skip


class InstallView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._system: dict[str, Any] = {}
        self._catalog: list[dict[str, Any]] = []
        self._catalog_loading = False
        self._catalog_error = ""
        self._pictures: list[dict[str, Any]] = []  # the picture models (29.3), asked for when «Рисовать» is picked
        self._pictures_loading = False
        self._purpose = ""
        self._tab = "catalog"
        self._query = ""
        self._task, self._size, self._sort, self._plain = "", "", "downloads", False  # the search's filters (32.6)
        self._asked: tuple | None = None  # the last search sent: an older answer is not shown over it
        self._found: list[dict[str, Any]] = []
        self._searching = False
        self._search_error = ""
        self._repo: dict[str, Any] = {}
        self._repo_name = ""
        self._repo_loading = False
        self._repo_error = ""
        self._drive = ""
        self._folder = ""
        self._projector = True
        self._jobs: list[dict[str, Any]] = []
        self._jobs_loading = False
        # what events said while a load of the list was on its way (id -> the job, None: forgotten): newer than what
        # that load read, so it goes over its answer - else a stale «загружается» could outlive «установлена»
        self._heard: dict[str, dict[str, Any] | None] = {}
        self._busy = False
        self._local: dict[str, Any] = {}  # the models found on the drives (15.1)

    # ================= for QML =================

    @Property(str, notify=changed)
    def systemText(self) -> str:
        return system_text(self._system) if self._system else tr("Смотрю, что умеет этот компьютер…")

    @Property(str, notify=changed)
    def problem(self) -> str:
        return self._system.get("problem", "")

    @Property(str, notify=changed)
    def tab(self) -> str:
        return self._tab

    @Property(list, notify=changed)
    def purposes(self) -> list:
        return [{"id": pid, "name": tr(name)} for pid, name in PURPOSES]

    @Property(str, notify=changed)
    def purpose(self) -> str:
        return self._purpose

    @Property(list, notify=changed)
    def catalog(self) -> list:
        if self._purpose == "image":
            return [{**r, "job": job_state(self._job_for_set(r["id"]))} for r in picture_rows(self._pictures)]
        return [{**r, "job": job_state(self._job_for(r["repo"], r["quant"]))} if r["quant"] else {**r, "job": {}}
                for r in catalog_rows(self._catalog, self._purpose)]  # fmt: skip

    def _job_for_set(self, set_id: str) -> dict[str, Any] | None:
        """A picture model's install still on the list - the newest."""
        found = [j for j in self._jobs if (j.get("plan") or {}).get("set") == set_id
                 and j["status"] not in ("cancelled", "removed")]  # fmt: skip
        return found[0] if found else None

    @Property(bool, notify=changed)
    def catalogLoading(self) -> bool:
        return self._catalog_loading or (self._purpose == "image" and self._pictures_loading)

    @Property(str, notify=changed)
    def catalogError(self) -> str:
        return self._catalog_error

    @Property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @Property(list, notify=changed)
    def found(self) -> list:
        return search_rows(self._found)

    @Property(bool, notify=changed)
    def searching(self) -> bool:
        return self._searching

    @Property(bool, notify=changed)
    def nothingFound(self) -> bool:
        """A search came back empty: the sheet says so (with filters it may well)."""
        return self._asked is not None and not self._searching and not self._search_error and not self._found

    @Property(list, notify=changed)
    def searchTasks(self) -> list:
        return [{"id": tid, "name": tr(name)} for tid, name in SEARCH_TASKS]

    @Property(list, notify=changed)
    def searchSizes(self) -> list:
        return size_rows(self._system)

    @Property(list, notify=changed)
    def searchSorts(self) -> list:
        return [{"id": sid, "name": tr(name)} for sid, name in SEARCH_SORTS]

    @Property(str, notify=changed)
    def searchTask(self) -> str:
        return self._task

    @Property(str, notify=changed)
    def searchSize(self) -> str:
        return self._size

    @Property(str, notify=changed)
    def searchSort(self) -> str:
        return self._sort

    @Property(bool, notify=changed)
    def searchPlain(self) -> bool:
        return self._plain

    @Property(str, notify=changed)
    def searchError(self) -> str:
        return self._search_error

    @Property(str, notify=changed)
    def repoName(self) -> str:
        return self._repo_name

    @Property(bool, notify=changed)
    def repoLoading(self) -> bool:
        return self._repo_loading

    @Property(str, notify=changed)
    def repoError(self) -> str:
        return self._repo_error

    @Property(dict, notify=changed)
    def repo(self) -> dict:
        r = self._repo
        if not r:
            return {}
        notes = [tr("лицензия {license}", license=r["license"]) if r.get("license") else tr("лицензия не указана")]
        if r.get("params"):
            notes.append(tr("{count} млрд параметров", count=billions(r["params"])))
        if r.get("context_trained"):
            notes.append(tr("контекст до {size}K", size=r["context_trained"] // 1024))
        if r.get("moe"):
            notes.append(tr("из «экспертов»"))
        lens = r.get("projector")
        return {"repo": r.get("repo", ""), "page": r.get("page", ""), "notes": " · ".join(notes),
                "unrestricted": bool(r.get("unrestricted")), "gated": bool(r.get("gated")),
                "projector": bool(lens),
                "projectorText": tr("Понимать картинки (+{size})", size=gb(lens["size"])) if lens else ""}  # fmt: skip

    @Property(list, notify=changed)
    def variants(self) -> list:
        return [{**v, "job": job_state(self._job_for(self._repo_name, v["quant"]))} for v in variant_rows(self._repo)]

    def _job_for(self, repo: str, quant: str) -> dict[str, Any] | None:
        """The install of this variant still on the list (not cancelled, not removed) - the newest."""
        found = [j for j in self._jobs if j["repo"] == repo and j.get("quant") == quant
                 and j["status"] not in ("cancelled", "removed")]  # fmt: skip
        return found[0] if found else None  # the list is newest first

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict:
        """The status bar: the install going on (text, 0-1, tooltip)."""
        return status_line(self._jobs)

    @Slot()
    def showJobs(self) -> None:
        """The status bar clicked: the sheet open on «Установки»."""
        self._repo_name, self._repo, self._repo_error, self._tab = "", {}, "", "jobs"
        self.changed.emit()
        self.app.openUi("install-models")

    def load_jobs(self) -> None:
        """At connecting: an install going on shows in the status bar before the sheet is ever opened."""
        if self.app.client is not None:
            self._load_jobs()

    @Property(list, notify=changed)
    def drives(self) -> list:
        return drive_rows(self._system.get("drives") or [], self._drive, self._system.get("home"))

    @Property(str, notify=changed)
    def target(self) -> str:
        """Where the open model will be saved: the line under «Куда сохранить»."""
        return target_folder(self._system, self._drive, self._folder, self._repo_name)

    @Property(str, notify=changed)
    def folder(self) -> str:
        return self._folder

    @Property(bool, notify=changed)
    def projector(self) -> bool:
        return self._projector

    @Property(list, notify=changed)
    def jobs(self) -> list:
        return job_rows(self._jobs)

    @Property(int, notify=changed)
    def activeCount(self) -> int:
        return sum(1 for j in self._jobs if j["status"] in ("queued", "downloading", "configuring", "trial"))

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def system(data: dict[str, Any]) -> None:
            self._system = data  # no drive chosen: LocalChat's own `model` folder (2026-10-28, the user's word)
            self.changed.emit()

        self.app._call(lambda: self.app.client.install_system(), system, lambda _e: None)
        self._load_jobs()
        if not self._catalog and not self._catalog_loading:
            self._load_catalog()

    def _load_jobs(self) -> None:
        def show(data: dict[str, Any]) -> None:
            heard, self._heard = self._heard, {}
            jobs = [{**j, **(heard.get(j["id"]) or {})} for j in data.get("jobs", []) if heard.get(j["id"], j)]
            known = {j["id"] for j in jobs}
            fresh = [j for i, j in heard.items() if j and i not in known]  # began after the load read the list
            self._jobs, self._jobs_loading = fresh + jobs, False
            self.changed.emit()

        def failed(_error: Exception) -> None:
            self._jobs_loading, self._heard = False, {}

        self._jobs_loading, self._heard = True, {}
        self.app._call(lambda: self.app.client.install_jobs(), show, failed)

    def _load_catalog(self) -> None:
        self._catalog_loading, self._catalog_error = True, ""
        self.changed.emit()

        def show(data: dict[str, Any]) -> None:
            self._catalog, self._catalog_loading = data.get("models", []), False
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._catalog_loading, self._catalog_error = False, tr("Каталог не загрузился: {error}", error=error)
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_catalog(), show, failed)

    @Slot(str)
    def setTab(self, tab: str) -> None:
        self._tab = tab
        self.changed.emit()
        if tab == "local" and not self._local:
            self.loadLocal()

    # --- the models already on this computer (15.1) ---

    @Property(list, notify=changed)
    def localModels(self) -> list:
        return local_rows(self._local)

    @Property(str, notify=changed)
    def scanText(self) -> str:
        return scan_text(self._local)

    @Property(bool, notify=changed)
    def scanning(self) -> bool:
        return (self._local.get("job") or {}).get("status") == "scanning"

    def _set_local(self, found: dict[str, Any]) -> None:
        self._local = found
        self.changed.emit()

    @Slot()
    def loadLocal(self) -> None:
        if self.app.client is not None:
            self.app._call(lambda: self.app.client.install_found(), self._set_local, lambda _e: None)

    @Slot()
    def scanLocal(self) -> None:
        self.app.call_or_fail(lambda: self.app.client.install_scan(), self._set_local,
                              lambda e: self.app.show_error(tr("Не начать поиск: {error}", error=e)))  # fmt: skip

    @Slot()
    def stopScan(self) -> None:
        self.app.call_or_fail(lambda: self.app.client.install_scan_cancel(), self._set_local, lambda _e: None)

    @Slot(str, str, str)
    def addLocal(self, path: str, projector: str, name: str) -> None:
        """«Добавить»: the model where it lies; `name` - the list's, its id in the config comes from it (34.2: an
        Ollama model's file is named by its sha256)."""

        def done(result: dict[str, Any]) -> None:
            job = result.get("job") or {}
            self._upsert(job)
            self.app.flash(tr("Добавляю «{name}» — пробный запуск на вкладке «Установки»", name=job.get("name", "")))
            self.loadLocal()

        self.app.call_or_fail(lambda: self.app.client.install_local(path, projector, name), done,
                              lambda e: self.app.show_error(tr("Не добавить: {error}", error=e)))  # fmt: skip

    @Slot(str)
    def addFile(self, url: str) -> None:
        """A model file chosen in the dialog (any GGUF on this computer): no name - the hub takes the file's."""
        self.addLocal(QUrl(url).toLocalFile() if url.startswith("file:") else url, "", "")

    @Slot(str)
    def setPurpose(self, purpose: str) -> None:
        self._purpose = purpose
        self.changed.emit()
        if purpose == "image" and not self._pictures and not self._pictures_loading:
            self._load_pictures()

    def _load_pictures(self) -> None:
        self._pictures_loading, self._catalog_error = True, ""
        self.changed.emit()

        def show(data: dict[str, Any]) -> None:
            self._pictures, self._pictures_loading = data.get("models", []), False
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._pictures_loading, self._catalog_error = False, tr("Каталог не загрузился: {error}", error=error)
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_pictures(), show, failed)

    @Slot(str)
    def search(self, query: str) -> None:
        """The words sent (Enter); none - the most popular by the filters (32.6)."""
        self._query = query.strip()
        self._search()

    @Slot(str)
    def setQuery(self, query: str) -> None:
        """The words as they are typed, not sent: a filter picked searches with them."""
        self._query = query.strip()

    @Slot(str)
    def setSearchTask(self, task: str) -> None:
        self._task = task
        self._search()

    @Slot(str)
    def setSearchSize(self, size: str) -> None:
        self._size = size
        self._search()

    @Slot(str)
    def setSearchSort(self, sort: str) -> None:
        self._sort = sort
        self._search()

    @Slot(bool)
    def setSearchPlain(self, plain: bool) -> None:
        self._plain = plain
        self._search()

    def _search(self) -> None:
        asked = self._asked = (self._query, self._task, self._size, self._sort, self._plain)
        self._searching, self._search_error = True, ""
        self.changed.emit()
        query, task, size, sort, plain = asked

        def show(data: dict[str, Any]) -> None:
            if asked == self._asked:
                self._found, self._searching = data.get("models", []), False
                self.changed.emit()

        def failed(error: Exception) -> None:
            if asked == self._asked:
                self._searching, self._search_error = False, tr("Поиск не удался: {error}", error=error)
                self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_search(query, task=task, size=size, sort=sort,
                                                                     plain=plain), show, failed)  # fmt: skip

    @Slot(str)
    def openRepo(self, repo: str) -> None:
        self._repo_name, self._repo, self._repo_loading, self._repo_error = repo, {}, True, ""
        self._projector = True
        self.changed.emit()

        def show(data: dict[str, Any]) -> None:
            if self._repo_name == repo:
                self._repo, self._repo_loading = data, False
                self.changed.emit()

        def failed(error: Exception) -> None:
            if self._repo_name == repo:
                self._repo_loading, self._repo_error = False, str(error)
                self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_repo(repo), show, failed)

    @Slot()
    def closeRepo(self) -> None:
        self._repo_name, self._repo, self._repo_error = "", {}, ""
        self.changed.emit()

    @Slot(str)
    def setDrive(self, letter: str) -> None:
        """A drive, or «home» - LocalChat's own `model` folder."""
        self._drive, self._folder = ("" if letter == "home" else letter), ""
        self.changed.emit()

    @Slot(str)
    def setFolder(self, folder: str) -> None:
        """A folder of one's own: typed, or picked in «Обзор…» (a file:// address)."""
        folder = folder.strip()
        self._folder = (QUrl(folder).toLocalFile() or folder) if folder.startswith("file:") else folder
        self.changed.emit()

    @Slot(bool)
    def setProjector(self, on: bool) -> None:
        self._projector = on
        self.changed.emit()

    # ================= installing =================

    def _start(self, spec: dict[str, Any], *, pictures: bool = False) -> None:
        if self._folder:
            spec["folder"] = self._folder
        elif self._drive:
            spec["drive"] = self._drive
        self._busy = True
        self.changed.emit()
        client = self.app.client
        start = (lambda: client.install_pictures_start(spec)) if pictures else (lambda: client.install(spec))

        def done(result: dict[str, Any]) -> None:
            self._busy = False
            job = result.get("job") or {}
            self._upsert(job)  # its row now has «Пауза» and ✕; the status bar shows how it goes
            self.app.flash(tr("Устанавливаю «{name}» — ход внизу окна и на вкладке «Установки»",
                              name=job.get("name", "")))  # fmt: skip
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._busy = False
            self.app.show_error(tr("Не начать установку: {error}", error=error))
            self.changed.emit()

        self.app.call_or_fail(start, done, failed)

    def startInstall(self, repo: str, quant: str) -> None:
        """An install the wizard asks for (10.2): the drive this sheet would choose; a picture model's by its row."""
        if repo.startswith(PICTURES):
            self._start({"id": repo.removeprefix(PICTURES)}, pictures=True)
        else:
            self._start({"repo": repo, "quant": quant})

    @Slot(str)
    def installRecommended(self, repo: str) -> None:
        if repo.startswith(PICTURES):
            self.startInstall(repo, "")
            return
        model = next((m for m in self._catalog if m["repo"] == repo), None)
        if model and model.get("recommended"):
            self._start({"repo": repo, "quant": model["recommended"]["quant"]})

    @Slot(str)
    def install(self, quant: str) -> None:
        if self._repo:
            spec: dict[str, Any] = {"repo": self._repo["repo"], "quant": quant}
            if self._repo.get("projector"):
                spec["projector"] = self._projector
            self._start(spec)

    @Slot(str, str)
    def act(self, job_id: str, action: str) -> None:
        job = next((j for j in self._jobs if j["id"] == job_id), None)
        if job is None:
            return
        if action == "remove":
            own = job.get("own_files", 1)

            def decide(yes: bool) -> None:
                if yes:
                    gone = tr("«{name}» удалена: файлы и запись в llama-swap", name=job["name"])
                    kept = tr("«{name}» убрана из llama-swap, файл остался на диске", name=job["name"])
                    said = gone if own else kept
                    self._do(job, lambda: self.app.client.install_remove(job["model_id"]), said)

            if not own:  # found on the drives: the file is the person's (15.1)
                text = tr("Её запись в config.yaml llama-swap удалится (копия конфига останется рядом); файл модели "
                          "останется на диске, где лежал.")  # fmt: skip
            else:
                text = tr(
                    "Её файлы ({size}) удалятся с диска, запись в config.yaml llama-swap — тоже "
                    "(копия конфига останется рядом).",
                    size=gb(job["total_bytes"]),
                )
            self.app._ask(tr("Удалить модель «{name}»?", name=job["name"]), text, tr("Удалить"), decide)
            return
        if action == "forget":
            said = (tr("Убрано из списка вместе с файлами") if job.get("own_files", 1)
                    else tr("Убрано из списка, файл остался на диске"))  # fmt: skip
            self._do(job, lambda: self.app.client.install_forget(job_id), said)
            return
        self._do(job, lambda: self.app.client.install_act(job_id, action), "")

    def _do(self, job: dict[str, Any], call, said: str) -> None:
        def done(result: dict[str, Any]) -> None:
            if result and result.get("job"):
                self._upsert(result["job"])
            elif result and result.get("forgotten"):
                self._jobs = [j for j in self._jobs if j["id"] != result["forgotten"]]
            if said:
                self.app.flash(said)
            self.changed.emit()
            if job["status"] == "done":
                self.app.load_models()
                self._catalog = []
                self._load_catalog()  # «установлена» there too

        self.app.call_or_fail(call, done, lambda e: self.app.show_error(tr("Не вышло: {error}", error=e)))

    def _upsert(self, job: dict[str, Any]) -> None:
        if not job.get("id"):
            return
        for i, j in enumerate(self._jobs):
            if j["id"] == job["id"]:
                self._jobs[i] = {**j, **job}
                return
        self._jobs.insert(0, job)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "install.found":  # the search on the drives
            job = payload.get("job") or {}
            if job.get("status") == "scanning":
                self._local = {**self._local, "job": job}
                self.changed.emit()
            else:
                self.loadLocal()
            return
        if kind == "install.changed":
            job = payload.get("job") or {}
            before = next((j["status"] for j in self._jobs if j["id"] == job.get("id")), "")
            self._upsert(job)
            if self._jobs_loading and job.get("id"):
                self._heard[job["id"]] = {**(self._heard.get(job["id"]) or {}), **job}
            if job.get("status") == "done" and before != "done":
                self.app.flash(tr("Модель «{name}» установлена: {trial}", name=job.get("name"),
                                  trial=trial_text(job) or tr("готова")))  # fmt: skip
                self.app.load_models()
                self._catalog = []
                self._load_catalog()
            elif job.get("status") == "failed" and before != "failed":
                self.app.flash(tr("«{name}»: {error}", name=job.get("name"), error=job.get("error")), error=True)
            self.changed.emit()
        elif kind == "install.progress":
            if not any(j["id"] == payload.get("id") for j in self._jobs) and not self._jobs_loading:
                self._load_jobs()  # started before this window looked: the list first
            for j in self._jobs:
                if j["id"] == payload.get("id"):
                    done, total = payload["done_bytes"], payload["total_bytes"]
                    j.update(
                        done_bytes=done,
                        total_bytes=total,
                        speed=payload.get("speed", 0),
                        status="downloading",
                        percent=round(100 * done / max(total, 1), 1),
                    )
            self.changed.emit()
        elif kind == "install.forgotten":
            self._jobs = [j for j in self._jobs if j["id"] != payload.get("id")]
            if self._jobs_loading and payload.get("id"):
                self._heard[payload["id"]] = None
            self.changed.emit()
