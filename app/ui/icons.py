"""The app's line icons (24x24, round strokes), served to QML as image://icons/<name>?<rrggbb>.

Lucide's icons (app/ui/icons, ISC licence in LICENSE-lucide.txt; release 1.57.0), as their SVG files are, by the
app's own names; a few of the app's own drawing where Lucide has none that fits (the window's minimize and maximize
glyphs, the filled stop square, a download arrow). Tinted on request, so one icon serves every theme colour. Rendered
by the CPU.
"""

import functools
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).with_name("icons")
WIDTH = "1.75"  # Lucide draws 2: a little lighter at the 14-16px the window shows
STROKE = f'fill="none" stroke="{{c}}" stroke-width="{WIDTH}" stroke-linecap="round" stroke-linejoin="round"'

LUCIDE = {
    "plus": "plus", "send": "arrow-up", "copy": "copy", "check": "check", "chevron-down": "chevron-down",
    "chevron-right": "chevron-right", "search": "search", "sliders": "sliders-horizontal", "sparkle": "sparkle",
    "panel": "panel-left", "cpu": "cpu", "cloud": "cloud", "wallet": "wallet", "sun": "sun", "moon": "moon",
    "close": "x", "dots": "ellipsis", "clip": "paperclip", "bolt": "zap", "chat": "message-square", "diff": "diff",
    "key": "key-round", "layers": "layers", "edit": "pencil", "trash": "trash", "power": "power",
    "refresh": "refresh-cw", "folder": "folder", "folder-plus": "folder-plus", "file": "file", "branch": "git-branch",
    "commit": "git-commit-horizontal", "external": "external-link", "back": "arrow-left", "lock": "lock",
    "pin": "pin", "activity": "activity", "terminal": "square-terminal", "keyboard": "keyboard",
    "outline": "list-tree", "shield": "shield-check", "agent": "bot",
}  # fmt: skip

PATHS = {
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="{c}"/>',
    "pause": '<rect x="7" y="6" width="3.5" height="12" rx="1" fill="{c}"/>'
    '<rect x="13.5" y="6" width="3.5" height="12" rx="1" fill="{c}"/>',
    "play": '<path d="M8.5 6v12l9.5-6z" fill="{c}"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/>'
    '<circle cx="12" cy="12" r="1.2" fill="{c}"/>',
    "flask": '<path d="M9 3.5h6"/><path d="M10 3.5v5.5L5 18a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3L14 9V3.5"/>'
    '<path d="M7.5 14.5h9"/>',
    "image": '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
    '<path d="m4.5 17.5 5-5 4 4 2-2 4 4"/>',
    "min": '<path d="M7 12h10"/>',
    "max": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
}

NAMES = frozenset(LUCIDE) | frozenset(PATHS)


@functools.cache
def _lucide(name: str) -> str:
    text = (ICON_DIR / f"{LUCIDE[name]}.svg").read_text(encoding="utf-8")
    return text.replace('stroke-width="2"', f'stroke-width="{WIDTH}"')


def svg(name: str, colour: str) -> str:
    if name in LUCIDE:
        return _lucide(name).replace("currentColor", colour)
    body = PATHS[name].replace("{c}", colour)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {STROKE.format(c=colour)}>{body}</svg>'


class IconProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, icon_id: str, size: QSize, requested: QSize) -> QImage:
        name, _, colour = icon_id.partition("?")
        side = requested.width() if requested.width() > 0 else 32
        image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(svg(name, "#" + (colour or "888888")).encode()))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
        return image
