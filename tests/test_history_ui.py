"""«История файла» in the window (stage 26.2): a project file's versions with who made each, the chosen one's diff -
what changed in it, or what taking it back changes; «Вернуть» through the hub, checked against the file as the sheet
saw it. Files gone from the disk come back from «Удалённые файлы» in the project's overview. The hub's events reach
the project's lists: «Правки моделей» followed `file.*` only when the project was opened again."""

from PySide6.QtCore import QMetaObject, QObject

from app.hub_client import HubError
from app.ui import history_view, project_rows
from common.paths import data_paths
from tests.test_code_editor import shown
from tests.test_ui import FILES, FakeClient, qapp, send, sha, wait_until  # noqa: F401 - the qapp fixture

VERSIONS = [
    {"id": 7, "created_at": "2026-11-06T10:05:00.000Z", "path": "src/app.py", "hash": sha(FILES["src/app.py"]),
     "size": 25, "deleted": False, "change_id": None, "tool": None, "chat": None},
    {"id": 5, "created_at": "2026-11-06T10:00:00.000Z", "path": "src/app.py", "hash": sha("print('hi')\n"),
     "size": 12, "deleted": False, "change_id": 3, "tool": "edit_file", "chat": "Рефакторинг"},
    {"id": 2, "created_at": "2026-11-05T09:00:00.000Z", "path": "src/app.py", "hash": sha("old\n"), "size": 4,
     "deleted": False, "change_id": 1, "tool": "save", "chat": None},
]  # fmt: skip
GONE = {"path": "docs/old.md", "versions": 2, "last_at": "2026-11-06T09:00:00.000Z", "deleted": True}


class HistoryClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.versions = [dict(v) for v in VERSIONS]
        self.asked = []
        self.restored = []
        self.conflict = False

    def history(self, project_id, path=""):
        if not path:
            return [{"path": "src/app.py", "versions": 3, "last_at": "2026-11-06T10:05:00.000Z", "deleted": False},
                    dict(GONE)]  # fmt: skip
        return [dict(v) for v in self.versions if v["path"] == path]

    def version(self, version_id, against="previous"):
        self.asked.append((version_id, against))
        same = against == "current" and version_id == 7
        return {"diff": "" if same else f"--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x\n+v{version_id}\n"}

    def restore_version(self, version_id, base):
        if self.conflict:
            raise HubError("changed", data={"reason": "changed", "message": "changed", "file": None})
        self.restored.append((version_id, base))
        return {"path": "src/app.py", "change": {"id": 9, "tool": "restore"}, "restored": version_id}


def test_who_made_each_version_and_which_is_the_file_now():
    rows = history_view.version_rows(VERSIONS, current=VERSIONS[0]["hash"], selected=5)
    assert [r["who"] for r in rows] == ["изменён на диске", "модель, чат «Рефакторинг»", "вы, в редакторе"]
    assert [(r["current"], r["selected"]) for r in rows] == [(True, False), (False, True), (False, False)]
    assert rows[1]["size"] == "12 Б"
    assert history_view.who({"deleted": True}) == "файл удалён"
    assert history_view.who({"tool": "restore"}) == "вы вернули версию"
    assert history_view.who({"tool": "write_file"}) == "модель"
    (gone,) = project_rows.deleted_rows([GONE, {**GONE, "path": "a.txt", "deleted": False}])
    assert (gone["name"], gone["folder"]) == ("old.md", "docs") and gone["when"].startswith("удалён")
    change = {"id": 1, "path": "a.txt", "before_hash": "x", "tool": "restore", "created_at": ""}
    assert project_rows.write_rows([change])[0]["note"] == "возвращена версия · вы"


