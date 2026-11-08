"""Long words do not break the window (stage 37): a name from the data wraps or ends in «…» inside what holds it."""

import pytest
from PySide6.QtCore import QMetaObject, QObject, QRectF
from PySide6.QtTest import QTest

from common.paths import data_paths
from tests import test_ui as T
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


SPACED = " — очень длинное название, которое никто не сокращал и которое никак не помещается в одну строку окна"
SOLID = "-очень_длинное_имя_без_единого_пробела/и/ещё/вложенная/папка/которая/не/переносится/совсем.ext"
SPACED_KEYS = {"title", "name", "label", "goal", "summary", "description", "detail", "text"}
SOLID_KEYS = {"path", "exe", "folder", "command", "cwd", "branch", "root", "file"}
SAME = {"role", "status", "kind", "type", "id", "state"}


def stretch(value, key=None):
    if isinstance(value, dict):
        return {k: stretch(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [stretch(v, key) for v in value]
    if isinstance(value, str) and value and key not in SAME:
        return value + (SOLID if key in SOLID_KEYS else SPACED if key in SPACED_KEYS else "")
    return value


def plain(value):
    if isinstance(value, str):
        return value.replace(SOLID, "").replace(SPACED, "")
    if isinstance(value, list):
        return [plain(v) for v in value]
    return {k: plain(v) for k, v in value.items()} if isinstance(value, dict) else value


class LongNames:
    """The tests' client, every name and path it hands the window made long."""

    def __init__(self, real):
        object.__setattr__(self, "real", real)

    def __getattr__(self, name):
        found = getattr(self.real, name)
        if not callable(found):
            return found
        return lambda *args, **kwargs: stretch(found(*plain(list(args)), **plain(kwargs)))

    def __setattr__(self, name, value):
        setattr(self.real, name, value)


@pytest.fixture(params=[(1280, 820), (900, 560)], ids=["roomy", "narrowest"])
def long_named(qapp, request):  # noqa: F811
    from app.ui.main import QuickApp

    window = QuickApp(qapp, data_paths().ensure(), connect=False)
    real = T.FakeClient()
    real.run_list = [dict(T.AUTO_RUN), dict(T.RUN)]
    for program in real.program_list:
        program["fresh"] = False  # the question about a new program would cover the screens
    window.controller.attach(LongNames(real), start_events=False)
    wait_until(lambda: not window.controller.bg.busy)
    window.window.resize(*request.param)
    yield window, request.param[0]
    window.close()


def settle(quick):
    wait_until(lambda: not quick.controller.bg.busy)
    QTest.qWait(150)
    quick.window.grabWindow()


def scrolls_sideways(item):
    while item is not None:
        if item.inherits("QQuickFlickable") and item.property("contentWidth") > item.width() + 1:
            return True
        item = item.parentItem()
    return False


def outside(item, right, found):
    if not item.isVisible():
        return found
    is_text = item.metaObject().className().startswith("QQuickText") and item.property("text")
    if is_text and ink(item).right() > right + 1 and not scrolls_sideways(item):
        found.append(item.property("text")[:40])
    for child in item.childItems():
        outside(child, right, found)
    return found


def test_no_long_name_pushes_text_out_of_the_window(long_named):
    quick, width = long_named
    app, root = quick.controller, quick.window.contentItem()

    def show(step):
        step()
        settle(quick)
        assert outside(root, width, []) == []

    show(app.newChat)
    show(lambda: app.openChat("c1"))
    show(lambda: app.openChat("ct"))
    label = item_named(root, "toolLabel")
    assert ink(label).right() <= box(label.parentItem()).right() - 30  # the status icon has its place
    show(lambda: app.setMode("projects"))
    show(lambda: app.projects.openProject("p1"))
    name, ports = item_named(root, "procName"), item_named(root, "procPorts")
    assert ink(name).right() <= box(ports).left()
    show(lambda: app.setMode("agent"))
    show(app.autonomy_view.newTask)
    show(lambda: app.autonomy_view.select("auto1"))


def test_the_status_bar_gives_way_on_the_left_and_keeps_its_right_side(long_named):
    quick, width = long_named
    root = quick.window.contentItem()
    settle(quick)
    name, right = item_named(root, "statusModel"), item_named(root, "statusRight")
    assert ink(name).right() <= box(right).left()
    assert box(right).right() <= width


def test_the_search_box_stays_clear_of_the_buttons_in_the_title_bar(long_named):
    quick, _ = long_named
    root = quick.window.contentItem()
    settle(quick)
    search, buttons = item_named(root, "titleSearch"), item_named(root, "titleButtons")
    assert box(search).right() <= box(buttons).left()
    for text in texts_in(search):
        assert box(search).contains(ink(text)), text.property("text")


def test_no_long_name_runs_past_any_sheet(long_named):
    quick, _ = long_named
    settle(quick)
    drawers = [o for o in quick.window.findChildren(QObject) if o.inherits("QQuickDrawer")]
    assert len(drawers) > 15
    for sheet in drawers:
        name = sheet.metaObject().className().split("_QML")[0]
        if name == "HistorySheet":  # opened for one file, by the editor
            continue
        open_popup(quick, sheet)
        settle(quick)
        edge = box(sheet.property("background")).right()
        assert outside(sheet.property("contentItem"), edge, []) == [], name
        QMetaObject.invokeMethod(sheet, "close")
        wait_until(lambda s=sheet: not s.property("visible"))
    assert quick.warnings == []  # the limits above make no binding loops
