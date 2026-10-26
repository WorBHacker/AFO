"""The app's line icons (24x24, round strokes), served to QML as image://icons/<name>?<rrggbb>.

Drawn here as tiny SVGs and tinted on request, so one icon serves every theme colour. Rendered by the CPU.
"""

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

STROKE = 'fill="none" stroke="{c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"'

PATHS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "send": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="{c}"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "chevron-down": '<path d="M7 10l5 5 5-5"/>',
    "chevron-right": '<path d="M10 7l5 5-5 5"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "sparkle": '<path d="M12 4l1.6 4.8L18.5 10.5 13.6 12.2 12 17l-1.6-4.8L5.5 10.5l4.9-1.7z"/>',
    "panel": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/>',
    "cpu": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>'
    '<path d="M10 3.5v3M14 3.5v3M10 17.5v3M14 17.5v3M3.5 10h3M3.5 14h3M17.5 10h3M17.5 14h3"/>',
    "cloud": '<path d="M7.5 18.5h9a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 6.6 11.2 3.7 3.7 0 0 0 7.5 18.5z"/>',
    "wallet": '<rect x="3.5" y="6.5" width="17" height="12" rx="2"/><path d="M3.5 10.5h17M15.5 14.5h2"/>',
    "sun": '<circle cx="12" cy="12" r="3.5"/>'
    '<path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
    "moon": '<path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z"/>',
    "min": '<path d="M7 12h10"/>',
    "max": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
    "close": '<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>',
    "dots": '<circle cx="6" cy="12" r="1" fill="{c}"/><circle cx="12" cy="12" r="1" fill="{c}"/>'
    '<circle cx="18" cy="12" r="1" fill="{c}"/>',
    "clip": '<path d="M15.5 7.5l-6.4 6.4a2 2 0 0 0 2.8 2.8l6.6-6.6a3.8 3.8 0 0 0-5.4-5.4'
    'L6.5 11.3a5.7 5.7 0 0 0 8.1 8.1l4-4"/>',
    "bolt": '<path d="M13 3.5L6 13.5h5.5L10.5 20.5 18 10.5h-5.5z"/>',
    "chat": '<path d="M5 5.5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5v-3.5H5a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/>',
    "diff": '<path d="M12 4v8M8 8h8M8 18h8"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2 2"/>',
    "layers": '<path d="M12 4l8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4M4 16l8 4 8-4"/>',
    "edit": '<path d="M5 19h4l10-10-4-4L5 15z"/>',
    "trash": '<path d="M5 7h14M10 11v6M14 11v6M7 7l1 12h8l1-12M10 7V4h4v3"/>',
    "power": '<path d="M12 4v8"/><path d="M7.5 7.5a6.5 6.5 0 1 0 9 0"/>',
    "refresh": '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5v4h-4"/>',
    "folder": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "folder-plus": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'
    '<path d="M12 10.5v5M9.5 13h5"/>',
    "file": '<path d="M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><path d="M13.5 3.5V9H19"/>',
    "branch": '<circle cx="7" cy="6" r="2"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="7" r="2"/>'
    '<path d="M7 8v8M17 9c0 4.5-10 3-10 7"/>',
    "commit": '<circle cx="12" cy="12" r="3.5"/><path d="M3.5 12h5M15.5 12h5"/>',
    "external": '<path d="M14 4.5h5.5V10M19.5 4.5L11 13M17 14v4.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4.5"/>',
    "back": '<path d="M14.5 6l-6 6 6 6"/>',
    "lock": '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    "activity": '<path d="M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4"/>',
    "terminal": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7.5 9.5l3 2.5-3 2.5M12.5 15h4"/>',
    "keyboard": '<rect x="3.5" y="6.5" width="17" height="11" rx="2"/>'
    '<path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M8 14h8"/>',
}


def svg(name: str, colour: str) -> str:
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
