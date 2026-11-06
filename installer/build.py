"""AFO's installer (step 10.4c, Q16): the program's folder - the embeddable Python from python.org (its sha256
checked), the libraries at the versions in requirements.lock, AFO's code and icon - and Inno Setup's wizard around it
(installer/afo.iss).

    .venv/Scripts/python.exe -m installer.build              build/AFO, checked, then build/dist/AFO-Setup-<version>.exe
                                                             and its .sha256
    .venv/Scripts/python.exe -m installer.build --folder     the folder only
    .venv/Scripts/python.exe -m installer.build --checksum F   F.sha256 only (again after signing)

A release is built the same way by GitHub Actions from the repository (.github/workflows/release.yml, 20.6).

Not one frozen .exe (PyInstaller, Nuitka): the hub runs Python itself - the agent's scripts, `python -m hub` from the
window and at login, the MCP bridge `python -m hub mcp`, the syntax check - and a frozen program has none. The build
needs the network (python.org, PyPI) and Inno Setup 6 (`winget install JRSoftware.InnoSetup`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
ISS = Path(__file__).with_name("afo.iss")
PYTHON = "3.14.7"  # the one the tests run on: the libraries' wheels are for it
EMBED = f"https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip"
EMBED_SHA256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"  # python.org's list of the files
EXTRAS = ("hub", "app")  # the program's libraries; `dev` (ruff, pytest, pip-audit) stays out
CODE = ("hub", "app", "common")
FILES = ("LICENSE",)  # beside the code in the program's folder


class BuildError(Exception):
    """The message says what to do."""


def requirements(pyproject: Path = ROOT / "pyproject.toml") -> list[str]:
    """The hub's and the window's libraries, as pyproject.toml asks for them."""
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    return list(dict.fromkeys(r for extra in EXTRAS for r in extras[extra]))


def pins(lock: Path = ROOT / "requirements.lock") -> list[str]:
    """requirements.lock's `name==version` lines: pip takes exactly the versions the tests ran with."""
    lines = (line.strip() for line in lock.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if "==" in line and not line.startswith(("#", "-"))]


def pth(stdlib: str) -> str:
    """The embedded Python's path file: its standard library, its folder, the libraries, and AFO's code one level up -
    whatever the current folder is (the login value runs `pythonw -m hub` from anywhere)."""
    return f"{stdlib}\n.\nLib\\site-packages\n..\nimport site\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, sha256: str, folder: Path, opener: Callable[[str], Any] = urllib.request.urlopen) -> Path:
    """The file at `url`, kept in `folder` only when its sha256 is `sha256`; a kept one is not downloaded again."""
    target = folder / url.rsplit("/", 1)[-1]
    if target.is_file() and _sha256(target) == sha256:
        return target
    folder.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with opener(url) as answer, part.open("wb") as out:
        shutil.copyfileobj(answer, out)
    if _sha256(part) != sha256:
        part.unlink()
        raise BuildError(f"{url}: the sha256 is not the one published - the file is not used")
    part.replace(target)
    return target


def iscc() -> Path | None:
    """Inno Setup's compiler: installed for this user or for everyone, or on PATH."""
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs", Path(os.environ.get("PROGRAMFILES(X86)", "")),
                 Path(os.environ.get("PROGRAMFILES", ""))):  # fmt: skip
        if (base / "Inno Setup 6" / "ISCC.exe").is_file():
            return base / "Inno Setup 6" / "ISCC.exe"
    found = shutil.which("iscc")
    return Path(found) if found else None


