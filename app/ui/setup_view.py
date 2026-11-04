"""The first-run wizard in the window (stage 10.2, D37): «Этот компьютер» - its graphics cards and the llama.cpp build
they want, RAM and the page file; «Движок» - llama.cpp and llama-swap fetched and started with one button; «Первая
модель» - for each purpose the curated model that runs best here, installed by the model installer (10.1).

It opens by itself when the hub says the computer has no engine yet - once: «Позже» keeps it closed (app.ini), and
«Настройки» opens it again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .install_view import gb, verdict_of

if TYPE_CHECKING:
    from .controller import Controller

PURPOSES = [("chat", "Разговоры", "отвечает на вопросы, пишет и переводит тексты"),
            ("code", "Код", "пишет и объясняет программы"),
            ("vision", "Картинки", "понимает картинки и снимки экрана")]  # fmt: skip
VENDORS = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel", "other": ""}
DISMISSED = "setup/dismissed"


def card_rows(cards: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in cards:
        memory = f"{c['vram_gib']:g} ГБ видеопамяти".replace(".", ",") if c.get("vram_gib") else "память общая с ОЗУ"
        out.append(f"{c['name']} — {memory}")
    return out or ["Видеокарта не найдена"]


def job_text(job: dict[str, Any]) -> str:
    status = job.get("status")
    if status == "downloading":
        total, done = job.get("total") or 0, job.get("done") or 0
        size = f" — {gb(done)} из {gb(total)}" if total else ""
        return f"Скачиваю {job.get('step') or 'движок'}{size}"
    if status == "starting":
        return "Запускаю llama-swap…"
    if status == "done":
        return "Движок обновлён" if job.get("update") else "Движок установлен и запущен"
    if status == "failed":
        return f"Не вышло: {job.get('error')}"
    if status == "cancelled":
        return "Отменено"
    return ""


def model_rows(models: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for purpose, title, what in PURPOSES:
        m = models.get(purpose)
        best = (m or {}).get("recommended") or {}
        words, tone = verdict_of(best) if best else ("подходящей модели нет", "error")
        out.append({"id": purpose, "title": title, "what": what, "name": (m or {}).get("name", ""),
                    "repo": (m or {}).get("repo", ""), "quant": best.get("quant", ""),
                    "offer": f"{best['quant']} · {gb(best['size'])}" if best else "", "words": words, "tone": tone,
                    "installed": bool((m or {}).get("installed")), "canInstall": bool(best)})  # fmt: skip
    return out


class SetupView(QObject):
    changed = Signal()
    openRequested = Signal()  # the hub says this computer needs the wizard

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
        text = f"ОЗУ {memory.get('ram_total_gib', 0):g} ГБ".replace(".", ",")
        return text + (f" · файл подкачки ≈ {page:g} ГБ".replace(".", ",") if page is not None else "")

    @Property(str, notify=changed)
    def pageAdvice(self) -> str:
        return (self._state.get("page_file") or {}).get("advice", "")

    @Property(str, notify=changed)
    def engineText(self) -> str:
        if not self._state:
            return "Смотрю, что есть на этом компьютере…"
        engine = self._state.get("engine") or {}
        if self._state.get("answering"):
            return "llama-swap уже работает — движок ставить не нужно"
        if not self._state.get("needed"):
            return f"Движок на месте: {engine.get('start', '')}"
        folder = self._state.get("folder", "")
        return (f"Движка для локальных моделей ещё нет. AFO скачает llama.cpp и llama-swap с их страниц на "
                f"GitHub (около 40–600 МБ, смотря по сборке) в {folder} и сам их запустит.")  # fmt: skip

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

        def show(state: dict[str, Any]) -> None:
            self._state = state
            self.changed.emit()
            if not self._asked:
                self._asked = True
                if state.get("needed") and not self.app.settings.value(DISMISSED, False, bool):
                    self.openRequested.emit()
            if not state.get("needed") and not self._models and not self._models_loading:
                self._load_models()
            if not state.get("needed"):
                self._load_update()

        self.app._call(lambda: self.app.client.setup_state(), show, lambda _e: None)

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
            return (f"llama.cpp {found['current']} — есть новее: {found['latest']}. Обновление встанет рядом со "
                    "старой сборкой, конфиг llama-swap получит путь к новой (копия прежнего — рядом).")  # fmt: skip
        return f"llama.cpp {found['current']} — самая новая сборка"

    @Property(bool, notify=changed)
    def updateAvailable(self) -> bool:
        return bool(self._update.get("newer"))

    @Slot()
    def updateEngine(self) -> None:
        def done(result: dict[str, Any]) -> None:
            self._state = {**self._state, "job": result.get("job") or {}}
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.setup_update(), done,
                              lambda e: self.app.show_error(f"Движок не обновить: {e}"))  # fmt: skip

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
                              lambda e: self.app.show_error(f"Движок не поставить: {e}"))  # fmt: skip

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
            self.changed.emit()
