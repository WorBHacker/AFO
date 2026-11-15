"""«Нарисовать» in the window (stage 29.5, D55): a description, the shape, «Быстрее ↔ Качественнее», the picture as it
is drawn - step and preview - and the result: «Ещё вариант», «Сохранить как…», the folder; the latest pictures
beside it. The chip «Нарисовать» in the chat's input opens it with what was typed.

«По картинке» (29.6б): the picture shown («По этой картинке»), a file («Выбрать картинку…» - any picture Qt reads,
sent as PNG), or a chat's (a click on it there) is the source, with «Чуть / Заметно / Сильно»; the hub sizes the
picture by its shape. Only models of several steps draw from a source.

A picture that did not come out goes a rung lower by itself in the hub (29.4б); the sheet says how - «меньшим
размером», «моделью …» - and before the next one shows where it will begin: the model that comes out here in place of
the chosen one, no more pixels than came out.

The hub draws (hub/visual.py); this asks and shows. Pictures reach QML through an image provider
(image://pictures/...): the preview's JPEG from the progress events, a result's PNG fetched once and kept.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QBuffer, QByteArray, QIODevice, QObject, QSize, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtQuick import QQuickImageProvider

from common.i18n import N_, tr
from common.picture_sizes import CPU_PIXELS, SHAPES, clip_of, fit, size_for, video_size

from ..hub_client import HubError

if TYPE_CHECKING:
    from .controller import Controller

SHAPE_NAMES = [("square", N_("Квадрат")), ("wide", N_("Широкая")), ("tall", N_("Высокая"))]
QUALITY = [("fast", N_("Быстрее")), ("quality", N_("Качественнее"))]
STATUS = {"queued": N_("в очереди"), "running": N_("рисую"), "done": N_("готово"), "failed": N_("не вышло"),
          "cancelled": N_("остановлено")}  # fmt: skip
RECENT = 30  # the Studio's gallery (48.7в) - and its queue: the jobs going on are among the latest
GALLERY = [("all", N_("Всё")), ("image", N_("Картинки")), ("video", N_("Видео"))]  # 48.7в
MODES = [("picture", N_("Картинка")), ("video", N_("Видео"))]  # 29.8б
# a video by words, by a picture (its first frame), by two frames (48.7б) - as its model films (the hub's `modes`):
# the switch's short word (the Studio's column is narrow), the model menu's whole one
VIDEO_MODES = [("text", N_("Описание"), N_("по описанию")), ("picture", N_("Кадр"), N_("по картинке")),
               ("frames", N_("Два кадра"), N_("по двум кадрам"))]  # fmt: skip
LENGTHS = [("1", N_("1 с")), ("2", N_("2 с")), ("3", N_("3 с"))]
STRENGTHS = [("little", N_("Чуть"), 0.3), ("some", N_("Заметно"), 0.6), ("much", N_("Сильно"), 0.85)]
_TIER = {"fast": ("main", "light", "quality"), "quality": ("quality", "main", "light")}


def pick_model(models: list[dict[str, Any]], default: str, quality: str) -> dict[str, Any] | None:
    """«Быстрее» - the hub's default (the tier this computer draws fast); «Качественнее» - the quality one if there."""
    if quality == "fast":
        found = next((m for m in models if m["model_id"] == default), None)
        if found:
            return found
    order = _TIER.get(quality, _TIER["fast"])
    return min(models, key=lambda m: order.index(m["tier"]) if m["tier"] in order else len(order), default=None)


def seconds_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return tr("{seconds} с", seconds=f"{seconds:.1f}".replace(".", ",") if seconds < 10 else round(seconds))


