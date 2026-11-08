"""Long words do not break the window (stage 37): a name from the data wraps or ends in «…» inside what holds it."""

import pytest
from PySide6.QtCore import QMetaObject, QObject, QRectF

from common.paths import data_paths
from tests.test_ui import FakeClient, item_named, opened, qapp, wait_until  # noqa: F401 - the qapp fixture

LONG = "Автономный агент: открой телеграм и жди ответа от чатов в папке личные " * 3


@pytest.fixture
def quick(qapp):  # noqa: F811
    from app.ui.main import QuickApp

    window = QuickApp(qapp, data_paths().ensure(), connect=False)
    window.controller.attach(FakeClient(), start_events=False)
    wait_until(lambda: not window.controller.bg.busy)
    yield window
    window.close()


def box(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def ink(text):
    """Where a Text really paints: its own width, or further when the words do not fit in it."""
    found = box(text)
    found.setWidth(max(found.width(), text.property("paintedWidth")))
    return found


def texts_in(item):
    found = []
    for child in item.childItems():
        if child.isVisible() and child.metaObject().className().startswith("QQuickText"):
            found.append(child)
        found += texts_in(child)
    return found


def open_popup(quick, popup):
    QMetaObject.invokeMethod(popup, "open")
    wait_until(lambda: popup.property("opened"))
    quick.window.grabWindow()


def test_a_question_keeps_a_long_title_and_its_answers_inside_the_card(quick):
    dialog = quick.window.findChild(QObject, "confirmDialog")
    quick.controller._ask(
        LONG, "Расходы останутся.", "Удалить навсегда и без возврата", lambda *_: None, other="Не сохранять и выйти"
    )
    wait_until(lambda: dialog.property("opened"))
    quick.window.grabWindow()
    card = box(dialog.property("background"))
    found = texts_in(dialog.property("contentItem"))
    assert len(found) == 5  # the title, the text, three answers
    for text in found:
        assert card.contains(ink(text)), text.property("text")[:30]


def test_a_sheets_long_title_stops_before_its_close_button(quick):
    sheet = quick.window.findChild(QObject, "visualSheet")
    sheet.setProperty("title", LONG)
    open_popup(quick, sheet)
    body = sheet.property("contentItem")
    title = next(t for t in texts_in(body) if t.property("text") == LONG)
    assert title.property("truncated")
    assert ink(title).right() <= box(item_named(body, "sheetClose")).left()


def test_a_chip_with_a_long_name_stays_in_its_row_and_comes_back_when_it_is_short(quick):
    chip = item_named(quick.window.contentItem(), "chatModeChip")
    chip.setProperty("text", LONG)
    quick.window.grabWindow()
    assert box(chip.parentItem()).contains(box(chip))
    label = texts_in(chip)[0]
    assert label.property("truncated") and box(chip).contains(ink(label))
    chip.setProperty("text", "Да")
    quick.window.grabWindow()
    assert chip.width() < 120 and not label.property("truncated")


def test_a_button_given_a_limit_ends_its_long_label_in_dots(quick):
    opened(quick.controller)
    wait_until(lambda: quick.controller.approvals.count == 1)
    quick.window.grabWindow()
    button = item_named(quick.window.contentItem(), "approveChat")
    button.setProperty("text", LONG)
    quick.window.grabWindow()
    assert box(button.parentItem()).contains(box(button))
    label = texts_in(button)[0]
    assert label.property("truncated") and box(button).contains(ink(label))
