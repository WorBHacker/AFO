"""A project's own checks (61.4): the commands its files say it is checked by - tests, a linter, types - found by the
hub, not written by a model, and only those whose program is on this computer. A coordinator's task asks for them with
the criterion {"checks": true}; they run in the task's copy of the project when its agent ends, and the agent may run
them there itself, unasked, once the user allowed the batch's checks (hub/agents.py, hub/chat.py).

A task's copy is a git worktree: what is not committed is not there - a Python project's environment, a Node
project's node_modules. A Python project's checks call the programs of its own environment (.venv, venv, env) by their
full path; `python -m pytest` puts the copy first on the import path, so it is the copy's code that is tested. A Node
project's scripts need node_modules in the copy, and installing them there is the agent's own command: they are not
offered (61.4). Rust and Go fetch and build into their own caches and the copy's folders."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

TIMEOUT_S = 600  # a project's tests may take long: commands.MAX_TIMEOUT_S
ENVIRONMENTS = (".venv", "venv", "env")


def _scripts(root: Path) -> Path | None:
    """The programs of the project's own Python environment, when it has one."""
    return next((root / name / "Scripts" for name in ENVIRONMENTS if (root / name / "Scripts").is_dir()), None)


def _program(scripts: Path | None, name: str) -> str | None:
    """How a PowerShell line calls a program: the project environment's by its full path, else one on PATH by its
    name; None - neither is here."""
    own = scripts / f"{name}.exe" if scripts is not None else None
    if own is not None and own.is_file():
        return f'& "{own}"'
    return name if shutil.which(name) else None


def _toml(path: Path) -> dict[str, Any]:
    try:
        found = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return {}
    tool = found.get("tool")
    return tool if isinstance(tool, dict) else {}


def found(root: Path) -> list[str]:
    """The project's checks, in the order they are worth running: the cheap ones first."""
    out: list[str] = []
    tool = _toml(root / "pyproject.toml")
    if any((root / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini")):
        scripts = _scripts(root)
        ruff = _program(scripts, "ruff")
        if ruff and ("ruff" in tool or (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file()):
            out.append(f"{ruff} check .")
        mypy = _program(scripts, "mypy")
        if mypy and ("mypy" in tool or (root / "mypy.ini").is_file()):
            out.append(f"{mypy} .")
        tests = "pytest" in tool or (root / "pytest.ini").is_file() or any(
            (root / name).is_dir() for name in ("tests", "test"))  # fmt: skip
        python = _program(scripts, "python")
        if tests and python and _program(scripts, "pytest"):
            out.append(f"{python} -m pytest -q")
    if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
        out.append("cargo test")
    if (root / "go.mod").is_file() and shutil.which("go"):
        out += ["go vet ./...", "go test ./..."]
    return out


def criteria(project: Any) -> list[dict[str, Any]]:
    """A task's criterion {"checks": true} as its project's commands - [] when none is found."""
    return [{"command": command, "project": project.name, "timeout_s": TIMEOUT_S} for command in found(project.root)]
