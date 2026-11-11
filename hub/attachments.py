"""Files and pictures the user sends with a message (2026-10-29, the user's word: «сделай возможным отправить файлы и
изображение в ИИ модель»). Each is kept once by its content in data/blobs, the message holds blocks that name it, and
every request sends it the way the model takes it.

- a picture (PNG, JPEG, GIF, WebP - known by its bytes, not its name): an image block; a model that sees pictures
  gets it as one (the adapters: image_url for the OpenAI-compatible ones, image blocks for Claude, inline data for
  Gemini, input_image for the Responses API); one the user's settings say does not see them is refused with why.
- a text file (code, notes, CSV, JSON, HTML - UTF-8, or Windows-1251 as Russian Windows writes it): its text goes
  with the message in an <attachment> element, through the secret filter, cut at TEXT_CHARS.
- a Word document (.docx): the text of its paragraphs (the file is a zip of XML: no library needed).
- a PDF: its text, when the pypdf package is installed; else refused with why.
Anything else is refused with why. Claude Code is given the files' paths instead: it reads them itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ntpath
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from common.i18n import tr

from .secret_filter import clean

MAX_BYTES = 20 * 1024 * 1024  # one file
MAX_TOTAL = 40 * 1024 * 1024  # one message's
MAX_FILES = 10
TEXT_CHARS = 200_000  # of one file's text the model reads
NAME_CHARS = 100  # of a file's name written for Claude Code: with the data folder's path, within Windows' 260
EXTENSION_CHARS = 16
REFUSED = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # what Windows takes in no file's name
PICTURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"))  # fmt: skip
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class AttachmentError(ValueError):
    """What is wrong with a file, in the user's words."""


def picture_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in PICTURES if data.startswith(magic)), None)


def keep(folder: Path, data: bytes) -> str:
    """The file in data/blobs under its sha256 (once, however often it is sent); its name there."""
    name = hashlib.sha256(data).hexdigest()
    path = folder / name
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".part")
        temp.write_bytes(data)
        temp.replace(path)
    return name


def load(folder: Path, name: str) -> bytes | None:
    if not re.fullmatch(r"[0-9a-f]{64}", name):
        return None
    try:
        return (folder / name).read_bytes()
    except OSError:
        return None


def file_names(names: list[str]) -> list[str]:
    """The names the files sent with a message are written under for Claude Code: each one's own, as Windows takes a
    file's name - its last part (no way out of the folder), a character Windows refuses as «_», no dot or space at the
    end, not a device's (CON, NUL, COM1...), at most NAME_CHARS with its extension - and two alike told apart:
    «отчёт (2).txt»."""
    taken: set[str] = set()
    out = []
    for name in names:
        base = REFUSED.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1]).rstrip(". ") or "file"
        root, ext = os.path.splitext(base)
        if len(ext) > EXTENSION_CHARS:  # a dot inside a long name, not an extension
            root, ext = base, ""
        root = root[: NAME_CHARS - len(ext)].rstrip(". ") or "file"
        if ntpath.isreserved(root + ext):
            root = "_" + root
        made, n = root + ext, 1
        while made.casefold() in taken:  # Windows does not tell «A.txt» from «a.txt»
            n += 1
            made = f"{root} ({n}){ext}"
        taken.add(made.casefold())
        out.append(made)
    return out


def docx_text(data: bytes) -> str:
    """A Word document's text, paragraph by paragraph (tables' cells too, as they come)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise AttachmentError(tr("документ Word не читается ({error})", error=exc)) from None
    lines = []
    for paragraph in root.iter(f"{WORD}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{WORD}tab":
                parts.append("\t")
            elif node.tag in (f"{WORD}br", f"{WORD}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines).strip()


def pdf_text(data: bytes) -> str:
    try:
        import pypdf
    except ImportError:
        raise AttachmentError(tr("PDF пока не читается: нужен пакет pypdf. Сохраните документ как текст или .docx")) \
            from None  # fmt: skip
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:  # a broken or encrypted file
        raise AttachmentError(tr("PDF не читается ({error})", error=exc)) from None


def decode_text(data: bytes) -> str | None:
    """A text file's text; None for one that is not text."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    text = data.decode("cp1251", errors="replace")
    controls = sum(1 for ch in text[:8192] if ord(ch) < 32 and ch not in "\r\n\t\f")
    return None if controls > 8 else text


def blocks(folder: Path, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The message's blocks for the files sent with it ({name, data (base64)}): pictures by reference, text read
    out. Raises AttachmentError with what is wrong, before anything is kept."""
    if len(items) > MAX_FILES:
        raise AttachmentError(tr("не больше {count} файлов в одном сообщении", count=MAX_FILES))
    decoded = []
    total = 0
    unnamed = tr("файл")
    for item in items:
        name = " ".join(str(item.get("name") or unnamed).split())[:200] or unnamed
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except binascii.Error, ValueError:
            raise AttachmentError(tr("«{name}»: файл пришёл повреждённым", name=name)) from None
        if not data:
            raise AttachmentError(tr("«{name}»: файл пустой", name=name))
        if len(data) > MAX_BYTES:
            raise AttachmentError(tr("«{name}»: больше {size} МБ", name=name, size=MAX_BYTES // (1024 * 1024)))
        total += len(data)
        decoded.append((name, data))
    if total > MAX_TOTAL:
        raise AttachmentError(tr("вместе файлы больше {size} МБ", size=MAX_TOTAL // (1024 * 1024)))
    out = []
    for name, data in decoded:
        kind = picture_type(data)
        if kind:
            out.append(
                {"type": "image", "name": name, "media_type": kind, "size": len(data), "blob": keep(folder, data)}
            )
            continue
        lower = name.lower()
        if data.startswith(b"%PDF"):
            text, source = pdf_text(data), "pdf"
        elif lower.endswith(".docx") and data.startswith(b"PK"):
            text, source = docx_text(data), "docx"
        else:
            text, source = decode_text(data), "text"
            if text is None:
                raise AttachmentError(tr("«{name}»: не текст и не картинка — такие файлы модель не прочтёт", name=name))
        if not text.strip():
            raise AttachmentError(tr("«{name}»: в файле нет текста", name=name))
        cut = len(text) > TEXT_CHARS
        out.append({"type": "file", "name": name, "size": len(data), "source": source, "blob": keep(folder, data),
                    "text": clean(text[:TEXT_CHARS]), "cut": cut})  # fmt: skip
    return out


def as_text(block: dict[str, Any]) -> str:
    """A file block as the model reads it."""
    cut = f' cut="first {TEXT_CHARS} characters"' if block.get("cut") else ""
    return f'<attachment name="{block["name"]}"{cut}>\n{block.get("text", "").rstrip(chr(10))}\n</attachment>'


def prompt_text(content: list[dict[str, Any]]) -> str:
    """A user message's text for the model: what was typed, then the files' text."""
    typed = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    files = [as_text(b) for b in content if b.get("type") == "file"]
    pictures = [b["name"] for b in content if b.get("type") == "image"]
    note = f"[pictures attached: {', '.join(pictures)}]" if pictures else ""
    return "\n\n".join(p for p in (typed, *files, note) if p)


def images(folder: Path | None, content: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The message's pictures for the request: [{media_type, data (base64)}] - those still kept."""
    out = []
    for block in content:
        if block.get("type") != "image" or folder is None:
            continue
        data = load(folder, str(block.get("blob") or ""))
        if data is not None:
            out.append({"media_type": block["media_type"], "data": base64.b64encode(data).decode("ascii")})
    return out


def has_images(content: list[dict[str, Any]]) -> bool:
    return any(b.get("type") == "image" for b in content)
