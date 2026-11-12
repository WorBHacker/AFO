"""The first-run wizard in the window (stage 10.2, D37): «Этот компьютер» - its graphics cards and the llama.cpp build
they want, RAM and the page file; «Движок» - llama.cpp and llama-swap fetched and started with one button; «Первая
модель» - for each purpose the curated model that runs best here, installed by the model installer (10.1).

It opens by itself when the hub says the computer has no engine yet - once: «Позже» keeps it closed (app.ini), and
«Настройки» opens it again. When the hub fetches the engine by itself (39.1) the status bar says how far it is and the
wizard stays closed; once the engine is in, the wizard opens at «Первая модель» - once, «Позже» or not (39.3).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

from ..hub_client import HubError
from .install_view import gb, verdict_of

if TYPE_CHECKING:
    from .controller import Controller

PURPOSES = [("chat", N_("Разговоры"), N_("отвечает на вопросы, пишет и переводит тексты")),
            ("code", N_("Код"), N_("пишет и объясняет программы")),
            ("vision", N_("Понимать картинки"), N_("понимает картинки и снимки экрана")),
            ("image", N_("Рисовать"), N_("рисует картинки по описанию"))]  # fmt: skip
VENDORS = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel", "other": ""}
DISMISSED = "setup/dismissed"
OFFERED = "setup/modelOffered"  # the wizard opened at «Первая модель» once the engine came by itself (39.3)


def card_rows(cards: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in cards:
        if c.get("vram_gib"):
            memory = tr("{gib} ГБ видеопамяти", gib=f"{c['vram_gib']:g}".replace(".", ","))
        else:
            memory = tr("память общая с ОЗУ")
        out.append(f"{c['name']} — {memory}")
    return out or [tr("Видеокарта не найдена")]


def job_text(job: dict[str, Any]) -> str:
    status = job.get("status")
    if status == "downloading":
        total, done = job.get("total") or 0, job.get("done") or 0
        what = job.get("step") or tr("движок")
        if total:
            return tr("Скачиваю {what} — {done} из {total}", what=what, done=gb(done), total=gb(total))
        return tr("Скачиваю {what}", what=what)
    if status == "starting":
        return tr("Запускаю llama-swap…")
    if status == "done":
        return tr("Движок обновлён") if job.get("update") else tr("Движок установлен и запущен")
    if status == "failed":
        return tr("Не вышло: {error}", error=job.get("error"))
    if status == "cancelled":
        return tr("Отменено")
    return ""


def amount(size: float) -> str:
    """A download's size: in MB below a gigabyte - «340 МБ из 820 МБ» moves where «0,3 ГБ» would stand still."""
    return tr("{size:.0f} МБ", size=max(size / 1e6, 1)) if size < 1e9 else gb(size)


def fetched_by_itself(job: dict[str, Any]) -> bool:
    """The hub's scanner fetches the engine now, not the person (39.1)."""
    return bool(job.get("auto")) and job.get("status") in ("downloading", "starting")


