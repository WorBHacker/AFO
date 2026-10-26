"""Where LocalChat keeps its data (D29): database, logs, terminal logs, file snapshots, backups."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_ENV = "LOCALCHAT_DATA_DIR"


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
    def backups(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> DataPaths:
        """Creates the data folder and its subfolders if they are missing."""
        for folder in (self.root, self.logs, self.terminals, self.snapshots, self.blobs, self.backups):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def data_paths() -> DataPaths:
    """The data folder: $LOCALCHAT_DATA_DIR when set (tests use it), otherwise <project>/data."""
    override = os.environ.get(DATA_DIR_ENV)
    return DataPaths(Path(override) if override else PROJECT_ROOT / "data")
