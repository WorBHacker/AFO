"""AFO's icon (step 10.4c): the name on the accent's rounded square - the installer, its shortcuts and the window's
taskbar button. Drawn rather than kept as a file: one look at every size, and nothing to keep in step by hand.
Drawing needs the Qt application (its fonts)."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPixmap

ACCENT = "#4f8cff"  # the window's accent, as on the tray icon
SIDES = (16, 24, 32, 48, 64, 128, 256)  # what Windows asks an .ico for: lists, the taskbar, the desktop, large views
FONT = Path(__file__).with_name("fonts") / "Inter-Bold.ttf"
_family: list[str] = []


def _font() -> str:
    if not _family:
        found = QFontDatabase.addApplicationFont(str(FONT))
        _family.append((QFontDatabase.applicationFontFamilies(found) or ["Segoe UI"])[0] if found >= 0 else "Segoe UI")
    return _family[0]


def picture(side: int) -> QImage:
    """The icon at `side` pixels: «AFO», or «A» where three letters would not read (24 px and less)."""
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(QRectF(0, 0, side, side), side * 0.22, side * 0.22)
    text = "AFO" if side > 24 else "A"
    font = QFont(_font())
    font.setBold(True)
    font.setPixelSize(round(side * (0.36 if len(text) > 1 else 0.7)))
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return image


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
    """The window's icon: its taskbar button, Alt+Tab."""
    made = QIcon()
    for side in SIDES:
        made.addPixmap(QPixmap.fromImage(picture(side)))
    return made
