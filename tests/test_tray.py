"""Stage 43: the tray and the graphics card for models - one window, the tray, its menu, Windows' login."""

from common.paths import data_paths
from tests.test_ui import qapp  # noqa: F401 - the qapp fixture


def test_a_start_in_the_tray_shows_no_window_until_asked(qapp):  # noqa: F811
    """43.1: `--tray` (Windows' login) starts AFO without its window - the tray only; the window comes when asked (a
    second start, the tray's «Открыть AFO»), as it was left: maximized stays maximized."""
    from app.ui.main import QuickApp

    paths = data_paths().ensure()
    quick = QuickApp(qapp, paths, connect=False, hidden=True)
    try:
        assert not quick.window.isVisible()
        quick.show_window()
        assert quick.window.isVisible()
    finally:
        quick.close()
