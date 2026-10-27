"""Focus by mention (stage 3, D27): @project and @project/file in a message, names in passing, finding files."""

from app.ui import mentions
from hub.search import find_files

PROJECTS = [
    {"id": "p1", "name": "LocalChat", "path": "D:/AI model/LocalChat", "description": "чат и хаб"},
    {"id": "p2", "name": "llama-swap", "path": "D:/AI model/llama-swap"},
    {"id": "p3", "name": "AI model", "path": "D:/AI model"},
    {"id": "p4", "name": "ui", "path": "D:/ui"},
]


def test_the_word_being_typed():
    assert mentions.at_word("@", 1) == (0, "")
    assert mentions.at_word("смотри @Loc", 11) == (7, "Loc")
    assert mentions.at_word("смотри @LocalChat/hub/ch", 24) == (7, "LocalChat/hub/ch")
    assert mentions.at_word("(@lla", 5) == (1, "lla")
    assert mentions.at_word("mail me@host", 12) is None  # an address, not a mention
    assert mentions.at_word("", 5) is None  # the text was just sent: QML still reports the old cursor
    assert mentions.at_word("@Lo", 99) == (0, "Lo")


def test_qml_positions_are_utf16_units():
    text = "привет 👋 @Loc"  # the emoji is one character here, two units in QML
    cursor = len(text.encode("utf-16-le")) // 2  # QML's cursor at the end: 14
    assert cursor == 14 and len(text) == 13
    index = mentions.from_utf16(text, cursor)
    assert index == 13 and mentions.at_word(text, index) == (9, "Loc")
    assert mentions.to_utf16(text, 9) == 10  # where QML's @ is
    assert mentions.from_utf16("abc", 99) == 3 and mentions.from_utf16("abc", -1) == 0
    assert mentions.at_word("@LocalChat готово", 17) is None  # the cursor is past the mention
    assert mentions.at_word("@Loc и дальше", 3) == (0, "Lo")  # the part before the cursor


def test_project_and_file_choices():
    assert [c["name"] for c in mentions.project_choices("l", PROJECTS)] == ["llama-swap", "LocalChat", "AI model"]
    first = mentions.project_choices("loc", PROJECTS)[0]
    assert first == {"kind": "project", "name": "LocalChat", "note": "чат и хаб", "insert": "@LocalChat "}
    assert len(mentions.project_choices("", PROJECTS)) == 4
    assert mentions.file_query("LocalChat/hub/ch", PROJECTS) == (PROJECTS[0], "hub/ch")
    assert mentions.file_query("localchat/", PROJECTS) == (PROJECTS[0], "")
    assert mentions.file_query("Local", PROJECTS) is None
    files = mentions.file_choices(PROJECTS[0], ["hub/chat.py"])
    assert files == [{"kind": "file", "name": "hub/chat.py", "note": "LocalChat", "insert": "@LocalChat/hub/chat.py "}]


def test_what_a_message_mentions():
    text = "@LocalChat почему не видит @llama-swap? Глянь @LocalChat/hub/settings.py, и (@AI model/README.md)."
    ids, files = mentions.mentioned(text, PROJECTS)
    assert ids == ["p1", "p2", "p3"]
    assert files == [("p1", "hub/settings.py"), ("p3", "README.md")]
    assert mentions.mentioned("пиши на me@LocalChat.dev", PROJECTS) == ([], [])
    assert mentions.mentioned("@LocalChatty", PROJECTS) == ([], [])  # not a whole name
    assert mentions.mentioned("@localchat", PROJECTS) == (["p1"], [])  # any case


def test_projects_named_in_passing():
    found = mentions.named("почему llama-swap не отвечает, а LocalChat ждёт?", PROJECTS, skip=set())
    assert [p["id"] for p in found] == ["p1", "p2"]
    assert mentions.named("почему llama-swap молчит", PROJECTS, skip={"p2"}) == []
    assert [p["id"] for p in mentions.named("Упал LocalChat.", PROJECTS, skip=set())] == ["p1"]
    assert mentions.named("правлю ui окна", PROJECTS, skip=set()) == []  # too short a name to guess
    assert mentions.named("файл localchat.py и llama-swapper", PROJECTS, skip=set()) == []  # parts of other words
    assert mentions.named("@LocalChat", PROJECTS, skip=set()) == []  # an @ mention is not a suggestion


def test_finding_files_by_a_few_letters(repo):
    (repo / "src" / "chat.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src" / "deep").mkdir()
    (repo / "src" / "deep" / "mychat.py").write_text("x = 1\n", encoding="utf-8")
    assert find_files(repo, "chat") == ["src/chat.py", "src/deep/mychat.py"]  # the name starting with it first
    assert find_files(repo, "src/de") == ["src/deep/mychat.py"]
    assert find_files(repo, "sdm") == ["src/deep/mychat.py"]  # its letters in order
    assert find_files(repo, "readme") == ["README.md"]
    assert "secret.txt" not in find_files(repo, "")  # ignored by git
    assert len(find_files(repo, "", limit=2)) == 2


def test_files_route(repo):
    from starlette.testclient import TestClient

    from common.paths import data_paths
    from hub.server import create_app
    from hub.settings import HubSettings

    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as hub:
        hub.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
        assert hub.get(f"/v1/projects/{pid}/files", params={"q": "app"}).json() == {"files": ["src/app.py"]}
        assert len(hub.get(f"/v1/projects/{pid}/files", params={"limit": 1}).json()["files"]) == 1
        assert hub.get("/v1/projects/nope/files").status_code == 404
