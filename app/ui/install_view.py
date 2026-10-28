"""Installing local models in the window (stage 10.1, D37): the sheet «Установка моделей». The curated list with the
variant this computer can run best, a search of Hugging Face, one repository's variants judged against the memory,
the drive the files go to, and the installs - their progress, pause, «Продолжить», the trial run, removal.

The hub does the work (hub/model_install.py); this only shows it and asks. Words are plain: «помещается в
видеопамять — быстро», not layers and quantization; the technical name of a variant is beside it for those who know.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.plain_errors import explain

if TYPE_CHECKING:
    from .controller import Controller

PURPOSES = [("", "Все"), ("chat", "Разговоры"), ("code", "Код"), ("vision", "Картинки")]
TONES = {"gpu": "ok", "split": "warn", "split_moe": "warn", "cpu": "warn", "no": "error"}
STATUS = {"queued": "в очереди", "downloading": "загружается", "paused": "на паузе",
          "configuring": "записываю в llama-swap", "trial": "пробный запуск", "done": "установлена",
          "failed": "не вышло", "cancelled": "отменена", "removed": "удалена"}  # fmt: skip
ACTIONS = {
    "queued": [("pause", "Пауза", "plain"), ("cancel", "Отменить", "danger")],
    "downloading": [("pause", "Пауза", "plain"), ("cancel", "Отменить", "danger")],
    "paused": [("resume", "Продолжить", "primary"), ("cancel", "Отменить", "danger")],
    "failed": [("resume", "Повторить", "primary"), ("forget", "Убрать", "danger")],
    "cancelled": [("forget", "Убрать", "plain")],
    "done": [("remove", "Удалить", "danger")],
}
LIVE = ("queued", "downloading", "configuring", "trial")
WHERE = {"gpu": "целиком в видеопамяти", "split": "частью в ОЗУ"}
PICKS = {"fast": "быстрее", "quality": "качественнее"}  # «Быстрее ↔ Качественнее» beside the recommended one


def gb(size: float, *, of_bytes: bool = True) -> str:
    """A size as people read it: «8,1 ГБ», a small one «52 МБ» (decimal units, as Hugging Face shows them)."""
    value = size / 1e9 if of_bytes else size
    if value < 0.1:
        return f"{max(value * 1000, 1):.0f} МБ"
    return f"{value:.1f}".replace(".", ",") + " ГБ"


def count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}".replace(".", ",") + " млн"
    if n >= 1000:
        return f"{n / 1e3:.0f} тыс."
    return str(n)


def system_text(system: dict[str, Any]) -> str:
    space = system.get("space") or {}
    if space.get("vram_total_gib"):
        parts = [f"{space.get('gpu') or 'Видеокарта'}: для новой модели ≈ {gb(space['vram_gib'], of_bytes=False)} "
                 "видеопамяти"]  # fmt: skip
    else:
        parts = ["Видеокарта NVIDIA не найдена — модели будут работать в ОЗУ, медленно"]
    parts.append(f"ОЗУ свободно ≈ {gb(space.get('ram_gib') or 0, of_bytes=False)}")
    if space.get("loaded"):
        parts.append(f"сейчас загружена «{', '.join(space['loaded'])}» — на время работы новой она выгрузится")
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
            words, tone = f"не поместится: даже самый маленький вариант — {gb(m['smallest']['size'])}", "error"
            offer = ""
        else:
            words, tone, offer = m.get("error", ""), "error", ""
        out.append({"id": m["id"], "repo": m["repo"], "quant": best["quant"] if best else "", "name": m["name"],
                    "about": (m.get("about") or {}).get("ru", ""),
                    "purposes": " · ".join(m.get("purpose_words") or []), "license": m.get("license", ""),
                    "gated": bool(m.get("gated")), "words": words, "tone": tone, "offer": offer,
                    "installed": installed,
                    "canInstall": bool(best) and not m.get("installed_recommended")})  # fmt: skip
    return out


def search_rows(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in models:
        note = [f"↓ {count(m.get('downloads') or 0)}", f"♥ {count(m.get('likes') or 0)}"]
        if m.get("license"):
            note.append(f"лицензия {m['license']}")
        if m.get("updated"):
            note.append(f"обновлена {m['updated']}")
        badges = (["снятые ограничения"] if m.get("unrestricted") else []) + (
            ["лицензия на сайте"] if m.get("gated") else [])  # fmt: skip
        out.append({"repo": m["repo"], "note": " · ".join(note), "badges": badges})
    return out


def variant_rows(details: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for v in details.get("variants") or []:
        words, tone = verdict_of(v)
        bits = f"≈ {v['bits']:.1f}".replace(".", ",") + " бита на вес" if v.get("bits") else ""
        out.append({"quant": v["quant"], "size": gb(v["size"]), "bits": bits, "words": words, "tone": tone,
                    "recommended": bool(v.get("recommended")), "parts": len(v.get("files") or []),
                    "pick": PICKS.get(v.get("pick") or "", "")})  # fmt: skip
    return out


def drive_rows(drives: list[dict[str, Any]], chosen: str, home: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Where a model may go: LocalChat's own `model` folder first (the default, 2026-10-28), then each drive."""
    out = []
    if home and home.get("folder"):
        free = f" · свободно {gb(home['free_gib'], of_bytes=False)}" if home.get("free_gib") is not None else ""
        out.append({"id": "home", "selected": chosen in ("", "home"), "slow": home.get("kind") in ("hdd", "usb"),
                    "name": "Папка LocalChat" + (f" ({home['letter']}: {home['words']})" if home.get("letter")
                                                 else "") + free})  # fmt: skip
    return out + [{"id": d["letter"], "selected": d["letter"] == chosen, "slow": d.get("kind") in ("hdd", "usb"),
                   "name": f"{d['letter']}: {d.get('words', '')} · свободно {gb(d['free_gib'], of_bytes=False)}"}
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
    parts = [f"{gb(done)} из {gb(total)}"]
    speed = job.get("speed") or 0
    if job.get("status") == "downloading" and speed > 0:
        parts.append(f"{speed / 1e6:.1f}".replace(".", ",") + " МБ/с")
        left = (total - done) / speed
        parts.append(f"ещё ≈ {max(1, round(left / 60))} мин" if left >= 60 else f"ещё ≈ {max(1, round(left))} с")
    return " · ".join(parts)


def percent_of(job: dict[str, Any]) -> float:
    total = job.get("total_bytes") or 0
    return float(job.get("percent") or (round(100 * (job.get("done_bytes") or 0) / total, 1) if total else 0.0))


def job_state(job: dict[str, Any] | None) -> dict[str, Any]:
    """An install where its model's «Установить» was (2026-10-28, the user's word): pause or go on, ✕ to cancel (or
    to take a failed one away), and its progress in the line under the name."""
    if not job:
        return {}
    actions = {a: label for a, label, _kind in ACTIONS.get(job["status"], [])}
    primary = next((a for a in ("pause", "resume") if a in actions), "")
    pct = percent_of(job)
    progress = progress_text(job)
    words = STATUS.get(job["status"], job["status"])
    if job["status"] == "downloading" and progress:
        line = f"{pct:.0f} % · {progress}"
    elif job["status"] == "paused":
        line = f"на паузе · {pct:.0f} %" + (f" · {progress}" if progress else "")
    elif job["status"] == "failed":
        line = f"не вышло: {job.get('error') or ''}".rstrip(": ")
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
            bits += [
                f"{speed / 1e6:.1f}".replace(".", ",") + " МБ/с",
                f"ещё ≈ {max(1, round(left / 60))} мин" if left >= 60 else f"ещё ≈ {max(1, round(left))} с",
            ]
        text = " · ".join(bits)
    else:
        text = STATUS.get(job["status"], job["status"])
    more = f" (и ещё {len(live) - 1})" if len(live) > 1 else ""
    tip = f"Установка «{job['name']}»: {progress_text(job) or STATUS.get(job['status'], '')}. Нажмите — «Установки»"
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
        parts = [f"загрузилась и ответила за {trial['seconds']} с".replace(".", ",")]
        if trial.get("tok_s"):
            parts.append(f"{round(trial['tok_s'])} ток/с")
        if trial.get("where") in WHERE:
            parts.append(WHERE[trial["where"]])
        if len(trial.get("tries") or []) > 1:
            parts.append(f"с {len(trial['tries'])}-й попытки — настройки уменьшены")
        return " · ".join(parts)
    return ""


def job_rows(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": j["id"], "name": j["name"], "modelId": j["model_id"], "repo": j["repo"], "status": j["status"],
             "statusWords": STATUS.get(j["status"], j["status"]), "percent": j.get("percent") or 0.0,
             "progress": progress_text(j), "error": with_hint(j.get("error") or ""), "trial": trial_text(j),
             "live": j["status"] in ("queued", "downloading", "configuring", "trial"),
             "actions": [{"id": a, "label": label, "kind": kind} for a, label, kind in ACTIONS.get(j["status"], [])]}
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
        self._purpose = ""
        self._tab = "catalog"
        self._query = ""
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

    # ================= for QML =================

    @Property(str, notify=changed)
    def systemText(self) -> str:
        return system_text(self._system) if self._system else "Смотрю, что умеет этот компьютер…"

    @Property(str, notify=changed)
    def problem(self) -> str:
        return self._system.get("problem", "")

    @Property(str, notify=changed)
    def tab(self) -> str:
        return self._tab

    @Property(list, notify=changed)
    def purposes(self) -> list:
        return [{"id": pid, "name": name} for pid, name in PURPOSES]

    @Property(str, notify=changed)
    def purpose(self) -> str:
        return self._purpose

    @Property(list, notify=changed)
    def catalog(self) -> list:
        return [{**r, "job": job_state(self._job_for(r["repo"], r["quant"]))} if r["quant"] else {**r, "job": {}}
                for r in catalog_rows(self._catalog, self._purpose)]  # fmt: skip

    @Property(bool, notify=changed)
    def catalogLoading(self) -> bool:
        return self._catalog_loading

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
        notes = [f"лицензия {r['license']}" if r.get("license") else "лицензия не указана"]
        if r.get("params"):
            notes.append(f"{r['params'] / 1e9:.1f}".replace(".", ",") + " млрд параметров")
        if r.get("context_trained"):
            notes.append(f"контекст до {r['context_trained'] // 1024}K")
        if r.get("moe"):
            notes.append("из «экспертов»")
        lens = r.get("projector")
        return {"repo": r.get("repo", ""), "page": r.get("page", ""), "notes": " · ".join(notes),
                "unrestricted": bool(r.get("unrestricted")), "gated": bool(r.get("gated")),
                "projector": bool(lens),
                "projectorText": f"Понимать картинки (+{gb(lens['size'])})" if lens else ""}  # fmt: skip

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
            self._catalog_loading, self._catalog_error = False, f"Каталог не загрузился: {error}"
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_catalog(), show, failed)

    @Slot(str)
    def setTab(self, tab: str) -> None:
        self._tab = tab
        self.changed.emit()

    @Slot(str)
    def setPurpose(self, purpose: str) -> None:
        self._purpose = purpose
        self.changed.emit()

    @Slot(str)
    def search(self, query: str) -> None:
        self._query = query.strip()
        if not self._query:
            self._found = []
            self.changed.emit()
            return
        self._searching, self._search_error = True, ""
        self.changed.emit()
        asked = self._query

        def show(data: dict[str, Any]) -> None:
            if asked == self._query:
                self._found, self._searching = data.get("models", []), False
                self.changed.emit()

        def failed(error: Exception) -> None:
            self._searching, self._search_error = False, f"Поиск не удался: {error}"
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install_search(asked), show, failed)

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

    def _start(self, spec: dict[str, Any]) -> None:
        if self._folder:
            spec["folder"] = self._folder
        elif self._drive:
            spec["drive"] = self._drive
        self._busy = True
        self.changed.emit()

        def done(result: dict[str, Any]) -> None:
            self._busy = False
            job = result.get("job") or {}
            self._upsert(job)  # its row now has «Пауза» and ✕; the status bar shows how it goes
            self.app.flash(f"Устанавливаю «{job.get('name', '')}» — ход внизу окна и на вкладке «Установки»")
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._busy = False
            self.app.show_error(f"Не начать установку: {error}")
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.install(spec), done, failed)

    def startInstall(self, repo: str, quant: str) -> None:
        """An install the wizard asks for (10.2): the drive this sheet would choose."""
        self._start({"repo": repo, "quant": quant})

    @Slot(str)
    def installRecommended(self, repo: str) -> None:
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

            def decide(yes: bool) -> None:
                if yes:
                    self._do(job, lambda: self.app.client.install_remove(job["model_id"]),
                             f"«{job['name']}» удалена: файлы и запись в llama-swap")  # fmt: skip

            text = (
                f"Её файлы ({gb(job['total_bytes'])}) удалятся с диска, запись в config.yaml llama-swap — тоже "
                "(копия конфига останется рядом)."
            )
            self.app._ask(f"Удалить модель «{job['name']}»?", text, "Удалить", decide)
            return
        if action == "forget":
            self._do(job, lambda: self.app.client.install_forget(job_id), "Убрано из списка вместе с файлами")
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

        self.app.call_or_fail(call, done, lambda e: self.app.show_error(f"Не вышло: {e}"))

    def _upsert(self, job: dict[str, Any]) -> None:
        if not job.get("id"):
            return
        for i, j in enumerate(self._jobs):
            if j["id"] == job["id"]:
                self._jobs[i] = {**j, **job}
                return
        self._jobs.insert(0, job)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "install.changed":
            job = payload.get("job") or {}
            before = next((j["status"] for j in self._jobs if j["id"] == job.get("id")), "")
            self._upsert(job)
            if self._jobs_loading and job.get("id"):
                self._heard[job["id"]] = {**(self._heard.get(job["id"]) or {}), **job}
            if job.get("status") == "done" and before != "done":
                self.app.flash(f"Модель «{job.get('name')}» установлена: {trial_text(job) or 'готова'}")
                self.app.load_models()
                self._catalog = []
                self._load_catalog()
            elif job.get("status") == "failed" and before != "failed":
                self.app.flash(f"«{job.get('name')}»: {job.get('error')}", error=True)
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
