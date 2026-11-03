"""Starting: the hub from nothing to /v1/health, the window built and closed (`python -m app --smoke`)."""

import os
import subprocess
import sys
import time

import httpx
from test_entry_points import free_port, stop_tree

from common.paths import DATA_DIR_ENV, PROJECT_ROOT
from hub.settings import LLAMA_SWAP_URL_ENV, PORT_ENV

from .stand import Clock, note


def test_the_hub_starts(isolated_data_dir):
    port = free_port()
    # nor a llama-swap that happens to run: its answer is not the hub's start
    env = {
        **os.environ,
        DATA_DIR_ENV: str(isolated_data_dir),
        PORT_ENV: str(port),
        LLAMA_SWAP_URL_ENV: "http://127.0.0.1:9",
    }
    times = []
    for _ in range(3):  # the first start makes the database; the next ones are a usual morning
        started = time.perf_counter()
        hub = subprocess.Popen([sys.executable, "-m", "hub"], cwd=PROJECT_ROOT, env=env)
        try:
            while True:
                assert time.perf_counter() - started < 60, "the hub did not answer /v1/health"
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.02)
            times.append(time.perf_counter() - started)
        finally:
            stop_tree(hub)
    note("хаб: первый старт до /v1/health", times[0], "с", 5)
    note("хаб: старт с готовой базой", min(times[1:]), "с", 5)


def test_the_window_builds(isolated_data_dir):
    env = {**os.environ, DATA_DIR_ENV: str(isolated_data_dir), "QT_QPA_PLATFORM": "offscreen"}
    times = []
    for _ in range(2):
        with Clock() as clock:
            done = subprocess.run([sys.executable, "-m", "app", "--smoke"], cwd=PROJECT_ROOT, env=env,
                                  capture_output=True, timeout=120, check=False)  # fmt: skip
        assert done.returncode == 0, done.stderr
        times.append(clock.s)
    note("окно: python -m app --smoke", min(times), "с", 5)
