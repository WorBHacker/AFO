"""A project's own checks (61.4): the commands its files say it is checked by - tests, a linter, types - found by the
hub, not written by a model, and only those whose program is on this computer. A coordinator's task asks for them with
the criterion {"checks": true}; they run in the task's copy of the project when its agent ends, and the agent may run
them there itself, unasked, once the user allowed the batch's checks (hub/agents.py, hub/chat.py).

A task's copy is a git worktree: what is not committed is not there - a Python project's environment, a Node
project's node_modules. A Python project's checks call the programs of its own environment (.venv, venv, env) by their
full path; `python -m pytest` puts the copy first on the import path, so it is the copy's code that is tested. A Node
project's scripts need node_modules in the copy: since «Стабилизация» portion 6 the copy's is a link to the project's
own (hub/sandbox.py `link_packages`, when git ignores it there), so its scripts - lint, types, tests - are offered
when the project has its packages (before: not offered, 61.4). Rust and Go fetch and build into their own caches and
the copy's folders."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

from . import projects

TIMEOUT_S = 600  # a project's tests may take long: commands.MAX_TIMEOUT_S
ENVIRONMENTS = (".venv", "venv", "env")
NODE_SCRIPTS = ("lint", "typecheck", "type-check", "test")  # a Node project's own checks, the cheap ones first
NPM = "npm.cmd" if os.name == "nt" else "npm"  # not npm.ps1: PowerShell may refuse scripts by its policy


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
    out += _node(root)
    if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
        out.append("cargo test")
    if (root / "go.mod").is_file() and shutil.which("go"):
        out += ["go vet ./...", "go test ./..."]
    return out


def _node(root: Path) -> list[str]:
    """A Node project's own scripts that check it (portion 6) - when its packages are here, git ignores them (so a
    task's copy gets them by a link) and npm is on this computer; npm's own «no test specified» is no test."""
    if not (root / "package.json").is_file() or not (root / "node_modules").is_dir() or not shutil.which(NPM):
        return []
    try:
        scripts = json.loads((root / "package.json").read_bytes().decode("utf-8-sig")).get("scripts") or {}
    except OSError, UnicodeDecodeError, ValueError, AttributeError:
        return []
    if not isinstance(scripts, dict) or "node_modules" not in projects.git_ignored(root, {"node_modules"}):
        return []
    out = []
    for name in NODE_SCRIPTS:
        script = scripts.get(name)
        if isinstance(script, str) and script.strip() and "no test specified" not in script:
            out.append(f"{NPM} test" if name == "test" else f"{NPM} run {name}")
    return out


def criteria(project: Any) -> list[dict[str, Any]]:
    """A task's criterion {"checks": true} as its project's commands - [] when none is found."""
    return [{"command": command, "project": project.name, "timeout_s": TIMEOUT_S} for command in found(project.root)]
