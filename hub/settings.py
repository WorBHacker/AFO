"""Where the hub listens and keeps its data (D28, D29)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from common.paths import DataPaths, data_paths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_ENV = "LOCALCHAT_HUB_PORT"
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class HubSettings:
    paths: DataPaths
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # D31: only requests addressed to this computer, and none sent by a web page
    allowed_hostnames: frozenset[str] = LOCAL_HOSTNAMES
    allowed_origins: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> HubSettings:
        return cls(paths=data_paths(), port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)))
