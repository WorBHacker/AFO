"""AFO's installer (step 10.4c, Q16): the embeddable Python and the libraries at their tested versions, the wizard
(installer/afo.iss) in step with the program, the icon, and the agent's scripts' Python taken from the embedded one.
Nothing is downloaded and Inno Setup is not run here: the build itself is checked by hand (docs/journal.md)."""

import io
import re
import struct
import sys
from pathlib import Path

import pytest

from hub import appcontainer
from installer import build

ROOT = Path(__file__).resolve().parent.parent


def name(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def test_the_programs_libraries_at_their_tested_versions():
    """The hub's and the window's libraries, each pinned by requirements.lock; the dev tools stay out."""
    names = {name(r) for r in build.requirements()}
    assert {"pyside6-essentials", "fastapi", "uvicorn", "tree-sitter", "pywinpty", "pyte", "markdown-it-py"} <= names
    assert not names & {"ruff", "pytest", "pip-audit", "httpx2"}
    pinned = build.pins()
    assert all("==" in p and not p.startswith(("#", "-")) for p in pinned)
    assert names <= {name(p) for p in pinned}  # pip takes exactly the versions the tests ran with
    assert build.PYTHON.rsplit(".", 1)[0] == f"{sys.version_info.major}.{sys.version_info.minor}"  # the wheels' Python


def test_the_embedded_python_finds_the_code_from_anywhere():
    """The login value runs `pythonw -m hub` from any folder: the path file names AFO's code one level up."""
    assert build.pth("python314.zip").splitlines() == ["python314.zip", ".", "Lib\\site-packages", "..", "import site"]


def test_a_download_is_kept_only_with_its_sha256(tmp_path):
    import hashlib

    good = hashlib.sha256(b"python").hexdigest()
    opened = []

    def opener(url):
        opened.append(url)
        return io.BytesIO(b"python")

    got = build.fetch("https://example.org/f/python-embed.zip", good, tmp_path, opener=opener)
    assert got == tmp_path / "python-embed.zip" and got.read_bytes() == b"python"
    assert build.fetch("https://example.org/f/python-embed.zip", good, tmp_path, opener=opener) == got
    assert len(opened) == 1  # kept: not downloaded again
    with pytest.raises(build.BuildError, match="sha256"):
        build.fetch("https://example.org/f/other.zip", "0" * 64, tmp_path, opener=opener)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["python-embed.zip"]  # nothing of the wrong file is left


def test_a_folder_too_deep_for_the_programs_files_is_refused_in_words(tmp_path):
    """Windows' paths end at 259 characters, and some of the libraries' files lie 170 deep: a folder chosen too deep
    would stop the install half way with «path not found». The build measures the deepest file; the wizard refuses
    a longer folder with a sentence, before anything is written."""
    (tmp_path / "python" / "Lib" / "deep").mkdir(parents=True)
    (tmp_path / "python" / "Lib" / "deep" / ("x" * 40 + ".py")).write_text("", encoding="utf-8")
    (tmp_path / "afo.ico").write_bytes(b"x")
    deepest = len("python\\Lib\\deep\\" + "x" * 40 + ".py")
    assert build.room(tmp_path) == 259 - 1 - deepest
    iss = build.ISS.read_text(encoding="utf-8")
    assert "Length(ExpandConstant('{app}')) > {#MaxDir}" in iss and "russian.DirTooLong=" in iss
    assert "english.DirTooLong=" in iss


WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_the_icon_has_every_size_windows_asks_for():
    from PySide6.QtWidgets import QApplication

    from app.ui import brand

    _app = QApplication.instance() or QApplication([])
    data = brand.ico()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, len(brand.SIDES))
    sides = []
    for i in range(count):
        width, height, _c, _r, _planes, _bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        sides.append(width or 256)
        assert height == width  # square
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + size <= len(data)
    assert sides == list(brand.SIDES) and 16 in sides and 256 in sides
    # the user's logo: transparent around the bubble, its white inside kept (it reads on a dark taskbar too)
    for side in (16, 64, 256):
        picture = brand.picture(side)
        assert (picture.width(), picture.height()) == (side, side) and picture.hasAlphaChannel()
        assert all(picture.pixelColor(x, y).alpha() == 0 for x, y in ((0, 0), (side - 1, 0), (0, side - 1)))
        if side >= 64:  # between the bubble's top and the </+>: its white inside
            inside = picture.pixelColor(side // 2, int(side * 0.17))
            assert inside.alpha() == 255 and inside.lightness() > 230


def test_the_installer_lets_the_programs_files_go(tmp_path, monkeypatch):
    """`python -m hub stop --program`: before files are replaced or removed, every other process of the installed
    program's Python ends - the window, a hub that did not stop. Only the embedded Python (its ._pth beside it): a
    checkout's Python is shared with other programs, and nothing of theirs is touched."""
    import psutil

    from hub import __main__ as hub_main

    python = tmp_path / "AFO" / "python"
    python.mkdir(parents=True)
    (python / "python.exe").write_bytes(b"x")

    class Process:
        def __init__(self, pid, exe):
            self.pid, self.info, self.ended = pid, {"exe": exe}, False

        def terminate(self):
            self.ended = True

    window = Process(11, str(python / "pythonw.exe"))
    hub = Process(12, str(python / "pythonw.exe"))
    other = Process(13, str(tmp_path / "Python314" / "pythonw.exe"))
    gone = Process(14, None)  # access denied: no exe
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([window, hub, other, gone]))
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (procs, []))
    assert hub_main.let_go(python / "python.exe") == 1  # no ._pth: not an installed program
    assert not any(p.ended for p in (window, hub, other))
    (python / "python314._pth").write_text(build.pth("python314.zip"), encoding="utf-8")
    assert hub_main.let_go(python / "python.exe") == 0
    assert (window.ended, hub.ended, other.ended, gone.ended) == (True, True, False, False)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_the_scripts_python_from_the_embedded_one(tmp_path, monkeypatch):
    """Installed, the hub runs on the embeddable Python - one folder, the standard library zipped. The scripts' copy
    takes its core and a path file of its own: no AFO code, no libraries."""
    base = tmp_path / "AFO" / "python"
    (base / "Lib" / "site-packages" / "PySide6").mkdir(parents=True)
    for file in ("python.exe", "pythonw.exe", "python314.dll", "python3.dll", "python314.zip", "_socket.pyd",
                 "sqlite3.dll", "LICENSE.txt"):  # fmt: skip
        (base / file).write_bytes(b"x")
    (base / "python314._pth").write_text(build.pth("python314.zip"), encoding="utf-8")
    monkeypatch.setattr(appcontainer.sys, "base_prefix", str(base))
    monkeypatch.setattr(appcontainer, "grant", lambda folder, rights: None)
    python = appcontainer.runtime(tmp_path / "home")
    copy = python.parent
    assert sorted(p.name for p in copy.iterdir()) == ["_socket.pyd", "python.exe", "python3.dll", "python314._pth",
                                                      "python314.dll", "python314.zip", "pythonw.exe",
                                                      "sqlite3.dll"]  # fmt: skip
    assert (copy / "python314._pth").read_text(encoding="utf-8") == "python314.zip\n.\n"
