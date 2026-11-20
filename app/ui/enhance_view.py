"""«Улучшить ролик» in the window (stage 54.6): a video of the Studio's or a film's joined one - twice as large (fast
or with details) and/or 60 frames a second or slowed; (56) only the object clicked on a frame of it, the rest dark.
The sheet: what to do with it, the parts that do it - each
fetched once by a click, how far, «Стоп» -, and «Улучшить»: a job of the Studio's queue, which goes on there as a
video does and ends as a new video beside the source. The hub improves (hub/enhance.py, hub/visual.py); this asks
and shows."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

if TYPE_CHECKING:
    from .controller import Controller

SIZES = [("", N_("Как есть")), ("sharp", N_("×2 быстро")), ("detail", N_("×2 с дорисовкой"))]
MOTIONS = [("", N_("Как есть")), ("fps60", N_("60 к/с")), ("slow2", N_("Медленнее ×2")), ("slow4", N_("Медленнее ×4"))]
# (56) SAM 2.1: the object clicked, alone; (58) VACE: the object clicked, or all but it, drawn anew
SUBJECTS = [("", N_("Весь кадр")), ("select", N_("Только предмет")), ("object", N_("Заменить предмет")),
            ("background", N_("Заменить фон"))]  # fmt: skip
FORMATS = [("", N_("Как есть")), ("9:16", "9:16"), ("1:1", "1:1"), ("16:9", "16:9")]  # (58) the edges drawn
SPEEDS = [("fast", N_("Быстрее")), ("careful", N_("Тщательнее"))]  # (58) the person chooses: 58.1 saw no difference
DRAWN = {"object": N_("Замена предмета"), "background": N_("Замена фона")}
# (58.1) a window of VACE's 81 frames, minutes: «Быстрее» 20 steps, «Тщательнее» 25 - the load and the VAE with them
WINDOW_MIN = {True: 7.1, False: 8.7}


def improvement(how: dict[str, Any] | None) -> str:
    """What an improved video is, in words - «Только предмет · ×2 быстро · 60 к/с»; "" - not an improved one."""
    if not how:
        return ""
    words = [tr(SUBJECTS[1][1])] if how.get("select") else []
    if draw := how.get("draw"):
        kind = draw.get("kind")
        words.append(tr(DRAWN[kind]) if kind in DRAWN else tr("Формат {canvas}", canvas=draw.get("canvas", "")))
    words += [tr(n) for k, n in SIZES if k and k == how.get("size")]
    return " · ".join(words + [tr(n) for k, n in MOTIONS if k and k == how.get("motion")])


def subsampled(fps: int) -> bool:
    """(58) «Быстрее» draws this video at 16 frames a second, RIFE the rest - as the hub's `enhance.stride`: 24, 32."""
    return fps > 16 and (fps * 2) % 16 == 0


def needed(size: str, motion: str, subject: str = "", canvas: str = "", fast: bool = False, fps: int = 24) -> list[str]:
    """The parts a choice takes: «Выделение предмета» for the object (alone, or replaced), «Замена и дорисовка» to
    draw anew, the size's own, «Плавность» for any motion and for «Быстрее» drawing fewer frames - in the order
    they work, each once."""
    drawing = subject in DRAWN or bool(canvas)
    parts = ["select" if subject else "", "replace" if drawing else "", size,
             "smooth" if motion or (drawing and fast and subsampled(fps)) else ""]  # fmt: skip
    return list(dict.fromkeys(p for p in parts if p))


