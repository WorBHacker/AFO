"""The first-run wizard's hub side (stage 10.2, D37): whether the computer needs the engine, the engine fetched from a
stand-in GitHub (the build the card wants, archives checked and unpacked), llama-swap's first config and the window's
choice of it, the hub starting it; the first model for each purpose; a word on a small page file."""

import hashlib
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from common import hardware
from hub import autostart, engines, setup
from hub import llama_config as lc
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def archive(*names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        for name in names:
            zf.writestr(name, b"MZ" + name.encode())
    return data.getvalue()


FILES = {
    "llama-b200-bin-win-vulkan-x64.zip": archive("llama-server.exe", "ggml-vulkan.dll"),
    "llama-b200-bin-win-cuda-13.4-x64.zip": archive("llama-server.exe", "ggml-cuda.dll"),
    "cudart-llama-bin-win-cuda-13.4-x64.zip": archive("cudart64_13.dll"),
    "llama-swap_300_windows_amd64.zip": archive("llama-swap.exe", "LICENSE.md"),
}


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _assets(self, *names):
        return [{"name": n, "size": len(FILES[n]), "digest": "sha256:" + hashlib.sha256(FILES[n]).hexdigest(),
                 "browser_download_url": f"http://127.0.0.1:{self.port}/download/{n}"} for n in names]  # fmt: skip

    def do_GET(self):
        if self.path.startswith("/repos/ggml-org/llama.cpp/releases"):
            builds = [n for n in FILES if "llama-swap" not in n]
            self._send([{"tag_name": "v0.7.0", "assets": []}, {"tag_name": "b200", "assets": self._assets(*builds)}])
        elif self.path == "/repos/mostlygeek/llama-swap/releases/latest":
            self._send({"tag_name": "v300", "assets": self._assets("llama-swap_300_windows_amd64.zip")})
        elif self.path.startswith("/download/"):
            self._send(FILES[self.path.removeprefix("/download/")], "application/zip")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def github(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    FakeGitHub.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    yield server
    server.shutdown()


def test_the_page_file_and_the_first_models():
    assert setup.page_file({"commit_limit_gib": 95.4, "ram_total_gib": 63.4}) == {"gib": 32.0, "advice": ""}
    small = setup.page_file({"commit_limit_gib": 36.0, "ram_total_gib": 32.0})
    assert small["gib"] == 4.0 and "Виртуальная память" in small["advice"] and setup.page_file(None)["gib"] is None

    def model(mid, purposes, size, verdict):
        return {"id": mid, "purposes": purposes, "recommended": {"size": size, "verdict": verdict}}

    found = setup.suggestions([model("small", ["chat"], 2, "gpu"), model("big", ["chat", "code"], 12, "gpu"),
                               model("huge", ["chat"], 40, "split"), model("eyes", ["vision"], 30, "split"),
                               model("eyes2", ["vision"], 20, "split"), {"id": "no", "purposes": ["code"],
                               "recommended": None}])  # fmt: skip
    assert {p: m["id"] if m else None for p, m in found.items()} == {"chat": "big", "code": "big", "vision": "eyes2"}


def test_the_engine_for_a_computer_that_has_none(hub, github, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(hardware, "adapters", lambda: [{"name": "Radeon RX 9070", "vendor": "amd", "vram_gib": 16.0,
                                                        "driver": "32.0"}])  # fmt: skip
    monkeypatch.setattr(hardware, "nvidia_driver", lambda: None)
    started = []

    async def fake_start(url, script, wanted, log_file, **kwargs):
        started.append((url, script, autostart.start_command(script, url)))
        return "started"

    monkeypatch.setattr(autostart, "ensure_llama_swap", fake_start)
    first = hub.get("/v1/setup").json()
    assert first["needed"] and first["build"]["build"] == "vulkan" and first["cards"][0]["name"] == "Radeon RX 9070"
    assert first["engine"]["config_found"] is False and first["job"]["status"] == "idle"
    assert hub.post("/v1/setup/engine", json={"build": "metal"}).status_code == 422

    job = hub.post("/v1/setup/engine", json={}).json()["job"]
    assert job["status"] == "downloading" and job["build"] == "vulkan"
    deadline = time.monotonic() + 20
    while hub.get("/v1/setup").json()["job"]["status"] in ("downloading", "starting"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    done = hub.get("/v1/setup").json()["job"]
    assert done["status"] == "done", done
    swap = tmp_path / "localchat-home" / "engines" / "llama-swap"
    server = tmp_path / "localchat-home" / "engines" / "llama.cpp" / "b200-vulkan" / "llama-server.exe"
    assert server.read_bytes() == b"MZllama-server.exe" and (swap / "llama-swap.exe").exists()
    text = (swap / "config.yaml").read_text(encoding="utf-8")
    assert lc.macros(text)["llama_server"] == '"' + str(server).replace("\\", "/") + '"'
    assert lc.configured == swap / "config.yaml" and autostart.configured_start == swap / "llama-swap.exe"
    _url, script, command = started[0]
    assert script == swap / "llama-swap.exe" and command[1:3] == ["--config", str(lc.config_path())]
    monkeypatch.delenv(lc.CONFIG_ENV)
    monkeypatch.delenv(autostart.LLAMA_SWAP_START_ENV)
    after = hub.get("/v1/setup").json()
    assert not after["needed"] and after["engine"]["server"] == "${llama_server}"  # the wizard's config, chosen
    events = hub.get("/v1/feed", params={"types": "setup.changed"}).json()["events"]
    assert "done" in [e["payload"]["job"]["status"] for e in events]

    again = hub.post("/v1/setup/engine", json={"build": "cuda-13"}).json()["job"]  # another build beside it
    while hub.get("/v1/setup").json()["job"]["status"] in ("downloading", "starting"):
        time.sleep(0.05)
    assert again["build"] == "cuda-13"
    cuda = tmp_path / "localchat-home" / "engines" / "llama.cpp" / "b200-cuda-13"
    assert (cuda / "cudart64_13.dll").exists() and (cuda / "llama-server.exe").exists()
    assert (swap / "config.yaml").read_text(encoding="utf-8") == text  # an existing config is never overwritten


def test_which_build_runs_and_the_macro_it_lives_in(tmp_path):
    cuda = tmp_path / "llama.cpp" / "b11899"
    cuda.mkdir(parents=True)
    for name in ("llama-server.exe", "ggml-cuda.dll", "cudart64_13.dll"):
        (cuda / name).write_bytes(b"MZ")
    assert engines.running_build(cuda / "llama-server.exe") == {"tag": "b11899", "build": "cuda-13"}
    vulkan = tmp_path / "engines" / "llama.cpp" / "b200-vulkan"
    vulkan.mkdir(parents=True)
    (vulkan / "llama-server.exe").write_bytes(b"MZ")
    (vulkan / "ggml-vulkan.dll").write_bytes(b"MZ")
    assert engines.running_build(vulkan / "llama-server.exe") == {"tag": "b200", "build": "vulkan"}
    assert engines.running_build(tmp_path / "nowhere" / "llama-server.exe") is None
    text = """macros:
  llama_server: '"D:/old/llama-server.exe"'   # the build
  other: "x"
models:
  "a":
    cmd: |
      ${llama_server} -m a.gguf
"""
    new = lc.set_macro(text, "llama_server", "'\"E:/new/llama-server.exe\"'")
    assert lc.macros(new) == {"llama_server": '"E:/new/llama-server.exe"', "other": "x"}
    assert "# the build" in new and new.replace("E:/new", "D:/old") == text


def test_the_engine_is_updated_beside_the_old_one(hub, github, monkeypatch, tmp_path):  # noqa: F811
    old = tmp_path / "llama.cpp" / "b100"
    old.mkdir(parents=True)
    for name in ("llama-server.exe", "ggml-vulkan.dll"):
        (old / name).write_bytes(b"MZ")
    config = tmp_path / "config.yaml"
    config.write_text(f"""macros:
  llama_server: '"{(old / "llama-server.exe").as_posix()}"'
models:
  "a":
    cmd: |
      ${{llama_server}} -m a.gguf
      --port ${{PORT}}
""", encoding="utf-8")  # fmt: skip
    monkeypatch.setenv(lc.CONFIG_ENV, str(config))
    monkeypatch.setattr(lc, "RELOAD_S", 0.5)  # no llama-swap here: the write is kept, not checked
    found = hub.get("/v1/setup/update").json()
    assert found == {"current": "b100", "build": "vulkan", "latest": "b200", "newer": True, "server": str(old /
                     "llama-server.exe")}  # fmt: skip
    job = hub.post("/v1/setup/update").json()["job"]
    assert job["status"] == "downloading" and job["update"]
    deadline = time.monotonic() + 20
    while hub.get("/v1/setup").json()["job"]["status"] in ("downloading", "starting"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    done = hub.get("/v1/setup").json()["job"]
    assert done["status"] == "done", done
    new = tmp_path / "localchat-home" / "engines" / "llama.cpp" / "b200-vulkan" / "llama-server.exe"
    text = config.read_text(encoding="utf-8")
    assert lc.macros(text)["llama_server"] == '"' + new.as_posix() + '"' and set(lc.model_blocks(text)) == {"a"}
    assert (old / "llama-server.exe").exists()  # the old build stays (a model may still run on it)
    assert list(tmp_path.glob("config.yaml.bak-*"))  # a copy of the config first
    assert hub.get("/v1/setup/update").json()["newer"] is False
