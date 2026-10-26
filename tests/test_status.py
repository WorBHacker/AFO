"""System status (GPU, memory, llama-swap) and freeing the GPU."""

import asyncio

import httpx
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.server import create_app
from hub.settings import HubSettings
from hub.system_status import GpuProbe, StatusMonitor, llama_swap_sample, memory_sample

BASE = "http://127.0.0.1:8765"


def test_probes_never_raise():
    gpu = GpuProbe().sample()  # None without an NVIDIA driver
    assert gpu is None or gpu["vram_total_mib"] > 0
    memory = memory_sample()  # None off Windows
    assert memory is None or memory["commit_limit_gib"] >= memory["ram_total_gib"] > 0


def test_llama_swap_running_and_unreachable():
    def handler(request):
        assert request.url.path == "/running"
        return httpx.Response(200, json={"running": [{"model": "coder", "state": "ready", "cmd": "x"}]})

    sample = asyncio.run(llama_swap_sample("http://x", transport=httpx.MockTransport(handler)))
    assert sample == {"reachable": True, "running": [{"model": "coder", "state": "ready"}]}
    down = asyncio.run(llama_swap_sample("http://x", transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    assert down == {"reachable": False, "running": []}


class FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, event_type, payload=None, *, persist=True):
        self.published.append((event_type, payload, persist))


class FixedGpu:
    def __init__(self):
        self.used = 1000

    def sample(self):
        return {"name": "GPU", "vram_used_mib": self.used, "vram_total_mib": 16000, "util_pct": 0, "temp_c": 30}


def test_monitor_publishes_only_changes_and_does_not_store_them(monkeypatch):
    # real RAM use moves between two samples; only the GPU change below should count
    monkeypatch.setattr("hub.system_status.memory_sample", lambda: {"ram_used_gib": 1.0})
    bus, gpu = FakeBus(), FixedGpu()
    monitor = StatusMonitor(bus, "http://127.0.0.1:9", interval_s=0, gpu=gpu)

    async def three_samples():
        await monitor.sample()
        await monitor.sample()  # nothing changed
        gpu.used = 9000
        return await monitor.sample()

    latest = asyncio.run(three_samples())
    assert [(t, persist) for t, _, persist in bus.published] == [("system.status", False)] * 2
    assert latest["gpu"]["vram_used_mib"] == 9000
    assert latest["local"]["reachable"] is False


def test_status_and_unload_endpoints():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    with TestClient(app, base_url=BASE) as hub:
        headers = {"Authorization": f"Bearer {(data_paths().tokens / 'app.token').read_text(encoding='utf-8')}"}
        status = hub.get("/v1/status", headers=headers).json()
        assert set(status) == {"gpu", "memory", "local", "busy"}
        assert status["local"]["reachable"] is False
        assert hub.get("/v1/status").status_code == 401
        unload = hub.post("/v1/models/unload", headers=headers)
        assert unload.status_code == 502  # llama-swap is not there
        assert "llama-swap" in unload.json()["detail"]
