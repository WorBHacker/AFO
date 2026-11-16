"""A video's picture in the window (48.12.7). Qt Multimedia's VideoOutput draws its frames with the graphics card's
shaders, and the window's software scene graph (D3: the CPU draws, VRAM stays with the models) draws none of them:
the live check saw the clip's first scene - the WebP, until the hub had made the MP4 - and then the background, the
time going on. This item is what the player shows on instead: its own QVideoSink takes the player's frames, and the
last one is painted with QPainter, its shape kept - the CPU converts and draws it, one frame held, none kept."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QRectF, QSize, Signal, Slot
from PySide6.QtGui import QPainter
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickPaintedItem

MODULE = "AFO.Video"  # QML: import AFO.Video


class VideoSurface(QQuickPaintedItem):
    """`MediaPlayer { videoOutput: surface.videoSink }`; `frameSize` - the clip's frame, for a view that takes its
    shape (a chat's card)."""

    frameSizeChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink = QVideoSink(self)
        self._frame = QVideoFrame()
        self._size = QSize()
        self._sink.videoFrameChanged.connect(self._take)

    @Slot(QVideoFrame)
    def _take(self, frame: QVideoFrame) -> None:
        self._frame = frame  # the last one only: the one before is let go
        size = frame.size() if frame.isValid() else QSize()
        if size != self._size:
            self._size = size
            self.frameSizeChanged.emit()
        self.update()

    def _sink_of(self) -> QObject:
        return self._sink

    def _frame_size(self) -> QSize:
        return self._size

    videoSink = Property(QObject, _sink_of, constant=True)
    frameSize = Property(QSize, _frame_size, notify=frameSizeChanged)

    def paint(self, painter: QPainter) -> None:
        if not self._frame.isValid():
            return
        image = self._frame.toImage()  # the frame's colours, converted by the CPU as it is painted
        if image.isNull():
            return
        box = self.boundingRect()
        scale = min(box.width() / image.width(), box.height() / image.height())
        width, height = image.width() * scale, image.height() * scale
        target = QRectF(box.x() + (box.width() - width) / 2, box.y() + (box.height() - height) / 2, width, height)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth())
        painter.drawImage(target, image)


_registered = False


def register() -> None:
    """Before the window's QML is loaded; once a process."""
    global _registered
    if not _registered:
        qmlRegisterType(VideoSurface, MODULE, 1, 0, "VideoSurface")
        _registered = True
