"""Windows snapping for the frameless window (app/ui/snap.py): the window's WM_NCCALCSIZE is answered "all client
area", other windows and messages are left to Qt, and a maximized window is kept inside the screen. The messages are
built in memory; no window is shown."""

import ctypes
import sys
from ctypes import wintypes

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from app.ui import snap  # noqa: E402


def message(hwnd, kind, wparam=1, lparam=0):
    msg = wintypes.MSG()
    msg.hWnd, msg.message, msg.wParam, msg.lParam = hwnd, kind, wparam, lparam
    return msg


def test_only_the_windows_frame_calculation_is_answered():
    frame = snap.SnapFrame(0x1234)
    params = snap.NCCALCSIZE_PARAMS()
    own = message(0x1234, snap.WM_NCCALCSIZE, lparam=ctypes.addressof(params))
    assert frame.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(own)) == (True, 0)
    other_window = message(0x9999, snap.WM_NCCALCSIZE)
    assert frame.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(other_window)) == (False, 0)
    other_message = message(0x1234, 0x0084)  # WM_NCHITTEST: Qt's, the QML edges resize
    assert frame.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(other_message)) == (False, 0)
    no_rect = message(0x1234, snap.WM_NCCALCSIZE, wparam=0)  # the other form of the message: Windows' default
    assert frame.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(no_rect)) == (False, 0)
    assert frame.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(own)) == (False, 0)


def test_a_maximized_window_stays_inside_the_screen():
    rect = wintypes.RECT(-8, -8, 1928, 1048)  # what Windows proposes for a maximized 1920x1040 work area
    snap.maximized_inset(rect, 8, 8)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (0, 0, 1920, 1040)
