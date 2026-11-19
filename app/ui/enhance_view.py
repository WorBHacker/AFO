"""«Улучшить ролик» in the window (stage 54.6): a video of the Studio's or a film's joined one - twice as large (fast
or with details) and/or 60 frames a second or slowed. The sheet: what to do with it, the parts that do it - each
fetched once by a click, how far, «Стоп» -, and «Улучшить»: a job of the Studio's queue, which goes on there as a
video does and ends as a new video beside the source. The hub improves (hub/enhance.py, hub/visual.py); this asks
and shows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

if TYPE_CHECKING:
    from .controller import Controller

SIZES = [("", N_("Как есть")), ("sharp", N_("×2 быстро")), ("detail", N_("×2 с дорисовкой"))]
MOTIONS = [("", N_("Как есть")), ("fps60", N_("60 к/с")), ("slow2", N_("Медленнее ×2")), ("slow4", N_("Медленнее ×4"))]


def improvement(how: dict[str, Any] | None) -> str:
    """What an improved video is, in words - «×2 быстро · 60 к/с»; "" - not an improved one."""
    if not how:
        return ""
    words = [tr(n) for k, n in SIZES if k and k == how.get("size")]
    return " · ".join(words + [tr(n) for k, n in MOTIONS if k and k == how.get("motion")])


def needed(size: str, motion: str) -> list[str]:
    """The parts a choice takes: the size's own, and «Плавность» for any motion."""
    return [p for p in (size, "smooth" if motion else "") if p]


