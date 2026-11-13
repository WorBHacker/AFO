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
    # the port asked for, when another program held it and the hub listens on `port` instead (43.9); None: as asked
    wanted_port: int | None = None
    # D31: only requests addressed to this computer, and none sent by a web page
    allowed_hostnames: frozenset[str] = LOCAL_HOSTNAMES
    allowed_origins: frozenset[str] = frozenset()
    llama_swap_url: str = DEFAULT_LLAMA_SWAP_URL
    status_interval_s: float = 2.0  # GPU / memory / llama-swap sampling; 0 turns the monitor off
    process_interval_s: float = 3.0  # processes and ports in project folders; 0: only when asked
    manage_llama_swap: bool = False  # start llama-swap when it does not answer (Q8); the real hub, not tests
    # another program on llama-swap's port: AFO's own llama-swap to a free one (39.2); not with an address set by hand
    move_llama_swap: bool = False
    schedule_tick_s: float = 0.0  # how often schedules are looked at (9.7); 0: never by themselves - tests
    programs_interval_s: float = 0.0  # looks for new programs (D43); 0: only when asked - tests never scan
    backup_interval_s: float = 0.0  # checks for today's database copy (D29); 0: never
    folder_check_s: float = 0.0  # projects' folders gone, moved, back (hub/watch.py); 0: never by themselves - tests
    links_quiet_s: float = 0.0  # links looked for again this long after a change (hub/links.py); 0: never - tests
    fetch_ripgrep: bool = False  # ripgrep from its GitHub release when there is none (hub/ripgrep.py); not in tests
    fetch_engines: bool = False  # llama.cpp and llama-swap fetched when AFO's own are missing (39.1); not in tests
    gpu_interval_s: float = 0.0  # programs on the card looked at (43.6); 0: never by themselves - tests never move

    @classmethod
    def from_env(cls) -> HubSettings:
        return cls(
            paths=data_paths(),
            port=int(os.environ.get(PORT_ENV, DEFAULT_PORT)),
            llama_swap_url=os.environ.get(LLAMA_SWAP_URL_ENV, DEFAULT_LLAMA_SWAP_URL),
            manage_llama_swap=True,
            move_llama_swap=LLAMA_SWAP_URL_ENV not in os.environ,  # an address set by hand stays as it is (39.2)
            programs_interval_s=600.0,
            schedule_tick_s=20.0,
            backup_interval_s=3600.0,
            folder_check_s=5.0,
            links_quiet_s=20.0,
            fetch_ripgrep=True,
            fetch_engines=True,
            gpu_interval_s=30.0,
        )
