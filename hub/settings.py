"""Where the hub listens and keeps its data (D28, D29)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from common.paths import DataPaths, data_paths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_LLAMA_SWAP_URL = "http://127.0.0.1:8080"
PORT_ENV = "LOCALCHAT_HUB_PORT"
LLAMA_SWAP_URL_ENV = "LOCALCHAT_LLAMA_SWAP_URL"
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class HubSettings:
    paths: DataPaths
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # D31: only requests addressed to this computer, and none sent by a web page
    allowed_hostnames: frozenset[str] = LOCAL_HOSTNAMES
    allowed_origins: frozenset[str] = frozenset()
    llama_swap_url: str = DEFAULT_LLAMA_SWAP_URL
    status_interval_s: float = 2.0  # GPU / memory / llama-swap sampling; 0 turns the monitor off

    @classmethod
    def from_env(cls) -> HubSettings:
        return cls(
            paths=data_paths(),
            port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)),
            llama_swap_url=os.environ.get(LLAMA_SWAP_URL_ENV, DEFAULT_LLAMA_SWAP_URL),
        )
