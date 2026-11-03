"""«Открыть в VS Code» (D47, step 14.4): the open file at its line, in the user's VS Code.

VS Code's own `code.cmd` would take the path through cmd.exe, where a file named with `&` becomes a command; so its
Code.exe is run itself, with `-g path:line` - an open VS Code takes the file in its window.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FOLDER = "Microsoft VS Code"


def find() -> Path | None:
    """Code.exe: beside the `code` on PATH (…/bin/code.cmd), else where its installers put it."""
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder and (Path(folder) / "code.cmd").is_file():
            exe = Path(folder).parent / "Code.exe"
            if exe.is_file():
                return exe
    local = os.environ.get("LOCALAPPDATA")
    roots = [Path(local) / "Programs"] if local else []
    roots += [Path(v) for v in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")) if v]
    return next((exe for root in roots if (exe := root / FOLDER / "Code.exe").is_file()), None)


def open_at(path: Path, line: int, *, exe: Path | None = None, popen=subprocess.Popen) -> bool:
    """False when VS Code is not found."""
    exe = exe or find()
    if exe is None:
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    popen([str(exe), "-g", f"{path}:{max(1, line)}"], creationflags=flags, close_fds=True,
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    return True