class PictureProvider(QQuickImageProvider):
    """image://pictures/preview/<n> - the picture as it is drawn; image://pictures/job/<id> - a finished one;
    image://pictures/blob/<sha256> - one a chat holds (29.6: a model drew it), asked of the hub the first time - on
    QML's loading thread, the chat's frame is asynchronous."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.preview = b""
        self.pictures: dict[str, bytes] = {}
        self.blobs: dict[str, bytes] = {}
        self.fetch: Callable[[str], bytes] | None = None  # a chat's picture from the hub
        self.source = b""  # «По картинке»: the source shown (29.6б)
        self.last = b""  # a video's last frame shown (48.7б)

    def requestImage(self, picture_id: str, size: QSize, requested: QSize) -> QImage:
        kind, _, key = picture_id.partition("/")
        if kind == "blob":
            data = self.blobs.get(key, b"")
            if not data and self.fetch is not None:
                try:
                    data = self.blobs[key] = self.fetch(key)
                except HubError:
                    data = b""
        elif kind in ("source", "last"):
            data = self.source if kind == "source" else self.last
        else:
            data = self.preview if kind == "preview" else self.pictures.get(key, b"")
        image = QImage()
        image.loadFromData(data)
        return image


class VisualView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.provider = PictureProvider()
        self.provider.fetch = self._picture
        self._prompt = ""
        self._shape = "square"
        self._quality = "fast"
        self._models: list[dict[str, Any]] = []
        self._default = ""
        self._loaded = False
        self._job: dict[str, Any] = {}  # the picture shown
        self._step = (0, 0)
        self._preview = 0  # bumped with each preview: QML asks the provider again
        self._recent: list[dict[str, Any]] = []
        self._fetching: set[str] = set()
        self._source: dict[str, str] = {}  # «По картинке»: {from_job | from_blob | from_png: value, shown: url}
        self._shown = 0  # bumped with each source: QML asks the provider again
        self._strength = "some"
        self._mode = "picture"  # or video (29.8б)
        self._videos: list[dict[str, Any]] = []
        self._length = "2"
        self._model = ""  # the model chosen in the Studio (48.7б): "" - «Авто», as «Быстрее ↔ Качественнее» says
        self._video_mode = "text"  # by words, by a picture, by two frames (48.7б)
        self._last: dict[str, str] = {}  # a video's last frame: {last_job | last_blob | last_png: value, shown: url}
        self._shown_last = 0
        self._progress: dict[str, tuple[int, int]] = {}  # each job's step of its steps (48.7в: the queue)
        self._gallery = "all"  # the gallery: all, pictures or videos (48.7в)

    # ================= for QML =================

    @Property(str, notify=changed)
    def prompt(self) -> str:
        return self._prompt

    @Property(list, notify=changed)
    def shapes(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in SHAPE_NAMES]

    @Property(str, notify=changed)
    def shape(self) -> str:
        return self._shape

    @Property(list, notify=changed)
    def qualities(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in QUALITY]

    @Property(str, notify=changed)
    def quality(self) -> str:
        return self._quality

    @Property(bool, notify=changed)
    def choice(self) -> bool:
        """«Быстрее ↔ Качественнее» only when there is something to choose between - a video: its family's size or
        its larger one; pictures: «Авто» among several (48.7б: a model chosen - nothing to choose)."""
        return self._mode == "video" or (not self._model and len(self._models) > 1)

    # ---- the Studio's choices (48.7б) ----

    def _kind(self) -> list[dict[str, Any]]:
        return self._videos if self._mode == "video" else self._models

    def _chosen(self) -> dict[str, Any] | None:
        """The model chosen by name, of this mode's kind; None - «Авто»."""
        return next((m for m in self._kind() if self._model and m["model_id"] == self._model), None)

    def _video_model(self) -> dict[str, Any] | None:
        """The video's model: the one chosen, or (Авто) the first that films as asked."""
        chosen = self._chosen()
        if chosen is not None:
            return chosen
        return next((m for m in self._videos if self._video_mode in (m.get("modes") or ["text"])),
                    self._videos[0] if self._videos else None)  # fmt: skip

    @Property(list, notify=changed)
    def modelChoices(self) -> list:
        """The models of the mode: «Авто» first, then each installed."""
        auto = tr("как скажет «Быстрее ↔ Качественнее»") if self._mode != "video" else tr("первая, что так снимает")
        return [{"id": "", "name": tr("Авто"), "note": auto, "selected": not self._chosen()},
                *({"id": m["model_id"], "name": m["name"], "selected": m["model_id"] == self._model,
                   "note": " · ".join(tr(whole) for k, _n, whole in VIDEO_MODES if k in (m.get("modes") or ["text"]))
                   if self._mode == "video" else ""} for m in self._kind())]  # fmt: skip

    @Property(str, notify=changed)
    def modelName(self) -> str:
        chosen = self._chosen()
        return chosen["name"] if chosen else tr("Авто")

    @Property(list, notify=changed)
    def videoModes(self) -> list:
        """How a video may be made: as the chosen model films - «Авто»: as one of them does."""
        chosen = self._chosen() if self._mode == "video" else None
        allowed = {k for m in ([chosen] if chosen else self._videos) for k in (m.get("modes") or ["text"])}
        return [{"id": k, "name": tr(n)} for k, n, _whole in VIDEO_MODES if k in allowed]

    @Property(str, notify=changed)
    def videoMode(self) -> str:
        return self._video_mode

    @Property(bool, notify=changed)
    def hasLast(self) -> bool:
        return bool(self._last)

    @Property(str, notify=changed)
    def lastSource(self) -> str:
        return self._last.get("shown", "")

    @Property(list, notify=changed)
    def modes(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in MODES]

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._mode

    @Property(bool, notify=changed)
    def hasVideo(self) -> bool:
        return bool(self._videos)

    @Property(list, notify=changed)
    def lengths(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in LENGTHS]

    @Property(str, notify=changed)
    def length(self) -> str:
        return self._length

    @Property(str, notify=changed)
    def videoSource(self) -> str:
        """The video shown (29.8б): the hub's file - AnimatedImage plays a file, not a provider's picture."""
        path = self._video_file(self._job)
        return QUrl.fromLocalFile(str(path)).toString() if path is not None else ""

    @Slot(str, result=str)
    def blobUrl(self, blob: str) -> str:
        """A video a chat holds (29.8б), as a file: the hub keeps it in data/blobs on this computer."""
        return QUrl.fromLocalFile(str(self.app.paths.blobs / blob)).toString() if blob else ""

    @Property(bool, notify=changed)
    def hasSource(self) -> bool:
        return bool(self._source)

    @Property(str, notify=changed)
    def sourceSource(self) -> str:
        return self._source.get("shown", "")

    @Property(list, notify=changed)
    def strengths(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n, _v in STRENGTHS]

    @Property(str, notify=changed)
    def strength(self) -> str:
        return self._strength

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return self._loaded

    @Property(bool, notify=changed)
    def hasModels(self) -> bool:
        return bool(self._models)

    @Property(str, notify=changed)
    def modelText(self) -> str:
        if self._mode == "video":
            model = self._video_model()
            if model is None:
                return ""
            if self._video_mode != "text":  # by a picture (48.7б): the picture's shape - the hub sizes it
                how = tr("по двум кадрам") if self._video_mode == "frames" else tr("по картинке")
                return tr("{name} · {how} · {seconds} с", name=model["name"], how=how, seconds=self._length)
            width, height = video_size(self._shape, self._quality == "quality",
                                       clip_of(model.get("clip")))  # the family's own (48.3)  # fmt: skip
            return tr("{name} · {width} × {height} · {seconds} с", name=model["name"], width=width,
                      height=height, seconds=self._length)  # fmt: skip
        model, width, height, why = self._plan()
        if model is None:
            return why
        if self._source:
            return tr("{name} · по картинке", name=model["name"])
        text = tr("{name} · {width} × {height}", name=model["name"], width=width, height=height)
        return tr("{text} — {why}", text=text, why=why) if why else text

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._job.get("status") in ("queued", "running")

    @Property(str, notify=changed)
    def statusText(self) -> str:
        status, how = self._job.get("status", ""), self._how()
        if not status:
            return ""
        if status == "running" and self._job.get("kind") == "video":
            return tr("снимаю видео…")
        if status == "running" and self._step[1]:
            text = tr("рисую: шаг {step} из {steps}", step=self._step[0], steps=self._step[1])
            return tr("{text} — {how}", text=text, how=how) if how else text
        if status == "running" and how:
            return tr("не вышло — пробую {how}", how=how)
        if status == "done":
            text = tr("готово за {time}", time=seconds_text(self._job.get("seconds")))
            return tr("{text} — {how}", text=text, how=how) if how else text
        if status == "failed":
            return tr("не вышло: {error}", error=self._job.get("error", ""))
        return tr(STATUS[status]) if status in STATUS else status

    @Property(float, notify=changed)
    def percent(self) -> float:
        return round(100 * self._step[0] / self._step[1], 1) if self._step[1] else 0.0

    @Property(str, notify=changed)
    def pictureSource(self) -> str:
        """What the big frame shows: the preview while it is drawn, then the picture."""
        job_id = self._job.get("id", "")
        if self._job.get("status") == "done" and job_id in self.provider.pictures:
            return f"image://pictures/job/{job_id}"
        if self.busy and self._preview:
            return f"image://pictures/preview/{self._preview}"
        return ""

    @Property(bool, notify=changed)
    def done(self) -> bool:
        if self._job.get("kind") == "video":
            return self._video_file(self._job) is not None
        return self._job.get("status") == "done" and self._job.get("id", "") in self.provider.pictures

    @Property(list, notify=changed)
    def recent(self) -> list:
        """The gallery: the latest pictures and videos done - all, or one kind (48.7в)."""
        out = []
        for j in self._recent:
            if j["status"] != "done" or self._gallery not in ("all", "video" if j.get("kind") == "video" else "image"):
                continue
            if (video := self._video_file(j)) is not None:  # its first frame (29.8б)
                out.append({"id": j["id"], "prompt": j["prompt"], "source": QUrl.fromLocalFile(str(video)).toString(),
                            "chosen": j["id"] == self._job.get("id"), "video": True})  # fmt: skip
            elif j["id"] in self.provider.pictures:
                out.append({"id": j["id"], "prompt": j["prompt"], "source": f"image://pictures/job/{j['id']}",
                            "chosen": j["id"] == self._job.get("id"), "video": False})  # fmt: skip
        return out

    @Property(bool, notify=changed)
    def shownVideo(self) -> bool:
        """Whether the job on the canvas is a video - not the mode chosen for the next one (48.7в)."""
        return self._job.get("kind") == "video"

    @Property(list, notify=changed)
    def galleryFilters(self) -> list:
        return [{"id": k, "name": tr(n)} for k, n in GALLERY]

    @Property(str, notify=changed)
    def gallery(self) -> str:
        return self._gallery

    @Property(list, notify=changed)
    def queue(self) -> list:
        """The jobs going on (48.7в) - the hub's queue, as they came: each how far it is; ✕ cancels it."""
        out = []
        for j in self._recent:
            if j["status"] not in ("queued", "running"):
                continue
            video = j.get("kind") == "video"
            step, steps = self._progress.get(j["id"], (0, 0))
            if j["status"] == "queued" and not steps:
                words = tr("в очереди")
            else:
                doing = tr("снимаю") if video else tr("рисую")
                words = tr("{doing}: шаг {step} из {steps}", doing=doing, step=step, steps=steps) if steps else doing
            out.append({"id": j["id"], "kind": "video" if video else "image", "prompt": j.get("prompt", ""),
                        "words": words, "percent": round(100.0 * step / steps, 1) if steps else 0.0,
                        "shown": j["id"] == self._job.get("id")})  # fmt: skip
        return out

    def _video_file(self, found: dict[str, Any]) -> Path | None:
        """A finished video's WebP: the hub's file on this computer (data/media)."""
        if found.get("kind") != "video" or found.get("status") != "done" or not found.get("file"):
            return None
        path = self.app.paths.media / found["file"]
        return path if path.is_file() else None

    def _plan(self) -> tuple[dict[str, Any] | None, int, int, str]:
        """What the next picture is drawn with: the model (the one that comes out here in place of the chosen), its
        size in this shape (no more pixels than come out here), and why it is not the chosen one as it is. From a
        source (29.6б): a model of several steps; its size - the hub's, by the source's shape."""
        chosen = self._chosen()  # the Studio's (48.7б); none - «Авто»
        if self._source:
            followers = [m for m in self._models if int(m.get("steps") or 0) >= 2]
            model = chosen if chosen in followers else pick_model(followers, self._default, self._quality)
            if model is None:
                return None, 0, 0, tr("По картинке рисуют модели в несколько шагов: поставьте FLUX.2-klein или "
                                      "Z-Image-Turbo")  # fmt: skip
            return model, 0, 0, ""
        model = chosen or pick_model(self._models, self._default, self._quality)
        if model is None:
            return None, 0, 0, ""
        why, wanted = "", model.get("instead")
        base = next((m for m in self._models if wanted and m.get("cpu_id") == wanted), None)
        if base is not None:  # the card draws nothing here: its block on the processor (29.4в)
            width, height = fit(*size_for(self._shape, base["size"]), CPU_PIXELS)
            why = tr("рисует процессор: видеокарта здесь не вышла") if base is model else tr(
                "{model} здесь не выходит — рисует процессор", model=model["name"])  # fmt: skip
            return {**base, "model_id": wanted}, width, height, why
        instead = next((m for m in self._models if m["model_id"] == wanted), None)
        if instead is not None:
            why, model = tr("{model} здесь не выходит", model=model["name"]), instead
        width, height = size_for(self._shape, model["size"])
        fitted = fit(width, height, int(model.get("pixels") or 0))
        if fitted != (width, height) and not why:
            why = tr("крупнее здесь не выходит")
        return model, fitted[0], fitted[1], why

    def _how(self) -> str:
        """How the picture shown went a rung lower (29.4б): its model or size against its first try; "" if it did
        not."""
        tries = self._job.get("tries") or []
        if not tries:
            return ""
        model_id, first = self._job.get("model_id"), tries[0]
        base = next((m for m in self._models if model_id and m.get("cpu_id") == model_id), None)
        if base is not None and model_id != first.get("model_id"):  # its block on the processor (29.4в)
            if base["model_id"] == first.get("model_id"):
                return tr("процессором")
            return tr("моделью {name} на процессоре", name=base["name"])
        if model_id != first.get("model_id"):
            name = next((m["name"] for m in self._models if m["model_id"] == model_id), model_id)
            return tr("моделью {name}", name=name)
        width, height = self._job.get("width"), self._job.get("height")
        if (width, height) != (first.get("width"), first.get("height")):
            return tr("меньшим размером, {width} × {height}", width=width, height=height)
        return ""

    def _picture(self, blob: str) -> bytes:
        client = self.app.client
        return client.visual_picture(blob) if client is not None else b""

    # ================= loading =================

    @Slot(str)
    def open(self, prompt: str) -> None:
        """The sheet opened - from the chip, with what was typed in the chat's input."""
        if prompt.strip():
            self._prompt = prompt.strip()
        self.changed.emit()
        self.load()

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def models(data: dict[str, Any]) -> None:
            self._models, self._default, self._loaded = data.get("models", []), data.get("default", ""), True
            self._videos = data.get("videos") or []
            if not self._videos:
                self._mode = "picture"
            if not self._chosen():  # a model gone (removed): «Авто» again (48.7б)
                self._model = ""
            self._fit_video_mode()
            self.changed.emit()

        def jobs(data: dict[str, Any]) -> None:
            self._recent = data.get("jobs", [])[:RECENT]
            for j in self._recent:
                if j["status"] == "done" and j.get("kind") != "video":  # a video plays from its file (29.8б)
                    self._fetch(j["id"])
            self.changed.emit()

        self.app._call(lambda: self.app.client.visual_models(), models, lambda _e: None)
        self.app._call(lambda: self.app.client.visual_jobs(RECENT), jobs, lambda _e: None)

    def _fetch(self, job_id: str) -> None:
        if job_id in self.provider.pictures or job_id in self._fetching:
            return
        self._fetching.add(job_id)

        def keep(data: bytes) -> None:
            self._fetching.discard(job_id)
            self.provider.pictures[job_id] = data
            self.changed.emit()

        def failed(_error: Exception) -> None:
            self._fetching.discard(job_id)

        self.app._call(lambda: self.app.client.visual_file(job_id), keep, failed)

    # ================= drawing =================

    @Slot(str)
    def setPrompt(self, prompt: str) -> None:
        self._prompt = prompt
        self.changed.emit()

    @Slot(str)
    def setShape(self, shape: str) -> None:
        self._shape = shape if shape in SHAPES else "square"
        self.changed.emit()

    @Slot(str)
    def setQuality(self, quality: str) -> None:
        self._quality = quality if quality in _TIER else "fast"
        self.changed.emit()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        wanted = mode if mode == "video" and self._videos else "picture"
        if wanted != self._mode:  # a picture model is no video model: «Авто» again (48.7б)
            self._model = ""
        self._mode = wanted
        self._fit_video_mode()
        self.changed.emit()

    @Slot(str)
    def setModel(self, model_id: str) -> None:
        """The Studio's model (48.7б): one installed of this mode's kind, or "" - «Авто»."""
        self._model = model_id if any(m["model_id"] == model_id for m in self._kind()) else ""
        self._fit_video_mode()
        self.changed.emit()

    @Slot(str)
    def setVideoMode(self, mode: str) -> None:
        """By words, by a picture, by two frames (48.7б) - as the model films."""
        if mode in {m["id"] for m in self.videoModes}:
            self._video_mode = mode
        if self._video_mode != "frames":
            self._last = {}
        self.changed.emit()

    def _fit_video_mode(self) -> None:
        """A video model that does not film as chosen: its first way, and no last frame unless by two frames."""
        allowed = [m["id"] for m in self.videoModes]
        if allowed and self._video_mode not in allowed:  # the nearest it films: two frames - by the first one
            near = {"frames": ("picture", "text"), "picture": ("frames", "text"), "text": ("picture", "frames")}
            self._video_mode = next((m for m in near[self._video_mode] if m in allowed), allowed[0])
        if self._video_mode != "frames":
            self._last = {}

    @Slot(str)
    def setLength(self, length: str) -> None:
        self._length = length if length in {k for k, _n in LENGTHS} else "2"
        self.changed.emit()

    @Slot()
    def draw(self) -> None:
        self._start(seed=-1)

    @Slot()
    def again(self) -> None:
        """«Ещё вариант»: the same description, a new seed."""
        self._start(seed=-1)

    def _start(self, *, seed: int) -> None:
        if self._mode == "video":
            self._film(seed=seed)
            return
        model, width, height, _why = self._plan()
        if not self._prompt.strip() or model is None:
            return
        spec = {"prompt": self._prompt.strip(), "model": model["model_id"], "seed": seed}
        if self._source:  # its shape sizes it, in the hub (29.6б)
            spec |= {k: v for k, v in self._source.items() if k != "shown"}
            spec["strength"] = next(v for k, _n, v in STRENGTHS if k == self._strength)
        else:
            spec |= {"width": width, "height": height}
        self._send(spec, video=False)

    def _send(self, spec: dict[str, Any], *, video: bool) -> None:
        """A job to the hub: on the canvas - or (48.7в) one already drawn there, into the hub's queue, the canvas
        kept; the queue shows it."""
        queued = self.busy
        if self.app.currentId:
            spec["conversation_id"] = self.app.currentId
        if not queued:
            self._job, self._step = {"status": "queued", "prompt": spec["prompt"],
                                     **({"kind": "video"} if video else {})}, (0, 0)  # fmt: skip
            self.changed.emit()

        def started(result: dict[str, Any]) -> None:
            job = result.get("job") or {}
            self._keep(job)  # among the latest as it came: the queue keeps the hub's order
            if not queued:
                self._job = job
            self.changed.emit()

        def failed(error: Exception) -> None:
            if not queued:
                self._job = {}
            self.app.show_error(tr("Не снять видео: {error}", error=error) if video else tr(
                "Не нарисовать: {error}", error=error))  # fmt: skip
            self.changed.emit()

        if video:
            self.app.call_or_fail(lambda: self.app.client.visual_video(spec), started, failed)
        else:
            self.app.call_or_fail(lambda: self.app.client.visual_draw(spec), started, failed)

    def _keep(self, job: dict[str, Any]) -> None:
        """A job among the latest: a new one first, a known one where it was (48.7в: the queue keeps its order)."""
        if not job.get("id"):
            return
        at = next((i for i, j in enumerate(self._recent) if j["id"] == job["id"]), None)
        if at is None:
            self._recent = [job, *self._recent][:RECENT]
        else:
            self._recent[at] = job

    def _film(self, *, seed: int) -> None:
        """A video (29.8б): the description, the shape, how long, «Качественнее» - the family's larger size; (48.7б)
        its model as chosen, by a picture - the first frame as «По картинке» takes one - and by two frames."""
        model = self._video_model()
        if not self._prompt.strip() or model is None:
            return
        if self._video_mode != "text" and not self._source:
            self.app.flash(tr("Выберите первый кадр"), ms=2500)
            return
        spec = {"prompt": self._prompt.strip(), "model": model["model_id"], "shape": self._shape,
                "seconds": float(self._length), "hd": self._quality == "quality", "seed": seed}  # fmt: skip
        if self._video_mode != "text":  # the first frame; its shape - the video's, in the hub
            spec |= {k: v for k, v in self._source.items() if k != "shown"}
        if self._video_mode == "frames" and self._last:
            spec |= {k: v for k, v in self._last.items() if k != "shown"}
        self._send(spec, video=True)

    # ================= «По картинке» (29.6б) =================

    def _use(self, kind: str, value: str, data: bytes | None = None, shown: str = "") -> None:
        if data is not None:
            self.provider.source, self._shown = data, self._shown + 1
            shown = f"image://pictures/source/{self._shown}"
        self._source = {kind: value, "shown": shown}
        if self._mode == "video" and self._video_mode == "text":  # a first frame chosen: a video by it (48.7б)
            self.setVideoMode("picture")
        self.changed.emit()

    @Slot()
    def fromThis(self) -> None:
        """«По этой картинке»: the picture shown is the source."""
        job_id = self._job.get("id", "")
        data = self.provider.pictures.get(job_id)
        if data and self._job.get("status") == "done":
            self._use("from_job", job_id, data)

    @Slot(str)
    def fromChat(self, blob: str) -> None:
        """A picture in a chat (a click on it there) is the source."""
        if blob:
            self._use("from_blob", blob, shown=f"image://pictures/blob/{blob}")

    @Slot(str)
    def chooseSource(self, url: str) -> None:
        """«Выбрать картинку…»: any picture Qt reads, sent as PNG."""
        data = self._png_of(url)
        if data is not None:
            self._use("from_png", base64.b64encode(data).decode(), data)

    @Slot()
    def clearSource(self) -> None:
        self._source = {}
        self.changed.emit()

    @Slot(str)
    def chooseLast(self, url: str) -> None:
        """A video's last frame (48.7б): any picture Qt reads, sent as PNG."""
        data = self._png_of(url)
        if data is not None:
            self.provider.last, self._shown_last = data, self._shown_last + 1
            self._last = {"last_png": base64.b64encode(data).decode(),
                          "shown": f"image://pictures/last/{self._shown_last}"}  # fmt: skip
            self.changed.emit()

    @Slot()
    def clearLast(self) -> None:
        self._last = {}
        self.changed.emit()

    def _png_of(self, url: str) -> bytes | None:
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        image = QImage(path)
        if image.isNull():
            self.app.show_error(tr("Не открыть картинку: {path}", path=path))
            return None
        array = QByteArray()
        buffer = QBuffer(array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(array.data())

    @Slot(str)
    def setStrength(self, strength: str) -> None:
        self._strength = strength if strength in {k for k, _n, _v in STRENGTHS} else "some"
        self.changed.emit()

    @Slot()
    def stop(self) -> None:
        job_id = self._job.get("id")
        if job_id and self.busy:
            self.app._call(lambda: self.app.client.visual_cancel(job_id), lambda _r: None, lambda _e: None)

    @Slot(str)
    def cancelJob(self, job_id: str) -> None:
        """✕ of a job in the queue (48.7в)."""
        if any(j["id"] == job_id and j["status"] in ("queued", "running") for j in self._recent):
            self.app._call(lambda: self.app.client.visual_cancel(job_id), lambda _r: None, lambda _e: None)

    @Slot(str)
    def setGallery(self, kind: str) -> None:
        self._gallery = kind if kind in {k for k, _n in GALLERY} else "all"
        self.changed.emit()

    @Slot(str)
    def pick(self, job_id: str) -> None:
        """One of the latest pictures to the big frame."""
        found = next((j for j in self._recent if j["id"] == job_id), None)
        if found and not self.busy:
            self._job, self._prompt = found, found["prompt"]
            self.changed.emit()

    @Slot(str)
    def saveAs(self, url: str) -> None:
        video = self._video_file(self._job)  # a video: its WebP as it is (29.8б), or MP4 from the hub (29.8в)
        target = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if video is not None and target and not target.lower().endswith(".webp"):
            self._save_mp4(self._job["id"], target if target.lower().endswith(".mp4") else target + ".mp4")
            return
        data = video.read_bytes() if video is not None else self.provider.pictures.get(self._job.get("id", ""))
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if not data or not path:
            return
        suffix = ".webp" if video is not None else ".png"
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            self.app.show_error(tr("Не сохранить: {error}", error=exc.strerror or exc))
            return
        self.app.flash(tr("Сохранено: {path}", path=path))

    def _save_mp4(self, job_id: str, path: str) -> None:
        """MP4 (29.8в): made in the hub with ffmpeg - the first time it fetches ffmpeg, ~86 MB."""
        self.app.flash(tr("Готовлю MP4… В первый раз AFO скачает ffmpeg (~86 МБ)"))

        def keep(data: bytes) -> None:
            try:
                Path(path).write_bytes(data)
            except OSError as exc:
                self.app.show_error(tr("Не сохранить: {error}", error=exc.strerror or exc))
                return
            self.app.flash(tr("Сохранено: {path}", path=path))

        def failed(error: Exception) -> None:
            self.app.show_error(tr("Не сделать MP4: {error}", error=error))

        self.app._call(lambda: self.app.client.visual_mp4(job_id), keep, failed)

    @Slot()
    def openFolder(self) -> None:
        """The folder the hub keeps its pictures in (data/media)."""
        folder = self.app.paths.media
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ================= the hub's events =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "visual.progress":
            step = (int(payload.get("step") or 0), int(payload.get("steps") or 0))
            self._progress[str(payload.get("id") or "")] = step  # the queue's (48.7в)
            if payload.get("id") != self._job.get("id"):
                self.changed.emit()
                return
            self._step = step
            try:
                preview = base64.b64decode(payload.get("preview") or "")
            except ValueError:  # binascii.Error is one
                preview = b""
            if preview:
                self.provider.preview, self._preview = preview, self._preview + 1
            self.changed.emit()
            return
        job = payload.get("job") or {}
        if not job.get("id"):
            return
        self._keep(job)
        if job["status"] not in ("queued", "running"):
            self._progress.pop(job["id"], None)
        if job["id"] == self._job.get("id"):
            self._job = job
            if job["status"] != "running":
                self._step = (0, 0)
        if job["status"] == "done" and job.get("kind") != "video":
            self._fetch(job["id"])
        self.changed.emit()