def model_rows(models: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for purpose, title, what in PURPOSES:
        m = models.get(purpose)
        best = (m or {}).get("recommended") or {}
        words, tone = verdict_of(best) if best else (tr("подходящей модели нет"), "error")
        out.append({"id": purpose, "title": tr(title), "what": tr(what), "name": (m or {}).get("name", ""),
                    "repo": (m or {}).get("repo", ""), "quant": best.get("quant", ""),
                    "offer": " · ".join(x for x in (best.get("quant"), gb(best["size"])) if x) if best else "",
                    "words": words, "tone": tone,
                    "installed": bool((m or {}).get("installed")), "canInstall": bool(best)})  # fmt: skip
    return out


class SetupView(QObject):
    changed = Signal()
    openRequested = Signal()  # the hub says this computer needs the wizard
    modelsRequested = Signal()  # the engine came by itself: the wizard at «Первая модель» (39.3)

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._state: dict[str, Any] = {}
        self._models: dict[str, Any] = {}
        self._models_loading = False
        self._build = ""
        self._asked = False
        self._update: dict[str, Any] = {}  # the llama.cpp build running and the newest of its kind (10.4)

    # ================= for QML =================

    @Property(bool, notify=changed)
    def needed(self) -> bool:
        return bool(self._state.get("needed"))

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return bool(self._state)

    @Property(list, notify=changed)
    def cards(self) -> list:
        return card_rows(self._state.get("cards") or [])

    @Property(str, notify=changed)
    def why(self) -> str:
        return (self._state.get("build") or {}).get("why", "")

    @Property(list, notify=changed)
    def builds(self) -> list:
        return [{"id": k, "name": v} for k, v in (self._state.get("builds") or {}).items()]

    @Property(str, notify=changed)
    def build(self) -> str:
        return self._build or (self._state.get("build") or {}).get("build", "")

    @Property(str, notify=changed)
    def memoryText(self) -> str:
        memory = self._state.get("memory") or {}
        if not memory:
            return ""
        page = (self._state.get("page_file") or {}).get("gib")
        text = tr("ОЗУ {gib} ГБ", gib=f"{memory.get('ram_total_gib', 0):g}".replace(".", ","))
        if page is None:
            return text
        return text + " · " + tr("файл подкачки ≈ {gib} ГБ", gib=f"{page:g}".replace(".", ","))

    @Property(str, notify=changed)
    def pageAdvice(self) -> str:
        return (self._state.get("page_file") or {}).get("advice", "")

    @Property(str, notify=changed)
    def picturesText(self) -> str:
        """What pictures and videos this computer can make (29.2), before the engine is there."""
        why = (self._state.get("pictures") or {}).get("why", "")
        return tr("Картинки и видео: {what}", what=why) if why else ""

    @Property(str, notify=changed)
    def engineText(self) -> str:
        if not self._state:
            return tr("Смотрю, что есть на этом компьютере…")
        engine = self._state.get("engine") or {}
        if self._state.get("answering"):
            return tr("llama-swap уже работает — движок ставить не нужно")
        if not self._state.get("needed"):
            return tr("Движок на месте: {start}", start=engine.get("start", ""))
        folder = self._state.get("folder", "")
        return tr("Движка для локальных моделей ещё нет. AFO скачает llama.cpp и llama-swap с их страниц на "
                  "GitHub (около 40–600 МБ, смотря по сборке) в {folder} и сам их запустит.",
                  folder=folder)  # fmt: skip

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict:
        """The status bar's word while the hub fetches the engine by itself (39.3): {text, short, percent, tip}; {}
        when it does not."""
        job = self._state.get("job") or {}
        if not fetched_by_itself(job):
            return {}
        total, done = job.get("total") or 0, job.get("done") or 0
        far = tr("{done} из {total}", done=amount(done), total=amount(total)) if total else ""
        if job["status"] == "starting":
            text = tr("Запускаю llama-swap…")
        else:
            text = tr("Скачиваю программу для локальных моделей…") + (f" {far}" if far else "")
        return {"text": text, "short": far or text, "percent": done / total if total else 0.0,
                "tip": job_text(job)}  # fmt: skip

    @Property(str, notify=changed)
    def jobStatus(self) -> str:
        return (self._state.get("job") or {}).get("status", "idle")

    @Property(str, notify=changed)
    def jobText(self) -> str:
        return job_text(self._state.get("job") or {})

    @Property(float, notify=changed)
    def jobPercent(self) -> float:
        job = self._state.get("job") or {}
        return round(100 * (job.get("done") or 0) / job["total"], 1) if job.get("total") else 0.0

    @Property(list, notify=changed)
    def models(self) -> list:
        return model_rows(self._models)

    @Property(bool, notify=changed)
    def modelsLoading(self) -> bool:
        return self._models_loading

    # ================= actions =================

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return
        client = self.app.client

        def looked() -> dict[str, Any]:  # in the background: the hub looks first (39.1), a hub without it says 404
            with contextlib.suppress(HubError):
                client.setup_check()
            return client.setup_state()

        def show(state: dict[str, Any]) -> None:
            self._state = state
            self.changed.emit()
            if not self._asked:
                self._asked = True
                fetching = fetched_by_itself(state.get("job") or {})  # the status bar says it, not the wizard (39.3)
                if state.get("needed") and not fetching and not self.app.settings.value(DISMISSED, False, bool):
                    self.openRequested.emit()
            if not state.get("needed") and not self._models and not self._models_loading:
                self._load_models()
            if not state.get("needed"):
                self._load_update()

        self.app._call(looked, show, lambda _e: None)

    def _load_update(self) -> None:
        def show(found: dict[str, Any]) -> None:
            self._update = found
            self.changed.emit()

        self.app._call(lambda: self.app.client.setup_update_info(), show, lambda _e: None)

    @Property(str, notify=changed)
    def updateText(self) -> str:
        found = self._update
        if not found.get("current"):
            return ""
        if found.get("newer"):
            return tr("llama.cpp {current} — есть новее: {latest}. Обновление встанет рядом со старой сборкой, "
                      "конфиг llama-swap получит путь к новой (копия прежнего — рядом).", current=found["current"],
                      latest=found["latest"])  # fmt: skip
        return tr("llama.cpp {current} — самая новая сборка", current=found["current"])

    @Property(bool, notify=changed)
    def updateAvailable(self) -> bool:
        return bool(self._update.get("newer"))

    @Slot()
    def updateEngine(self) -> None:
        def done(result: dict[str, Any]) -> None:
            self._state = {**self._state, "job": result.get("job") or {}}
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.setup_update(), done,
                              lambda e: self.app.show_error(tr("Движок не обновить: {error}", error=e)))  # fmt: skip

    def _load_models(self) -> None:
        self._models_loading = True
        self.changed.emit()

        def show(data: dict[str, Any]) -> None:
            self._models, self._models_loading = data.get("models") or {}, False
            self.changed.emit()

        def failed(_error: Exception) -> None:
            self._models_loading = False
            self.changed.emit()

        self.app._call(lambda: self.app.client.setup_models(), show, failed)

    @Slot(str)
    def setBuild(self, build: str) -> None:
        self._build = build
        self.changed.emit()

    @Slot()
    def installEngine(self) -> None:
        build = self.build

        def done(result: dict[str, Any]) -> None:
            self._state = {**self._state, "job": result.get("job") or {}}
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.setup_engine(build), done,
                              lambda e: self.app.show_error(tr("Движок не поставить: {error}", error=e)))  # fmt: skip

    @Slot()
    def cancelEngine(self) -> None:
        self.app._call(lambda: self.app.client.setup_cancel(), lambda _r: None, lambda _e: None)

    @Slot(str)
    def installModel(self, purpose: str) -> None:
        row = next((r for r in model_rows(self._models) if r["id"] == purpose), None)
        if row and row["canInstall"]:
            self.app.install_view.startInstall(row["repo"], row["quant"])

    @Slot()
    def dismiss(self) -> None:
        """«Позже»: it does not open by itself again (Settings opens it)."""
        self.app.settings.setValue(DISMISSED, True)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        job = payload.get("job")
        if job is not None:
            self._state = {**self._state, "job": job}
            if kind == "setup.changed" and job.get("status") == "done":
                self.load()  # the engine is there now: the first models
                self.app.load_models()
                if job.get("auto") and not self.app.settings.value(OFFERED, False, bool):
                    self.app.settings.setValue(OFFERED, True)  # once: «Позже» was about the engine by hand (39.3)
                    self.modelsRequested.emit()
            self.changed.emit()