def _run(command: list[str], **options: Any) -> None:
    print("  $", " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, check=True, **options)


def folder(out: Path = BUILD) -> Path:
    """The program's folder as it will be installed: out/AFO."""
    if sys.version.split()[0] != PYTHON:
        raise BuildError(f"build with Python {PYTHON} (the .venv's): the libraries' wheels must be the embedded one's")
    program = out / "AFO"
    shutil.rmtree(program, ignore_errors=True)
    python = program / "python"
    python.mkdir(parents=True)
    print("Python", PYTHON, "(embeddable, python.org)", flush=True)
    with zipfile.ZipFile(fetch(EMBED, EMBED_SHA256, out / "cache")) as archive:
        archive.extractall(python)
    stdlib = next(python.glob("python3*.zip"))
    for old in python.glob("python3*._pth"):
        old.unlink()
    stdlib.with_suffix("._pth").write_text(pth(stdlib.name), encoding="utf-8", newline="\r\n")
    print("the libraries (requirements.lock)", flush=True)
    constraints = out / "constraints.txt"
    constraints.write_text("\n".join(pins()) + "\n", encoding="utf-8")
    site = python / "Lib" / "site-packages"
    _run([sys.executable, "-m", "pip", "install", "--target", str(site), "--only-binary=:all:", "--no-compile",
          "--disable-pip-version-check", "--no-warn-script-location", "--constraint", str(constraints),
          *requirements()])  # fmt: skip
    shutil.rmtree(site / "bin", ignore_errors=True)  # the libraries' console commands: AFO runs none of them
    print("AFO's code", flush=True)
    for part in CODE:
        shutil.copytree(ROOT / part, program / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for file in FILES:
        shutil.copy2(ROOT / file, program / file)
    (program / "afo.ico").write_bytes(_icon())
    # compiled here, so the first start need not do it
    _run([str(python / "python.exe"), "-m", "compileall", "-q", "-j", "0", *(str(program / part) for part in CODE),
          str(site)], stdout=subprocess.DEVNULL)  # fmt: skip
    return program


def _icon() -> bytes:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    from app.ui import brand

    _app = QGuiApplication.instance() or QGuiApplication(["build"])
    return brand.ico()


def isolated(scratch: Path) -> dict[str, str]:
    """The environment of the checks: nothing of the user's - their data, keys, login value, llama-swap, Claude Code,
    Hugging Face, GitHub - as tests/conftest.py gives every test, and a port of its own."""
    from common.paths import DATA_DIR_ENV, HOME_ENV, MODELS_ENV
    from hub.autostart import LLAMA_SWAP_START_ENV, RUN_STORE_ENV
    from hub.claude_code import CLI_ENV
    from hub.engines import GITHUB_ENV
    from hub.hf import BASE_ENV as HF_ENV
    from hub.llama_config import CONFIG_ENV
    from hub.secrets import STORE_ENV

    env = {k: v for k, v in os.environ.items() if not k.startswith(("LOCALCHAT_", "PYTHON"))}
    env.update({DATA_DIR_ENV: str(scratch / "data"), HOME_ENV: str(scratch / "home"),
                MODELS_ENV: str(scratch / "model"), STORE_ENV: "memory", RUN_STORE_ENV: "memory",
                CLI_ENV: str(scratch / "no-claude.exe"), HF_ENV: "http://127.0.0.1:9", GITHUB_ENV: "http://127.0.0.1:9",
                CONFIG_ENV: str(scratch / "no-llama-swap" / "config.yaml"),
                LLAMA_SWAP_START_ENV: str(scratch / "no-llama-swap" / "start.cmd"),
                "LOCALCHAT_LLAMA_SWAP_URL": "http://127.0.0.1:9", "LOCALCHAT_HUB_PORT": "8799",
                "QT_QPA_PLATFORM": "offscreen"})  # fmt: skip
    return env


def check(program: Path) -> None:
    """The folder works on its own: its Python finds the code and the libraries, and the window is built."""
    python = program / "python" / "python.exe"
    with tempfile.TemporaryDirectory(prefix="afo-check-") as scratch:
        env = isolated(Path(scratch))
        _run([str(python), "-c", "import hub.server, hub.mcp_server, app.ui.main, winpty, tree_sitter_python, "
              "PySide6.QtQuick; print('  imports: ok')"], env=env, cwd=scratch)  # fmt: skip
        _run([str(python), "-m", "app", "--smoke"], env=env, cwd=scratch)


def room(program: Path) -> int:
    """How long the install folder's path may be: Windows' paths end at 259 characters, and the deepest of the
    program's files must fit below it."""
    return 259 - 1 - max(len(str(p.relative_to(program))) for p in program.rglob("*"))


def installer(program: Path, out: Path = BUILD) -> Path:
    """Inno Setup's wizard around the folder: out/dist/AFO-Setup-<version>.exe."""
    from common import __version__

    compiler = iscc()
    if compiler is None:
        raise BuildError("Inno Setup 6 is not installed: winget install JRSoftware.InnoSetup")
    dist = out / "dist"
    _run([str(compiler), "/Qp", f"/DAppVersion={__version__}", f"/DSource={program}", f"/DOutputDir={dist}",
          f"/DMaxDir={room(program)}", str(ISS)])  # fmt: skip
    return dist / f"AFO-Setup-{__version__}.exe"


def checksum(made: Path) -> Path:
    """`<sha256>  <name>` beside the installer, as sha256sum writes it: the hub's update compares it with GitHub's
    digest (hub/release.py). A signature changes the file, so after signing it is written again."""
    target = made.with_name(made.name + ".sha256")
    target.write_text(f"{_sha256(made)}  {made.name}\n", encoding="ascii", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m installer.build", description="AFO's installer")
    parser.add_argument("--folder", action="store_true", help="the program's folder only, no installer")
    parser.add_argument("--checksum", type=Path, metavar="FILE", help="only FILE.sha256 beside FILE (after signing)")
    args = parser.parse_args(argv)
    if args.checksum:
        print("ready:", checksum(args.checksum))
        return 0
    try:
        program = folder()
        check(program)
        if args.folder:
            print("ready:", program)
            return 0
        made = installer(program)
        checksum(made)
    except (BuildError, subprocess.CalledProcessError) as exc:
        print("not built:", exc, file=sys.stderr)
        return 1
    print(f"ready: {made} ({made.stat().st_size / 2**20:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