def test_the_sheet_reads_compares_and_takes_back(qapp):  # noqa: F811
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = HistoryClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        app.projects.openProject("p1")
        wait_until(lambda: shown(quick, "deletedFile") is not None)  # the overview: files gone, from their history
        assert app.projects.deletedFiles[0]["path"] == "docs/old.md"
        app.projects.openFile("src/app.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/app.py")
        button = shown(quick, "fileHistory")
        assert button is not None
        QMetaObject.invokeMethod(button, "clicked")
        view = app.history_view
        wait_until(lambda: view.state == "ready" and view.view.get("key") == "history:7:previous")
        assert quick.window.findChild(QObject, "historySheet").property("opened")
        assert [r["id"] for r in view.rows] == [7, 5, 2] and view.rows[0]["current"]
        assert "+v7" in view.view["text"] and "сейчас на диске" in view.note
        assert not view.canRestore  # the newest is the file now: nothing to take back

        view.setMode("current")
        wait_until(lambda: view.view.get("key") == "history:7:current")
        assert view.view["empty"]
        assert shown(quick, "historyEmpty").property("text") == "Эта версия совпадает с файлом сейчас."
        view.pick(5)
        wait_until(lambda: view.view.get("key") == "history:5:current")
        assert view.canRestore and "модель, чат «Рефакторинг»" in view.note

        view.restore()
        when = project_rows.local_time(VERSIONS[1]["created_at"])
        assert app.question["title"] == f"Вернуть app.py к версии от {when}?"
        client.conflict = True  # the file changed meanwhile: said, and read again
        app.answer(True)
        wait_until(lambda: "файл изменился, пока была открыта история" in app.flashText and app.flashError)
        client.conflict = False
        view.restore()
        app.answer(True)
        wait_until(lambda: client.restored)
        assert client.restored == [(5, sha(client.files["src/app.py"]))]  # against the file as the sheet saw it
        wait_until(lambda: view.path == "")  # closed: the file shows
        assert app.flashText.startswith("app.py: возвращена версия от")
        assert not quick.window.findChild(QObject, "historySheet").property("opened")

        # the hub's events: a new version of the file shown reads it again; «Правки моделей» follows `file.*`
        view.show("p1", "src/app.py")
        wait_until(lambda: view.state == "ready" and view.view.get("key"))
        client.versions.insert(0, {**VERSIONS[0], "id": 8, "hash": "new"})
        send(app, "history.changed", {"project_id": "p1", "paths": ["src/app.py"]})
        wait_until(lambda: [r["id"] for r in view.rows][:1] == [8])
        client.change_list = [{"id": 11, "path": "README.md", "before_hash": "a", "tool": "save", "created_at": "",
                               "reverted_at": None, "chat": None}]  # fmt: skip
        send(app, "file.saved", {"project_id": "p1", "path": "README.md"})
        wait_until(lambda: [c["id"] for c in app.projects.fileChanges] == [11])
        view.close()
        assert quick.warnings == []
    finally:
        quick.close()


def coloured(quick, item) -> int:
    """Pixels of the item on screen that are a colour, not a grey: the text's colours drawn."""
    shot = quick.window.grabWindow()
    corner = item.mapToScene(item.position().__class__(0, 0))
    found = 0
    for y in range(int(corner.y()), int(corner.y() + 90), 2):  # the first lines' start: nothing else lies there
        for x in range(int(corner.x()), int(corner.x() + 150), 2):
            pixel = shot.pixelColor(x, y)
            channels = (pixel.red(), pixel.green(), pixel.blue())
            found += max(channels) - min(channels) > 60
    return found


def test_a_viewers_next_text_is_drawn_in_its_colours(qapp):  # noqa: F811
    """The viewer's one TextEdit showed its first text in colours, and every next one - another file, another
    version in «История файла» - grey: the colours were in the document, the TextEdit did not draw them again until
    something laid it out anew (found in 26.2)."""
    import time

    from app.ui.main import QuickApp

    def pause(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.02)

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = HistoryClient()
        client.files["src/other.py"] = "x = 1\n"
        client.files["src/app.py"] = "import os\n"  # a keyword's colour at the line's start: left of any popup
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        app.projects.openProject("p1")
        app.projects.openFile("src/other.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/other.py")
        pause(0.8)
        app.projects.openFile("src/app.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/app.py")
        pause(0.8)
        assert coloured(quick, shown(quick, "codeView")) > 0

        QMetaObject.invokeMethod(shown(quick, "fileHistory"), "clicked")
        view = app.history_view
        wait_until(lambda: view.state == "ready" and view.view.get("key"))
        pause(0.8)
        view.pick(5)
        wait_until(lambda: view.view.get("key") == "history:5:previous")
        pause(0.8)
        assert coloured(quick, shown(quick, "historyView")) > 0
    finally:
        quick.close()
