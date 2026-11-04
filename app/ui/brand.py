"""AFO's icon: the user's logo (2026-11-04, a chat bubble with </+>; `logo.png`, the white around the bubble made
transparent) - the installer, its shortcuts, the window's taskbar button, the tray and the window's corner. Each size
is made from it by halving (one step from 512 to 16 would lose the strokes). Needs the Qt application."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap

LOGO = Path(__file__).with_name("logo.png")  # 512x512, transparent around the bubble
SIDES = (16, 24, 32, 48, 64, 128, 256)  # what Windows asks an .ico for: lists, the taskbar, the desktop, large views


def picture(side: int) -> QImage:
    """The icon at `side` pixels."""
    image = QImage(str(LOGO)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    smooth, keep = Qt.TransformationMode.SmoothTransformation, Qt.AspectRatioMode.KeepAspectRatio
    while image.width() > side * 2:
        image = image.scaled(image.width() // 2, image.height() // 2, keep, smooth)
    return image.scaled(side, side, keep, smooth)


def png(side: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    picture(side).save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def ico(sides: tuple[int, ...] = SIDES) -> bytes:
    """An .ico of PNG frames, one per size (Windows reads PNG frames at every size since Vista)."""
    frames = [png(side) for side in sides]
    entries, offset = b"", 6 + 16 * len(frames)
    for side, frame in zip(sides, frames, strict=True):
        entries += struct.pack("<BBBBHHII", side % 256, side % 256, 0, 0, 1, 32, len(frame), offset)
        offset += len(frame)
    return struct.pack("<HHH", 0, 1, len(frames)) + entries + b"".join(frames)


def icon() -> QIcon:
    """The window's and the tray's icon: the taskbar button, Alt+Tab, the notification area."""
    made = QIcon()
    for side in SIDES:
        made.addPixmap(QPixmap.fromImage(picture(side)))
    return made
