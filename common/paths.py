"""Where AFO keeps its data (D29) and what it installs (stage 10.3, D37).

AFO's own folder is `%LOCALAPPDATA%/AFO` (LOCALCHAT_HOME overrides it): its data (database, logs, snapshots...), the
engines the first-run wizard brings (llama.cpp, llama-swap), tools (ripgrep). A checkout that kept its data in
`<project>/data` before (the first computer it was built on) keeps it there; so does a folder under the old name,
LocalChat, that already holds data (10.4b).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_ENV = "LOCALCHAT_DATA_DIR"
HOME_ENV = "LOCALCHAT_HOME"
MODELS_ENV = "LOCALCHAT_MODELS_DIR"
NAME = "AFO"  # the program's name in its folders (10.4b, Q18): the short name only, never spelled out
OLD_NAME = "LocalChat"  # its name before 10.4b


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "hub.db"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def terminals(self) -> Path:
        return self.root / "terminals"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def media(self) -> Path:
        """The pictures and videos the hub made (D55, 29.4)."""
        return self.root / "media"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def computer(self) -> Path:
        """Which computer this is (D58, 35.2): the window reads it before the hub is up."""
        return self.root / "computer.json"

    @property
    def hf_answers(self) -> Path:
        """What Hugging Face said about the models offered (D58, 35.3)."""
        return self.root / "hf_answers.json"

    @property
    def tokens(self) -> Path:
        """Client token files the hub hands to local clients (the app reads tokens/app.token)."""
        return self.root / "tokens"

    @property
    def hub_info(self) -> Path:
        """Written by a running hub: pid, address, version. Clients read it to find the hub."""
        return self.root / "hub.json"

    @property
    def hub_lock(self) -> Path:
        """Held locked by the running hub, so a second copy refuses to start."""
        return self.root / "hub.lock"

    def ensure(self) -> DataPaths:
        """Creates the data folder and its subfolders if they are missing."""
        for folder in (self.root, self.logs, self.terminals, self.snapshots, self.blobs, self.backups, self.tokens):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def app_home() -> Path:
    """AFO's own folder: $LOCALCHAT_HOME, else %LOCALAPPDATA%/AFO (~/.local/share/AFO elsewhere). A folder under the
    old name that already holds data stays the one: llama-swap's config and the database point into it."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    old = base / OLD_NAME
    return old if (old / "data").is_dir() and not (base / NAME / "data").is_dir() else base / NAME


def engines_home() -> Path:
    """Where the first-run wizard puts llama.cpp and llama-swap (10.2)."""
    return app_home() / "engines"


def models_home() -> Path:
    """Where the installer puts models unless told otherwise (10.1; 2026-10-28, the user's word): the `model` folder
    in AFO's own folder - the program's root; $LOCALCHAT_MODELS_DIR overrides it (tests); a program folder one
    may not write to (an install under Program Files) gives AFO's app folder instead."""
    override = os.environ.get(MODELS_ENV)
    if override:
        return Path(override)
    return (PROJECT_ROOT if os.access(PROJECT_ROOT, os.W_OK) else app_home()) / "model"


def data_paths() -> DataPaths:
    """The data folder: $LOCALCHAT_DATA_DIR when set (tests use it); <project>/data when a checkout already keeps its
    data there; otherwise <AFO's own folder>/data."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return DataPaths(Path(override))
    legacy = PROJECT_ROOT / "data"
    return DataPaths(legacy if legacy.is_dir() else app_home() / "data")
