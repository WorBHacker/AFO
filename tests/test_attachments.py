"""Files and pictures sent with a message (2026-10-29, the user's word): known by their bytes, kept once in
data/blobs, read out as text (UTF-8, Windows-1251, Word) through the secret filter, refused with why; every provider
gets the pictures its own way; the request's size counts a picture, not its base64; a model that does not see
pictures is not sent one; the window's composer takes files, pictures from the clipboard, and sends them."""

import base64
import io
import sys
import zipfile

import pytest

from hub import attachments, fit
from hub.gateway import anthropic_provider, gemini_provider, openai_compat, openai_responses
from hub.gateway.base import Model
from tests.test_autonomous import Scripted, use
from tests.test_changes import wait
from tests.test_tools import hub  # noqa: F401 - the hub fixture

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
SECRET = "sk-ant-api03-" + "x" * 40


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def word(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')  # fmt: skip
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def test_what_a_file_becomes(tmp_path):
    found = attachments.blocks(tmp_path, [
        {"name": "снимок.png", "data": b64(PNG)},
        {"name": "notes.txt", "data": b64(f"ключ: {SECRET}\nвторая строка".encode())},
        {"name": "старый.txt", "data": b64("Привет из Windows-1251".encode("cp1251"))},
        {"name": "отчёт.docx", "data": b64(word("Первый абзац", "Второй абзац"))},
    ])  # fmt: skip
    picture, notes, old, report = found
    assert picture["type"] == "image" and picture["media_type"] == "image/png" and picture["size"] == len(PNG)
    assert (tmp_path / picture["blob"]).read_bytes() == PNG  # kept once, by its content
    assert notes["type"] == "file" and SECRET not in notes["text"] and "вторая строка" in notes["text"]
    assert old["text"] == "Привет из Windows-1251" and report["text"] == "Первый абзац\nВторой абзац"
    assert report["source"] == "docx"
    again = attachments.blocks(tmp_path, [{"name": "копия.png", "data": b64(PNG)}])
    assert again[0]["blob"] == picture["blob"] and len(list(tmp_path.iterdir())) == 4  # the same picture once
    text = attachments.prompt_text([{"type": "text", "text": "Что тут?"}, *found])
    assert text.startswith('Что тут?\n\n<attachment name="notes.txt">\nключ: ')
    assert '<attachment name="отчёт.docx">\nПервый абзац' in text and text.endswith("[pictures attached: снимок.png]")
    assert attachments.images(tmp_path, found) == [{"media_type": "image/png", "data": b64(PNG)}]

    def refused(item):
        with pytest.raises(attachments.AttachmentError) as caught:
            attachments.blocks(tmp_path, [item])
        return str(caught.value)

    assert "не текст и не картинка" in refused({"name": "app.exe", "data": b64(b"MZ\x00\x00\x00binary")})
    assert "пустой" in refused({"name": "a.txt", "data": ""})
    assert "повреждённым" in refused({"name": "a.txt", "data": "не base64!"})
    assert "PDF не читается" in refused({"name": "a.pdf", "data": b64(b"%PDF-1.7\n...")})  # a broken one


def pdf(pages: list[str]) -> bytes:
    """A PDF with a line of text on each page, made without libraries."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")  # fmt: skip
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_a_pdf_is_read_page_by_page(tmp_path, monkeypatch):
    (found,) = attachments.blocks(tmp_path, [{"name": "отчёт.pdf", "data": b64(pdf(["Hello from page one",
                                                                                  "Second page text"]))}])  # fmt: skip
    assert found["type"] == "file" and found["source"] == "pdf"
    assert found["text"] == "Hello from page one\n\nSecond page text"
    monkeypatch.setitem(sys.modules, "pypdf", None)  # without the package: refused with why
    with pytest.raises(attachments.AttachmentError, match="нужен пакет pypdf"):
        attachments.blocks(tmp_path, [{"name": "a.pdf", "data": b64(pdf(["x"]))}])
    with pytest.raises(attachments.AttachmentError):
        attachments.blocks(tmp_path, [{"name": f"{n}.txt", "data": b64(b"x")} for n in range(11)])


def test_every_provider_gets_the_pictures():
    picture = {"media_type": "image/png", "data": b64(PNG)}
    turn = {"role": "user", "content": "Что на картинке?", "images": [picture]}
    context = {"role": "system", "content": "контекст"}
    wire = openai_compat.wire_messages([turn, context])
    assert wire == [{"role": "user", "content": [
        {"type": "text", "text": "Что на картинке?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{picture['data']}"}},
        {"type": "text", "text": "<context>\nконтекст\n</context>"}]}]  # fmt: skip
    _system, turns = anthropic_provider.to_wire([turn], "anthropic")
    assert turns[0]["content"][0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                  "data": picture["data"]}}  # fmt: skip
    assert turns[0]["content"][1] == {"type": "text", "text": "Что на картинке?"}
    _instructions, items = openai_responses.to_input([turn], "openai")
    assert items[0]["content"][1] == {"type": "input_image", "image_url": f"data:image/png;base64,{picture['data']}"}
    _system, parts = gemini_provider.to_wire([turn], "gemini")
    assert parts[0]["parts"][0] == {"inlineData": {"mimeType": "image/png", "data": picture["data"]}}
    big = {**turn, "images": [{"media_type": "image/png", "data": "A" * 3_000_000}]}
    assert fit.tokens(big) < fit.tokens({"role": "user", "content": "Что на картинке?"}) + fit.IMAGE_TOKENS + 10


class Blind(Scripted):
    """A scripted model the settings say sees no pictures."""

    def __init__(self, name, script):
        super().__init__(name, script)
        self.models = [Model(f"{name}/m", name, "m", "Слепая", "local", {"vision": False}, {})]


def test_a_message_with_files_reaches_the_model(hub):  # noqa: F811
    eyes = use(hub, Scripted("eyes", ["Вижу красную точку."]))
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    url = f"/v1/conversations/{conv}/messages"
    files = [{"name": "точка.png", "data": b64(PNG)}, {"name": "data.csv", "data": b64(b"a,b\n1,2\n")}]
    sent = hub.post(url, json={"content": "", "model_id": "eyes/m", "files": files})
    assert sent.status_code == 202, sent.text
    stored = sent.json()["user_message"]["content"]
    assert [b["type"] for b in stored] == ["image", "file"]  # no empty text block
    wait(lambda: eyes.prompts)
    asked = eyes.prompts[0][-1]
    assert asked["images"] == [{"media_type": "image/png", "data": b64(PNG)}]
    assert '<attachment name="data.csv">\na,b\n1,2\n</attachment>' in asked["content"]
    chat = hub.get(f"/v1/conversations/{conv}").json()
    assert chat["title"] == "точка.png, data.csv"  # named after the files
    wait(lambda: conv not in hub.get("/v1/conversations").json()["busy"])

    use(hub, Blind("blind", []))
    refused = hub.post(url, json={"content": "А это?", "model_id": "blind/m", "files": files[:1]})
    assert refused.status_code == 409 and "не видит картинки" in refused.json()["detail"]
    assert hub.post(url, json={"content": "  ", "model_id": "eyes/m"}).status_code == 422
    bad = hub.post(url, json={"content": "x", "model_id": "eyes/m", "files": [{"name": "a.bin", "data": b64(b"\0\1")}]})
    assert bad.status_code == 409 and "не текст и не картинка" in bad.json()["detail"]
    assert len(hub.get(f"/v1/conversations/{conv}").json()["messages"]) == 2  # refused ones stored nothing


def test_claude_code_gets_the_files_paths(tmp_path):
    from hub.chat import ChatService

    found = attachments.blocks(tmp_path / "blobs", [{"name": "../secret/plan.txt", "data": b64(b"plan")}])
    service = ChatService.__new__(ChatService)
    service.blobs = tmp_path / "blobs"
    text = ChatService._files_for_agent(service, "c1", 7, "Посмотри", found)
    path = tmp_path / "attachments" / "c1" / "7" / "plan.txt"  # no way out of the folder by its name
    assert text == f"Посмотри\n\nFiles sent with this message (read them):\n- {path}" and path.read_text() == "plan"


class Clipboard:
    """The clipboard as the composer reads it: a picture, or files copied in Explorer (file: URLs), or neither."""

    def __init__(self, image):
        self.held = image
        self.copied: list[str] = []

    def image(self):
        return self.held

    def mimeData(self):
        from PySide6.QtCore import QMimeData, QUrl

        data = QMimeData()
        if self.copied:
            data.setUrls([QUrl(u) for u in self.copied])
        return data


def test_the_composer_takes_files_and_pictures(qapp, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QImage

    from app.ui import rows
    from app.ui.main import QuickApp
    from common.paths import data_paths
    from tests.test_ui import FakeClient, opened, wait_until

    picture = tmp_path / "кадр.png"
    picture.write_bytes(PNG)
    notes = tmp_path / "notes.md"
    notes.write_bytes(b"# Notes\n12")
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        app.attachFiles([picture.as_uri(), str(notes), str(tmp_path / "нет.txt"), str(notes)])
        assert [(a["name"], a["picture"]) for a in app.attachments] == [("кадр.png", True), ("notes.md", False)]
        assert app.attachments[1]["size"] == "10 Б" and app.attachWarning == ""
        assert quick.window.findChild(QObject, "attachments").property("visible")
        app.models[app.modelId] = {**app.models[app.modelId], "capabilities": {"vision": False}}
        app.modelChanged.emit()
        assert app.attachWarning.endswith("не видит картинки — выберите модель со зрением")
        app.removeAttachment(1)
        assert [a["name"] for a in app.attachments] == ["кадр.png"]
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(0xFF0000)
        board = Clipboard(image)  # a stand-in: the user's own clipboard is never touched
        app._clipboard = lambda: board
        assert app.pasteImage() and app.attachments[-1]["name"].startswith("картинка-")
        board.held = QImage()  # text only on it
        assert app.pasteImage() is False  # plain text is pasted as usual
        board.held, board.copied = QImage(), [notes.as_uri()]  # files copied in Explorer
        assert app.pasteImage() and app.attachments[-1]["name"] == "notes.md"
        app.removeAttachment(2)
        app.send("")  # files only: they go
        wait_until(lambda: not app.bg.busy and client.sent)
        names = [f["name"] for f in client.files_sent]
        assert names[0] == "кадр.png" and names[1].startswith("картинка-") and len(names) == 2
        assert base64.b64decode(client.files_sent[0]["data"]) == PNG and app.attachments == []
        content = [{"type": "text", "text": "вот"}, {"type": "image", "name": "кадр.png"},
                   {"type": "file", "name": "a.md"}]  # fmt: skip
        row = rows.user_row({"id": 5, "role": "user", "content": content})
        assert row["files"] == [{"name": "кадр.png", "picture": True}, {"name": "a.md", "picture": False}]
        for _ in range(10):
            qapp.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
