"""«Нарисовать» in the window (stage 29.5, D55): a description, the shape, «Быстрее ↔ Качественнее», the picture as it
is drawn - step and preview - and the result: «Ещё вариант», «Сохранить как…», the folder; the latest pictures
beside it. The chip «Нарисовать» in the chat's input opens it with what was typed.

The hub draws (hub/visual.py); this asks and shows. Pictures reach QML through an image provider
(image://pictures/...): the preview's JPEG from the progress events, a result's PNG fetched once and kept.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QSize, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtQuick import QQuickImageProvider

from common.i18n import N_, tr

if TYPE_CHECKING:
    from .controller import Controller

# a side of the model's own size (512 or 1024) and two shapes around it; each a multiple of 64
SHAPES = {"square": (1.0, 1.0), "wide": (1.3125, 0.75), "tall": (0.75, 1.3125)}
SHAPE_NAMES = [("square", N_("Квадрат")), ("wide", N_("Широкая")), ("tall", N_("Высокая"))]
QUALITY = [("fast", N_("Быстрее")), ("quality", N_("Качественнее"))]
STATUS = {"queued": N_("в очереди"), "running": N_("рисую"), "done": N_("готово"), "failed": N_("не вышло"),
          "cancelled": N_("остановлено")}  # fmt: skip
RECENT = 12
_TIER = {"fast": ("main", "light", "quality"), "quality": ("quality", "main", "light")}


def size_for(shape: str, side: int) -> tuple[int, int]:
    """The picture's width and height: the model's side in this shape, each a multiple of 64."""
    w, h = SHAPES.get(shape, SHAPES["square"])
    return max(64, round(side * w / 64) * 64), max(64, round(side * h / 64) * 64)


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
    """image://pictures/preview/<n> - the picture as it is drawn; image://pictures/job/<id> - a finished one."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.preview = b""
        self.pictures: dict[str, bytes] = {}

    def requestImage(self, picture_id: str, size: QSize, requested: QSize) -> QImage:
        kind, _, key = picture_id.partition("/")
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
        """«Быстрее ↔ Качественнее» only when there is something to choose between."""
        return len(self._models) > 1

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return self._loaded

    @Property(bool, notify=changed)
    def hasModels(self) -> bool:
        return bool(self._models)

    @Property(str, notify=changed)
    def modelText(self) -> str:
        model = pick_model(self._models, self._default, self._quality)
        if model is None:
            return ""
        width, height = size_for(self._shape, model["size"])
        return tr("{name} · {width} × {height}", name=model["name"], width=width, height=height)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._job.get("status") in ("queued", "running")

    @Property(str, notify=changed)
    def statusText(self) -> str:
        status = self._job.get("status", "")
        if not status:
            return ""
        if status == "running" and self._step[1]:
            return tr("рисую: шаг {step} из {steps}", step=self._step[0], steps=self._step[1])
        if status == "done":
            return tr("готово за {time}", time=seconds_text(self._job.get("seconds")))
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
        return self._job.get("status") == "done" and self._job.get("id", "") in self.provider.pictures

    @Property(list, notify=changed)
    def recent(self) -> list:
        return [{"id": j["id"], "prompt": j["prompt"], "source": f"image://pictures/job/{j['id']}",
                 "chosen": j["id"] == self._job.get("id")}
                for j in self._recent if j["status"] == "done" and j["id"] in self.provider.pictures]  # fmt: skip

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
            self.changed.emit()

        def jobs(data: dict[str, Any]) -> None:
            self._recent = data.get("jobs", [])[:RECENT]
            for j in self._recent:
                if j["status"] == "done":
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

    @Slot()
    def draw(self) -> None:
        self._start(seed=-1)

    @Slot()
    def again(self) -> None:
        """«Ещё вариант»: the same description, a new seed."""
        self._start(seed=-1)

    def _start(self, *, seed: int) -> None:
        model = pick_model(self._models, self._default, self._quality)
        if not self._prompt.strip() or model is None or self.busy:
            return
        width, height = size_for(self._shape, model["size"])
        spec = {"prompt": self._prompt.strip(), "model": model["model_id"], "width": width, "height": height,
                "seed": seed}  # fmt: skip
        if self.app.currentId:
            spec["conversation_id"] = self.app.currentId
        self._job, self._step = {"status": "queued", "prompt": spec["prompt"]}, (0, 0)
        self.changed.emit()

        def started(result: dict[str, Any]) -> None:
            self._job = result.get("job") or {}
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._job = {}
            self.app.show_error(tr("Не нарисовать: {error}", error=error))
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.visual_draw(spec), started, failed)

    @Slot()
    def stop(self) -> None:
        job_id = self._job.get("id")
        if job_id and self.busy:
            self.app._call(lambda: self.app.client.visual_cancel(job_id), lambda _r: None, lambda _e: None)

    @Slot(str)
    def pick(self, job_id: str) -> None:
        """One of the latest pictures to the big frame."""
        found = next((j for j in self._recent if j["id"] == job_id), None)
        if found and not self.busy:
            self._job, self._prompt = found, found["prompt"]
            self.changed.emit()

    @Slot(str)
    def saveAs(self, url: str) -> None:
        data = self.provider.pictures.get(self._job.get("id", ""))
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if not data or not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            self.app.show_error(tr("Не сохранить: {error}", error=exc.strerror or exc))
            return
        self.app.flash(tr("Сохранено: {path}", path=path))

    @Slot()
    def openFolder(self) -> None:
        """The folder the hub keeps its pictures in (data/media)."""
        folder = self.app.paths.media
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ================= the hub's events =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "visual.progress":
            if payload.get("id") != self._job.get("id"):
                return
            self._step = (int(payload.get("step") or 0), int(payload.get("steps") or 0))
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
        self._recent = [job, *[j for j in self._recent if j["id"] != job["id"]]][:RECENT]
        if job["id"] == self._job.get("id"):
            self._job = job
            if job["status"] != "running":
                self._step = (0, 0)
        if job["status"] == "done":
            self._fetch(job["id"])
        self.changed.emit()