def minutes(frames: int, fps: int, fast: bool) -> int:
    """(58) How long VACE draws a video of `frames` frames, about: its windows of 81 - those of «Быстрее» at 16 a
    second - by 58.1's minutes. Cuts not counted: a window each shot more."""
    step = fps / 16 if fast and subsampled(fps) else 1
    moments = math.ceil(max(frames - 1, 0) / step) + 1
    return math.ceil((1 + math.ceil(max(moments - 81, 0) / 72)) * WINDOW_MIN[fast])


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
        # (56) «Только предмет»: the moment of the video shown (a share of its length), its frame - the n-th one the
        # provider was given, 0: none yet -, where the object was clicked on it, what the frame's fetch says
        self._subject, self._moment, self._point = "", 0.0, None
        self._frame, self._asked, self._frame_note = 0, 0, ""
        # (58) the format the edges are drawn to, what to draw, «Быстрее» or not; the video's rate and length
        self._canvas, self._words, self._fast = "", "", True
        self._fps, self._frames = 24, 0

    def _drawing(self) -> bool:
        return self._subject in DRAWN or bool(self._canvas)

    def _wanted(self) -> list[str]:
        return needed(self._size, self._motion, self._subject, self._canvas, self._fast, self._fps)

    # ================= for QML =================

    @Property(list, notify=changed)
    def formats(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in FORMATS]

    @Property(str, notify=changed)
    def format(self) -> str:
        return self._canvas

    @Property(list, notify=changed)
    def speeds(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in SPEEDS]

    @Property(str, notify=changed)
    def speed(self) -> str:
        return "fast" if self._fast else "careful"

    @Property(bool, notify=changed)
    def drawing(self) -> bool:
        """(58) VACE draws: «Заменить предмет», «Заменить фон», a format - the words, the speed shown."""
        return self._drawing()

    @Property(str, notify=changed)
    def words(self) -> str:
        return self._words

    @Property(str, notify=changed)
    def wordsHint(self) -> str:
        if self._subject == "object":
            return tr("На что заменить — например, «красная керамическая кружка»")
        if self._subject == "background":
            return tr("Какой фон — например, «песчаный пляж с пальмами»")
        return tr("Что вокруг — можно не писать: возьмётся описание ролика")

    @Property(str, notify=changed)
    def pickHint(self) -> str:
        if self._subject == "background":
            return tr("Щёлкните по тому, что оставить, — человеку или предмету; всё остальное станет новым фоном. "
                      "Не видно здесь — передвиньте ползунок к другому моменту ролика.")  # fmt: skip
        return tr("Щёлкните по предмету на кадре. Не видно его здесь — передвиньте ползунок к другому моменту ролика.")

    @Property(str, notify=changed)
    def speedNote(self) -> str:
        """(58) What each speed costs - this video's minutes when its length is known; the choice is the person's."""
        if self._frames:
            fast, careful = (minutes(self._frames, self._fps, f) for f in (True, False))
            said = tr("Этот ролик: быстрее — около {fast} мин, тщательнее — около {careful} мин.", fast=fast,
                      careful=careful)  # fmt: skip
        else:
            said = tr("Около 1,5 мин на секунду ролика быстрее, около 3 мин — тщательнее.")
        how = (tr("Быстрее — 20 шагов, 16 кадров в секунду, остальные досчитывает «Плавность».")
               if subsampled(self._fps) else tr("Быстрее — 20 шагов."))  # fmt: skip
        return " ".join([said, how, tr("Тщательнее — 25 шагов, каждый кадр. На пробе разницы на глаз не было.")])

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

    @Property(list, notify=changed)
    def subjects(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in SUBJECTS]

    @Property(str, notify=changed)
    def subject(self) -> str:
        return self._subject

    @Property(float, notify=changed)
    def moment(self) -> float:
        return self._moment

    @Property(str, notify=changed)
    def frame(self) -> str:
        """The frame to click the object on - the provider's; "" until it came."""
        return f"image://pictures/pick/{self._frame}" if self._frame else ""

    @Property(str, notify=changed)
    def frameNote(self) -> str:
        return self._frame_note

    @Property(bool, notify=changed)
    def picked(self) -> bool:
        return self._point is not None

    @Property(float, notify=changed)
    def pointX(self) -> float:
        return self._point[0] if self._point else 0.0

    @Property(float, notify=changed)
    def pointY(self) -> float:
        return self._point[1] if self._point else 0.0

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
        if not self._size and not self._motion and not self._subject and not self._canvas:
            return tr("Выберите, что сделать с роликом")
        for part in self._wanted():
            found = self._part(part)
            name = found.get("name") or part
            if found.get("state") == "unavailable":
                return tr("«{name}» на этом компьютере не работает: {why}", name=name, why=found.get("why", ""))
            if found.get("state") == "loading":
                return tr("«{name}» скачивается…", name=name)
            if found.get("state") != "ready":
                return tr("Сначала скачайте «{name}» — ниже", name=name)
        if self._subject and self._point is None:
            return tr("Щёлкните по предмету на кадре")
        if self._subject in DRAWN and not self._words.strip():
            return tr("Напишите, на что заменить")
        return ""

    @Property(bool, notify=changed)
    def canStart(self) -> bool:
        chosen = self._size or self._motion or self._subject or self._canvas
        return bool(self._target) and bool(chosen) and not self.need

    @Property(str, notify=changed)
    def note(self) -> str:
        """What the choice does, besides the new video beside the source."""
        notes = []
        if self._subject == "select":
            notes.append(tr("Останется только предмет, по которому вы щёлкнули, — всё вокруг станет тёмным. "
                            "Предмет найдётся на всём ролике, до и после этого кадра."))  # fmt: skip
        if self._subject == "object":
            notes.append(tr("Предмет, по которому вы щёлкнули, нарисуется заново по вашим словам на всём ролике; "
                            "всё остальное останется как было. Что держит предмет, но не названо словами, может "
                            "нарисоваться как попало."))  # fmt: skip
        if self._subject == "background":
            notes.append(tr("Всё, кроме того, по чему вы щёлкнули, нарисуется заново по вашим словам — и руки "
                            "тоже, если они не выделены."))  # fmt: skip
        if self._canvas:
            notes.append(tr("Ролик останется в середине кадра {canvas}, края вокруг дорисуются.",
                            canvas=self._canvas))  # fmt: skip
        if self._size == "detail":
            notes.append(tr("С дорисовкой — дольше быстрого и только для роликов обычного размера, до 832 × 480."))
        if self._motion in ("slow2", "slow4"):
            notes.append(tr("Замедленный ролик — без звука."))
        notes.append(tr("Готовый ролик появится в «Последних» «Студии» рядом с исходным; исходный не меняется."))
        return " ".join(notes)

    def _parts_now(self) -> list[dict[str, Any]]:
        """The parts: what each is for, whether it is here, how far its download is, whether the choice needs it."""
        from .install_view import gb  # here: it imports the Studio's view, which imports this one

        wanted, parts = self._wanted(), []
        for p in self._state.get("parts") or []:
            size, percent = gb(p["size"]), round(100 * p["got"] / p["total"]) if p.get("total") else 0
            if p["state"] == "ready":  # (56.6) what it takes: an engine on PyTorch - more than it brings
                note = tr("установлено · {size}", size=gb(p.get("disk") or p["size"]))
            elif p["state"] == "loading":
                note = tr("скачивается: {percent}%", percent=percent) if p["got"] else tr("скачивается…")
            elif p["state"] == "unavailable":
                note = p.get("why") or ""
            elif p.get("error"):
                note = p["error"]
            elif p.get("disk", 0) > p["size"] * 1.2:  # (56) an engine on PyTorch unpacks to more than it brings
                note = tr("не установлено · {size}, на диске ≈ {disk}", size=size, disk=gb(p["disk"]))
            else:
                note = tr("не установлено · {size}", size=size)
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

    def _open(self, target: dict[str, str], title: str, *, fps: int = 24, frames: int = 0) -> None:
        self._target, self._title, self._size, self._motion = target, title, "", ""
        self._subject, self._moment, self._point, self._frame, self._frame_note = "", 0.0, None, 0, ""
        self._canvas, self._words, self._fast, self._fps, self._frames = "", "", True, fps, frames
        self._asked += 1  # a frame of the video before is not this one's
        self._take(self._state)
        self.app._call(lambda: self.app.client.enhance(), self._take, lambda _e: None)

    @Slot()
    def openStudio(self) -> None:
        """«Улучшить…» under the Studio's video shown."""
        job = self.app.visual_view.shown_job()
        if job.get("kind") == "video" and job.get("status") == "done" and job.get("id"):
            self._open({"from_job": job["id"]}, tr("Ролик: {prompt}", prompt=job.get("prompt", "")),
                       fps=int(job.get("fps") or 24), frames=int(job.get("frames") or 0))  # fmt: skip

    @Slot()
    def openFilm(self) -> None:
        """«Улучшить…» of the film open, joined - 24 frames a second, its length its own."""
        film = self.app.film_view.open_film()
        if film.get("id") and film.get("file"):
            self._open({"film": film["id"]}, tr("Фильм: {idea}", idea=film.get("idea", "")),
                       frames=round(float(film.get("seconds") or 0) * 24))  # fmt: skip

    @Slot(str)
    def setSize(self, size: str) -> None:
        self._size = size if size in {k for k, _n in SIZES} else ""
        self._take(self._state)

    @Slot(str)
    def setMotion(self, motion: str) -> None:
        self._motion = motion if motion in {k for k, _n in MOTIONS} else ""
        self._take(self._state)

    @Slot(str)
    def setSubject(self, subject: str) -> None:
        """(56) «Весь кадр» | «Только предмет» | (58) «Заменить предмет» | «Заменить фон» - the frame to click on
        asked of the hub the first time. An object or the background - not with a format: one drawing at a time."""
        self._subject = subject if subject in {k for k, _n in SUBJECTS} else ""
        if self._subject:
            self._canvas = ""
        self._take(self._state)
        if self._subject and not self._frame and self._target:
            self._fetch_frame()

    @Slot(str)
    def setFormat(self, canvas: str) -> None:
        """(58) «Как есть» | 9:16 | 1:1 | 16:9 - the edges drawn; only for the whole frame."""
        self._canvas = canvas if canvas in {k for k, _n in FORMATS} else ""
        if self._canvas:
            self._subject = ""
        self._take(self._state)

    @Slot(str)
    def setWords(self, words: str) -> None:
        self._words = words[:500]
        self._take(self._state)

    @Slot(str)
    def setSpeed(self, speed: str) -> None:
        """(58) «Быстрее» | «Тщательнее» - the person's choice."""
        self._fast = speed != "careful"
        self._take(self._state)

    @Slot(float)
    def setMoment(self, share: float) -> None:
        """(56) Another moment of the video - its frame; the point clicked on the one before is no more."""
        share = min(max(float(share), 0.0), 1.0)
        if not self._target or (share == self._moment and self._frame):
            return
        self._moment, self._point = share, None
        self._take(self._state)
        self._fetch_frame()

    @Slot(float, float)
    def pick(self, x: float, y: float) -> None:
        """(56) The object clicked: where on the frame shown, shares of its width and height."""
        if not self._frame:
            return
        self._point = (min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0))
        self._take(self._state)

    def _fetch_frame(self) -> None:
        """The frame of the moment, from the hub - the one the engine will be given; a later ask wins."""
        self._asked += 1
        asked, target, share = self._asked, dict(self._target), self._moment
        self._frame_note = tr("Беру кадр…")
        self.changed.emit()

        def got(data: bytes) -> None:
            if asked == self._asked:
                self.app.visual_view.provider.pick = data
                self._frame, self._frame_note = self._frame + 1, ""
                self.changed.emit()

        def failed(error: Exception) -> None:
            if asked == self._asked:
                self._frame_note = tr("Кадр не взять: {error}", error=error)
                self.changed.emit()

        if not self.app._call(lambda: self.app.client.improve_frame(target, share), got, failed):
            self._frame_note = ""  # no hub: said by the window already
            self.changed.emit()

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
        spec: dict[str, Any] = {**self._target, "size": self._size or None, "motion": self._motion or None}
        clicked = {"share": round(self._moment, 4), "x": round(self._point[0], 4),
                   "y": round(self._point[1], 4)} if self._point is not None else {}  # fmt: skip
        if self._subject == "select" and clicked:
            spec["select"] = clicked
        elif self._subject in DRAWN and clicked:  # (58)
            spec["draw"] = {"kind": self._subject, "words": self._words.strip(), "fast": self._fast, **clicked}
        elif self._canvas:
            spec["draw"] = {"kind": "edges", "canvas": self._canvas, "words": self._words.strip(), "fast": self._fast}

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
