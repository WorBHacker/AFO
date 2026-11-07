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
from common.picture_sizes import CPU_PIXELS, SHAPES, fit, size_for

from ..hub_client import HubError

if TYPE_CHECKING:
    from .controller import Controller

SHAPE_NAMES = [("square", N_("Квадрат")), ("wide", N_("Широкая")), ("tall", N_("Высокая"))]
QUALITY = [("fast", N_("Быстрее")), ("quality", N_("Качественнее"))]
STATUS = {"queued": N_("в очереди"), "running": N_("рисую"), "done": N_("готово"), "failed": N_("не вышло"),
          "cancelled": N_("остановлено")}  # fmt: skip
RECENT = 12
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

    def requestImage(self, picture_id: str, size: QSize, requested: QSize) -> QImage:
        kind, _, key = picture_id.partition("/")
        if kind == "blob":
            data = self.blobs.get(key, b"")
            if not data and self.fetch is not None:
                try:
                    data = self.blobs[key] = self.fetch(key)
                except HubError:
                    data = b""
        elif kind == "source":
            data = self.source
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
        return self._job.get("status") == "done" and self._job.get("id", "") in self.provider.pictures

    @Property(list, notify=changed)
    def recent(self) -> list:
        return [{"id": j["id"], "prompt": j["prompt"], "source": f"image://pictures/job/{j['id']}",
                 "chosen": j["id"] == self._job.get("id")}
                for j in self._recent if j["status"] == "done" and j["id"] in self.provider.pictures]  # fmt: skip

    def _plan(self) -> tuple[dict[str, Any] | None, int, int, str]:
        """What the next picture is drawn with: the model (the one that comes out here in place of the chosen), its
        size in this shape (no more pixels than come out here), and why it is not the chosen one as it is. From a
        source (29.6б): a model of several steps; its size - the hub's, by the source's shape."""
        if self._source:
            followers = [m for m in self._models if int(m.get("steps") or 0) >= 2]
            model = pick_model(followers, self._default, self._quality)
            if model is None:
                return None, 0, 0, tr("По картинке рисуют модели в несколько шагов: поставьте FLUX.2-klein или "
                                      "Z-Image-Turbo")  # fmt: skip
            return model, 0, 0, ""
        model = pick_model(self._models, self._default, self._quality)
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
        model, width, height, _why = self._plan()
        if not self._prompt.strip() or model is None or self.busy:
            return
        spec = {"prompt": self._prompt.strip(), "model": model["model_id"], "seed": seed}
        if self._source:  # its shape sizes it, in the hub (29.6б)
            spec |= {k: v for k, v in self._source.items() if k != "shown"}
            spec["strength"] = next(v for k, _n, v in STRENGTHS if k == self._strength)
        else:
            spec |= {"width": width, "height": height}
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

    # ================= «По картинке» (29.6б) =================

    def _use(self, kind: str, value: str, data: bytes | None = None, shown: str = "") -> None:
        if data is not None:
            self.provider.source, self._shown = data, self._shown + 1
            shown = f"image://pictures/source/{self._shown}"
        self._source = {kind: value, "shown": shown}
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
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        image = QImage(path)
        if image.isNull():
            self.app.show_error(tr("Не открыть картинку: {path}", path=path))
            return
        array = QByteArray()
        buffer = QBuffer(array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        data = bytes(array.data())
        self._use("from_png", base64.b64encode(data).decode(), data)

    @Slot()
    def clearSource(self) -> None:
        self._source = {}
        self.changed.emit()

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