class EnhanceView(QObject):
    changed = Signal()
    partsChanged = Signal()  # the parts' rows alone: not built anew at each change

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._state: dict[str, Any] = {}  # the parts as the hub has them (/v1/enhance)
        self._parts: list[dict[str, Any]] = []
        self._target: dict[str, str] = {}  # what is improved: {"from_job": id} or {"film": id}
        self._title = ""
        self._size, self._motion = "", ""

    # ================= for QML =================

    @Property(str, notify=changed)
    def title(self) -> str:
        return self._title

    @Property(list, notify=changed)
    def sizes(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in SIZES]

    @Property(list, notify=changed)
    def motions(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in MOTIONS]

    @Property(str, notify=changed)
    def size(self) -> str:
        return self._size

    @Property(str, notify=changed)
    def motion(self) -> str:
        return self._motion

    @Property(list, notify=partsChanged)
    def parts(self) -> list:
        return self._parts

    def _part(self, part: str) -> dict[str, Any]:
        return next((p for p in self._state.get("parts") or [] if p["id"] == part), {})

    @Property(str, notify=changed)
    def need(self) -> str:
        """What stands between the choice and «Улучшить»; "" - nothing."""
        if not self._target:
            return ""
        if not self._size and not self._motion:
            return tr("Выберите размер или движение")
        for part in needed(self._size, self._motion):
            found = self._part(part)
            name = found.get("name") or part
            if found.get("state") == "unavailable":
                return tr("«{name}» на этом компьютере не работает: {why}", name=name, why=found.get("why", ""))
            if found.get("state") == "loading":
                return tr("«{name}» скачивается…", name=name)
            if found.get("state") != "ready":
                return tr("Сначала скачайте «{name}» — ниже", name=name)
        return ""

    @Property(bool, notify=changed)
    def canStart(self) -> bool:
        return bool(self._target) and bool(self._size or self._motion) and not self.need

    @Property(str, notify=changed)
    def note(self) -> str:
        """What the choice does, besides the new video beside the source."""
        notes = []
        if self._size == "detail":
            notes.append(tr("С дорисовкой — дольше быстрого и только для роликов обычного размера, до 832 × 480."))
        if self._motion in ("slow2", "slow4"):
            notes.append(tr("Замедленный ролик — без звука."))
        notes.append(tr("Готовый ролик появится в «Последних» «Студии» рядом с исходным; исходный не меняется."))
        return " ".join(notes)

    def _parts_now(self) -> list[dict[str, Any]]:
        """The parts: what each is for, whether it is here, how far its download is, whether the choice needs it."""
        from .install_view import gb  # here: it imports the Studio's view, which imports this one

        wanted, parts = needed(self._size, self._motion), []
        for p in self._state.get("parts") or []:
            size, percent = gb(p["size"]), round(100 * p["got"] / p["total"]) if p.get("total") else 0
            if p["state"] == "ready":
                note = tr("установлено · {size}", size=size)
            elif p["state"] == "loading":
                note = tr("скачивается: {percent}%", percent=percent) if p["got"] else tr("скачивается…")
            elif p["state"] == "unavailable":
                note = p.get("why") or ""
            else:
                note = p.get("error") or tr("не установлено · {size}", size=size)
            parts.append({"id": p["id"], "name": p["name"], "about": p["about"], "state": p["state"], "note": note,
                          "failed": p["state"] in ("missing", "unavailable") and bool(p.get("error") or p.get("why")),
                          "needed": p["id"] in wanted, "getText": tr("Скачать {size}", size=size)})  # fmt: skip
        return parts

    def _take(self, state: dict[str, Any]) -> None:
        self._state = state or {}
        parts = self._parts_now()
        if parts != self._parts:
            self._parts = parts
            self.partsChanged.emit()
        self.changed.emit()

    # ================= what the person does =================

    def _open(self, target: dict[str, str], title: str) -> None:
        self._target, self._title, self._size, self._motion = target, title, "", ""
        self._take(self._state)
        self.app._call(lambda: self.app.client.enhance(), self._take, lambda _e: None)

    @Slot()
    def openStudio(self) -> None:
        """«Улучшить…» under the Studio's video shown."""
        job = self.app.visual_view.shown_job()
        if job.get("kind") == "video" and job.get("status") == "done" and job.get("id"):
            self._open({"from_job": job["id"]}, tr("Ролик: {prompt}", prompt=job.get("prompt", "")))

    @Slot()
    def openFilm(self) -> None:
        """«Улучшить…» of the film open, joined."""
        film = self.app.film_view.open_film()
        if film.get("id") and film.get("file"):
            self._open({"film": film["id"]}, tr("Фильм: {idea}", idea=film.get("idea", "")))

    @Slot(str)
    def setSize(self, size: str) -> None:
        self._size = size if size in {k for k, _n in SIZES} else ""
        self._take(self._state)

    @Slot(str)
    def setMotion(self, motion: str) -> None:
        self._motion = motion if motion in {k for k, _n in MOTIONS} else ""
        self._take(self._state)

    def _part_call(self, call, failed: str) -> None:
        self.app._call(call, self._take, lambda e: self.app.show_error(tr(failed, error=e)))

    @Slot(str)
    def getPart(self, part: str) -> None:
        """«Скачать»: the part's program and its model, in the background (enhance.changed)."""
        self._part_call(lambda: self.app.client.install_enhance(part), N_("Не скачать: {error}"))

    @Slot(str)
    def stopPart(self, part: str) -> None:
        self._part_call(lambda: self.app.client.stop_enhance(part), N_("Не остановить: {error}"))

    @Slot(str)
    def removePart(self, part: str) -> None:
        """A part off the disk - asked first."""
        name = self._part(part).get("name") or part
        self.app._ask(tr("Удалить «{name}»?", name=name),
                      tr("Программа и её модель удалятся с диска. Понадобятся снова — скачаются заново."),
                      tr("Удалить"),
                      lambda yes: self._part_call(lambda: self.app.client.remove_enhance(part),
                                                  N_("Не удалить: {error}")) if yes else None)  # fmt: skip

    @Slot()
    def start(self) -> None:
        """«Улучшить»: a job into the Studio's queue - it goes on there; the sheet closes."""
        if not self.canStart:
            return
        spec = {**self._target, "size": self._size or None, "motion": self._motion or None}

        def started(result: dict[str, Any]) -> None:
            job = result.get("job") or {}
            self.app.visual_view.queued(job)
            self.app.flash(tr("Улучшаю — ход виден в очереди «Студии»") if "from_job" in spec
                           else tr("Улучшаю — ход виден под фильмом"))  # fmt: skip

        self.app.call_or_fail(lambda: self.app.client.visual_improve(spec), started,
                              lambda e: self.app.show_error(tr("Не улучшить: {error}", error=e)))  # fmt: skip

    # ================= the hub's events =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "enhance.changed":  # a part's download: how far, done, stopped
            self._take(payload)
