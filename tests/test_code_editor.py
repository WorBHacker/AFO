"""The window's code editor (stage 14.2a, D47): a file's text edited where it is shown - the indent of the file kept
by Enter, Tab and Shift+Tab (each one undo step), Ctrl+S through the hub's save (14.1), «не сохранено», the file
changing on the disk while it is open, the question before unsaved edits are left - and in the real QML window. Its
colours (14.2b): the whole text lexed once off the window's thread, laid on line by line, the screen's first."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication as QApplication
from PySide6.QtGui import QKeyEvent, QTextCursor, QTextDocument

from app import palette
from app.ui import editor, highlighter
from common.paths import data_paths
from tests.test_ui import FILES, FakeClient, app, qapp, sha, wait_until  # noqa: F401 - the app and qapp fixtures


class Quick(QObject):
    """What CodeEditor.qml hands over (its TextEdit's textDocument), for a test without the window."""

    def __init__(self, document: QTextDocument):
        super().__init__()
        self._document = document

    def textDocument(self) -> QTextDocument:
        return self._document


def typed(document: QTextDocument, text: str, at: int | None = None) -> None:
    cursor = QTextCursor(document)
    cursor.setPosition(document.characterCount() - 1 if at is None else at)
    cursor.insertText(text)


def test_the_files_own_indent_and_one_undo_step(qapp):  # noqa: F811
    assert editor.indent_unit("def f():\n    return 1\n") == "    "
    assert editor.indent_unit("a:\n  b:\n    c\n") == "  "
    assert editor.indent_unit("x {\n\ty\n}\n") == "\t" and editor.indent_unit("plain\ntext\n") == "    "
    assert editor.next_indent("    if x:", "    ") == "        " and editor.next_indent("    y = 1", "    ") == "    "
    assert editor.next_indent("\tcall(", "\t") == "\t\t"

    document = QTextDocument()
    document.setPlainText("def f():\n    return 1")
    end = len("def f():")
    assert editor.newline(document, end, end, "    ") == end + 5
    assert document.toPlainText() == "def f():\n    \n    return 1"
    document.undo()
    assert document.toPlainText() == "def f():\n    return 1"  # Enter and its indent: one step

    document.setPlainText("a\n\nb\n  c")
    assert editor.tab(document, 0, len("a\n\nb"), "  ", False) == (0, len("  a\n\n  b"))
    assert document.toPlainText() == "  a\n\n  b\n  c"  # the empty line stays empty
    document.undo()
    assert document.toPlainText() == "a\n\nb\n  c"
    editor.tab(document, len("a\n\nb\n"), len("a\n\nb\n  c"), "  ", True)  # Shift+Tab
    assert document.toPlainText() == "a\n\nb\nc"
    document.setPlainText("ab")
    assert editor.tab(document, 1, 1, "    ", False) == (4, 4) and document.toPlainText() == "a   b"  # the next stop
    assert editor.where(document, 3) == "Строка 1, столбец 4"

    assert editor.why_not({"text": "x", "encoding": "utf-8", "size": 1}) == ""
    assert editor.why_not({"text": "a\r\nb", "encoding": "cp1251", "size": 4}) == ""
    assert "1 МБ" in editor.why_not({"text": "x", "encoding": "utf-8", "size": 2_000_000})
    assert editor.why_not({"binary": True}) == "двоичный файл"
    for text in ("a\u2028b", "a\u2029b", "a\rb"):  # a TextEdit would make line ends of them: saving changes them
        assert "U+2028" in editor.why_not({"text": text, "encoding": "utf-8", "size": 3}), repr(text)
    assert editor.status({"encoding": "cp1251", "eol": "CRLF"}, "\t") == "Windows-1251 · CRLF · табуляция"
    assert editor.status({"encoding": "utf-8", "eol": "CRLF+LF", "text": "a\r\nb\r\nc\n"}, "  ") == (
        "UTF-8 · CRLF и LF — сохранится как CRLF · пробелы: 2")  # fmt: skip


def test_editing_saving_and_the_disk(app):  # noqa: F811
    client, view, projects = app.client, app.editor, app.projects
    document = QTextDocument()
    view.attach(Quick(document))
    view.loaded.connect(document.setPlainText)
    projects.openProject("p1")
    projects.openFile("src/app.py", False)
    wait_until(lambda: projects.file.get("path") == "src/app.py")
    assert projects.file["editBlock"] == ""
    original = FILES["src/app.py"]

    view.open("p1", "src/app.py")
    wait_until(lambda: view.editing and not view.busy)
    assert document.toPlainText() == original and not view.dirty and view.status == "UTF-8 · LF · пробелы: 4"
    typed(document, "print('ещё')\n")
    view.check()
    assert view.dirty
    view.save()
    wait_until(lambda: not view.busy)
    assert client.saves[-1] == ("p1", "src/app.py", original + "print('ещё')\n", "utf-8", sha(original))
    assert not view.dirty and app.flashText == "Сохранено: app.py"

    def disk(text: str) -> None:  # another program writes the file; the hub's watcher says so
        client.files["src/app.py"] = text
        projects.on_event("project.files", {"project_id": "p1", "changed": ["src/app.py"], "folders": []})

    disk(client.files["src/app.py"])  # our own save, coming back: nothing happens
    wait_until(lambda: not app.bg.busy)
    assert not view.conflict and document.toPlainText() == original + "print('ещё')\n"
    disk("print('другое')\n")  # no edits: taken in place
    wait_until(lambda: document.toPlainText() == "print('другое')\n")
    assert not view.dirty and not view.conflict

    typed(document, "x = 1\n", 0)
    view.check()
    disk("print('третье')\n")  # with edits: the bar asks
    wait_until(lambda: view.conflict.get("reason") == "changed")
    view.compare()
    assert view.compareView["chunks"] and not view.compareView["empty"]
    view.takeDisk()
    assert document.toPlainText() == "print('третье')\n" and not view.dirty and not view.conflict
    document.undo()  # the edits come back with Ctrl+Z
    assert document.toPlainText() == "x = 1\nprint('другое')\n"
    view.check()
    assert view.dirty

    disk("print('четвёртое')\n")
    wait_until(lambda: view.conflict.get("reason") == "changed")
    view.keepMine()  # mine stays, and the save writes over the disk's
    assert not view.conflict and view.dirty
    view.save()
    wait_until(lambda: not view.busy)
    assert client.files["src/app.py"] == "x = 1\nprint('другое')\n" and not view.dirty

    client.files["src/app.py"] = "чужое\n"  # changed between the watcher's look and the save: the hub refuses
    typed(document, "y = 2\n")
    view.check()
    view.save()
    wait_until(lambda: view.conflict.get("reason") == "changed")
    assert client.files["src/app.py"] == "чужое\n"

    projects.openFile("README.md", False)  # unsaved edits: asked before they go
    assert app.question["confirm"] == "Не сохранять" and projects.file["path"] == "src/app.py"
    app.answer(False)
    assert view.editing and projects.file["path"] == "src/app.py"
    projects.openFile("README.md", False)
    app.answer(True)
    wait_until(lambda: projects.file.get("path") == "README.md")
    assert not view.editing

    client.files["big.txt"] = "x" * (editor.EDIT_LIMIT + 1)  # the viewer alone
    projects.openFile("big.txt", False)
    wait_until(lambda: projects.file.get("path") == "big.txt")
    assert "1 МБ" in projects.file["editBlock"]


def test_the_hub_knows_the_unsaved_edits(app):  # noqa: F811
    """D47, step 14.2c: the window tells the hub which file its editor holds with edits not saved - when that
    changes, and again whenever its events connect (the hub forgets a window gone away); a model's write into it is
    asked then, a trusted chat's too, and the card says why."""
    from app.ui.approvals import approval_rows
    from tests.test_ui import APPROVAL

    client, view = app.client, app.editor
    document = QTextDocument()
    view.attach(Quick(document))
    view.loaded.connect(document.setPlainText)
    view.open("p1", "src/app.py")
    wait_until(lambda: view.editing and not view.busy)
    assert client.unsaved == []  # opened, not edited: nothing to say
    typed(document, "x = 1\n")
    view.check()
    wait_until(lambda: client.unsaved)
    assert client.unsaved == [[{"project_id": "p1", "path": "src/app.py"}]]
    typed(document, "y = 2\n")
    view.check()
    view.save()
    wait_until(lambda: len(client.unsaved) == 2 and not app.bg.busy)
    assert client.unsaved[-1] == []  # saved
    app._on_connected(True)  # the events connected (again): said again
    wait_until(lambda: len(client.unsaved) == 3)
    typed(document, "z = 3\n")
    view.check()
    wait_until(lambda: len(client.unsaved) == 4)
    view.stop()
    app.answer(True)  # «Не сохранять»
    wait_until(lambda: len(client.unsaved) == 5)
    assert client.unsaved[2:] == [[], [{"project_id": "p1", "path": "src/app.py"}], []]

    asked = {**APPROVAL, "tool": "edit_file", "kind": "write", "unsaved": True}
    (row,) = approval_rows([asked], projects={"p1": {"id": "p1", "name": "LocalChat"}}, chats={})
    assert row["alwaysAsk"] == "unsaved" and row["projectScope"] == ""


def test_the_editor_in_the_window(qapp):  # noqa: F811
    from PySide6.QtQuick import QQuickItem

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def key(code, text="", modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(quick.window, QKeyEvent(kind, code, modifiers, text))

    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        app.projects.openProject("p1")
        app.projects.openFile("src/app.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/app.py")
        edit_button = quick.window.findChild(QObject, "editFile")
        assert edit_button.property("visible") and edit_button.property("enabled")
        app.editor.open("p1", "src/app.py")
        wait_until(lambda: app.editor.editing)
        body = quick.window.findChild(QQuickItem, "editorText")
        assert body is not None and body.property("text") == FILES["src/app.py"]
        assert quick.window.findChild(QObject, "saveFile").property("visible")
        assert not quick.window.findChild(QObject, "codeView").property("visible")  # the viewer steps aside
        quick.window.requestActivate()
        body.forceActiveFocus()
        body.setProperty("cursorPosition", len(FILES["src/app.py"]))

        for char in "def f():":
            key(Qt.Key.Key_unknown if char in "():" else getattr(Qt.Key, f"Key_{char.upper()}", 0), char)
        key(Qt.Key.Key_Return)
        assert body.property("text") == FILES["src/app.py"] + "def f():\n    "  # the next line, one step in
        key(Qt.Key.Key_P, "p")
        wait_until(lambda: app.editor.dirty)  # a pause in typing: «не сохранено»
        key(Qt.Key.Key_S, "", Qt.KeyboardModifier.ControlModifier)
        wait_until(lambda: client.saves and not app.editor.busy)
        assert client.saves[-1][2] == FILES["src/app.py"] + "def f():\n    p"
        assert not app.editor.dirty

        body.setProperty("cursorPosition", 0)
        body.setProperty("selectionEnd", 0)
        key(Qt.Key.Key_Tab)
        assert body.property("text").startswith("    print('hi')")
        key(Qt.Key.Key_Z, "", Qt.KeyboardModifier.ControlModifier)  # the TextEdit's own undo
        assert body.property("text").startswith("print('hi')")
        status = quick.window.findChild(QObject, "editorStatusText").property("text")
        assert "UTF-8 · LF · пробелы: 4" in status and "Строка 1" in status

        # the colours reach the screen: a pixel of `print`'s colour in the editor (14.2b)
        from pygments.token import Name

        wait_until(lambda: app.editor._document.firstBlock().userState() >= 0)
        wanted = highlighter._style(palette.current().name).style_for_token(Name.Builtin)["color"]
        red, green, blue = (int(wanted[i : i + 2], 16) for i in (0, 2, 4))
        shot = quick.window.grabWindow()
        corner = body.mapToScene(body.position())
        found = False
        for y in range(int(corner.y()), int(corner.y()) + 40):
            for x in range(int(corner.x()), int(corner.x()) + 200):
                pixel = shot.pixelColor(x, y)
                if abs(pixel.red() - red) + abs(pixel.green() - green) + abs(pixel.blue() - blue) < 60:
                    found = True
                    break
            if found:
                break
        assert found, f"no pixel of #{wanted} where `print` is drawn"
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_whole_text_lexed_once_the_screen_coloured(app):  # noqa: F811
    """A comment over two lines is one token for the whole text (lexed a line at a time, it would not be); Qt counts
    an emoji as two. The screen's reach (on it and AROUND it) gets its colours at once, then from the whole text's
    lexing - the lines on screen first, in short slices; lines further off when the screen comes to them. A line
    being typed in gets its own at once, the whole text after the pause."""
    from pygments.token import Comment, Keyword, Number

    lines, marks = highlighter.lex_lines(highlighter.lexer_for("a.c"), "/* one\n   two */ int x = 1;\n")
    assert len(lines) == 3 and lines[2] == () and marks[0] == highlighter.mark(lines[0])
    assert lines[1][0][:2] == (0, len("   two */")) and lines[1][0][2] in Comment
    (row,), _ = highlighter.lex_lines(highlighter.lexer_for("a.py"), "s = '\U0001f600'; n = 1")
    assert next(r for r in row if r[2] in Number)[0] == len("s = '\U0001f600'; n = ") + 1
    assert highlighter.lexer_for("notes.unknownext") is None

    document = QTextDocument()
    document.documentLayout()  # a TextEdit's document has its layout; without one Qt sends no contentsChange
    document.setPlainText("\n".join(f"def f{n}():\n    return {n}" for n in range(3000)))
    colours = highlighter.Highlighter(app)
    laid = []
    set_ = colours._set
    colours._set = lambda block, ranges, state: (laid.append(block.blockNumber()), set_(block, ranges, state))
    colours.attach(document)
    colours.screen(4000, 40)
    colours.start("big.py")
    reach = list(range(4000 - highlighter.AROUND, 4040 + highlighter.AROUND + 1))
    assert laid == reach  # the screen's reach at once, before the whole text is lexed
    wait_until(lambda: colours._revision == document.revision() and not colours._pending, timeout=20)
    assert laid[len(reach) :] == []  # the whole text's lexing says the same of them: nothing laid again
    block = document.findBlockByNumber(4000)  # `def f2000():`
    assert (block.layout().formats()[0].start, block.layout().formats()[0].length) == (0, 3)
    assert block.userState() == highlighter.mark(colours._lines[4000])
    assert document.firstBlock().userState() == -1  # far from the screen: not yet

    colours.screen(0, 40)  # the screen comes to the top: its lines from the lexing, in slices
    wait_until(lambda: not colours._pending)
    assert document.firstBlock().userState() == highlighter.mark(colours._lines[0])
    assert colours.slices and max(colours.slices) < 50  # short slices (8 ms, a line over at most)

    QTextCursor(document.firstBlock()).insertText("async ")  # typing: this line at once
    first = document.firstBlock().layout().formats()
    assert (first[0].start, first[0].length) == (0, 5)  # `async`, before the whole text is lexed again
    del laid[:]
    wait_until(lambda: colours._revision == document.revision() and not colours._pending)
    assert len(laid) < 10  # after the pause, only what changed is laid on again
    keyword = highlighter._style(palette.current().name).style_for_token(Keyword)["color"]
    assert first[0].format.foreground().color().name() == "#" + keyword.lower()
    colours.stop()
