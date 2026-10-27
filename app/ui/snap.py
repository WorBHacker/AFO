"""Windows' own window behaviour for the frameless window: snapping to the screen's edges and corners, maximizing by
dragging to the top, Win+arrows, Snap Assist.

Windows does these only for a window with a sizing frame and a caption, and a frameless Qt window has neither. So the
window gets those styles back, and the frame they would draw is taken away again: on WM_NCCALCSIZE the whole window is
client area, and it looks as before - the title bar and the edges are still the window's own (QML). Maximized, the
client area is kept inside the screen: Windows places a maximized window's frame past the screen's edges.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtGui import QGuiApplication, QWindow

log = logging.getLogger("app.ui.snap")

WM_NCCALCSIZE = 0x0083
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
SNAP_STYLES = WS_THICKFRAME | WS_CAPTION | WS_SYSMENU | WS_MAXIMIZEBOX | WS_MINIMIZEBOX
SWP_FRAME_ONLY = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010  # FRAMECHANGED, NOSIZE, NOMOVE, NOZORDER, NOACTIVATE
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


def maximized_inset(rect: wintypes.RECT, frame_x: int, frame_y: int) -> None:
    """A maximized window's rectangle reaches past the screen by its frame on every side: bring it back in."""
    rect.left += frame_x
    rect.top += frame_y
    rect.right -= frame_x
    rect.bottom -= frame_y


class SnapFrame(QAbstractNativeEventFilter):
    """Answers WM_NCCALCSIZE for one window: all of it is client area (inset when maximized)."""

    def __init__(self, hwnd: int):
        super().__init__()
        self.hwnd = hwnd
        self._user32 = ctypes.windll.user32

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if (msg.hWnd or 0) != self.hwnd or msg.message != WM_NCCALCSIZE or not msg.wParam:
            return False, 0
        if self._user32.IsZoomed(self.hwnd):
            dpi = self._user32.GetDpiForWindow(self.hwnd)
            padded = self._user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
            params = NCCALCSIZE_PARAMS.from_address(msg.lParam)
            maximized_inset(params.rgrc[0], self._user32.GetSystemMetricsForDpi(SM_CXFRAME, dpi) + padded,
                            self._user32.GetSystemMetricsForDpi(SM_CYFRAME, dpi) + padded)  # fmt: skip
        return True, 0  # no frame: the client area is the window


def install(app: QGuiApplication, window: QWindow) -> SnapFrame | None:
    """Gives the window Windows' snapping; None elsewhere or when the system refuses."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = int(window.winId())
        frame = SnapFrame(hwnd)
        app.installNativeEventFilter(frame)  # first, so the frame never shows
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | SNAP_STYLES)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAME_ONLY)  # the new styles take effect, frame recomputed
        return frame
    except (OSError, AttributeError) as exc:
        log.info("no Windows snapping for the window (%s)", exc)
        return None
