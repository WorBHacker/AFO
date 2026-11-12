"""The first-run wizard's hub side (stage 10.2, D37): whether the computer needs the engine, the engine fetched from a
stand-in GitHub (the build the card wants, archives checked and unpacked), llama-swap's first config and the window's
choice of it, the hub starting it; the first model for each purpose; a word on a small page file."""

import contextlib
import hashlib
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from common import hardware
from common.paths import data_paths
from hub import autostart, engines, setup
from hub import llama_config as lc
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_tools import BASE, hub  # noqa: F401 - the hub fixture


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
    asked: ClassVar[list] = []  # the paths asked for (39.1: GitHub allows 60 an hour)
    limited = False  # 403 to every question: the hour's limit is spent

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
        FakeGitHub.asked.append(self.path)
        if self.limited and self.path.startswith("/repos/"):
            self.send_response(403)
            self.send_header("content-length", "0")
            self.end_headers()
        elif self.path.startswith("/repos/ggml-org/llama.cpp/releases"):
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
    FakeGitHub.port, FakeGitHub.asked, FakeGitHub.limited = server.server_port, [], False
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

    async def taken_start(url, script, wanted, log_file, **kwargs):
        return "taken"  # 23.4: another program holds llama-swap's port

    monkeypatch.setattr(autostart, "ensure_llama_swap", taken_start)
    monkeypatch.setattr(autostart, "port_taken", lambda url: {"port": 8080, "pid": 4242, "name": "strata.exe"})
    hub.post("/v1/setup/engine", json={"build": "cuda-13"})
    while hub.get("/v1/setup").json()["job"]["status"] in ("downloading", "starting"):
        time.sleep(0.05)
    failed = hub.get("/v1/setup").json()["job"]
    assert failed["status"] == "failed" and "порт 8080 занят: strata.exe (pid 4242)" in failed["error"]


class Tomcat(BaseHTTPRequestHandler):
    """Another program on llama-swap's port (Tomcat in IntelliJ on 8080): it answers - 404 to all."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()


class OwnSwap(Tomcat):
    """The person's own llama-swap, run in a way AFO does not know (in Docker, say): its /running answers."""

    def do_GET(self):
        if self.path != "/running":
            return super().do_GET()
        data = json.dumps({"running": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_the_engine_is_judged_by_its_files_not_by_who_answers_on_its_port(hub, monkeypatch):  # noqa: F811
    """39.1 (2026-11-12, the plan's finding 1): on a laptop with Tomcat on 8080 and no llama-swap at all, the wizard
    said the engine was there - Tomcat answers 404, below 500, and that was taken for llama-swap; no «Установить
    движок». Now the files on the disk say it: llama-swap's program, its config, llama-server. A llama-swap that
    answers as llama-swap counts as there too - the person's own; any other program on its port does not."""
    for handler, needed in ((Tomcat, True), (OwnSwap, False)):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(hub.app.state.setup, "_url", f"http://127.0.0.1:{server.server_port}")
            state = hub.get("/v1/setup").json()
        finally:
            server.shutdown()
        assert (state["needed"], state["answering"]) == (needed, not needed), handler.__name__
        assert {m["what"] for m in state["resources"]["missing"]} == {"llama-swap", "config"}  # nothing on the disk
        assert not state["resources"]["ready"]


def a_bare_computer(monkeypatch, tmp_path) -> list:
    """No llama-swap here: the isolation's paths taken away, and the author's own llama-swap beside the checkout
    (D:/AI model/llama-swap) out of sight; llama-swap's start only noted - the real one never starts in a test."""
    monkeypatch.delenv(lc.CONFIG_ENV)
    monkeypatch.delenv(autostart.LLAMA_SWAP_START_ENV)
    monkeypatch.setattr(autostart, "DEFAULT_LLAMA_SWAP_START", tmp_path / "none" / "start-llama-swap.cmd")
    monkeypatch.setattr(lc, "DEFAULT_CONFIG", tmp_path / "none" / "config.yaml")
    monkeypatch.setattr(hardware, "adapters", lambda: [{"name": "Radeon RX 9070", "vendor": "amd", "vram_gib": 16.0,
                                                        "driver": "32.0"}])  # fmt: skip
    monkeypatch.setattr(hardware, "nvidia_driver", lambda: None)
    started = []

    async def fake_start(url, script, wanted, log_file, **kwargs):
        started.append(script)
        return "started"

    monkeypatch.setattr(autostart, "ensure_llama_swap", fake_start)
    return started


@contextlib.contextmanager
def fetching_hub():
    settings = HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                           process_interval_s=0, fetch_engines=True)  # fmt: skip
    with TestClient(create_app(settings), base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def until_ended(setup_, *, timeout=20.0):
    deadline = time.monotonic() + timeout
    while setup_.job["status"] in ("idle", "downloading", "starting"):
        assert time.monotonic() < deadline, setup_.job
        time.sleep(0.05)
    return setup_.job


def test_a_computer_with_nothing_gets_the_engine_by_itself(github, monkeypatch, tmp_path):
    """39.1 (2026-11-12, the user's word: «Программы AFO скачивает сам при входе: сканер видит, чего нет, и
    докачивает»): a hub started on a computer with no llama-swap fetches the engine itself - the build its graphics
    card wants, into AFO's engines folder - with no wizard; the window asks again when it comes. A llama-swap the
    person chose outside AFO's folder is never touched: what it lacks is said, nothing is fetched."""
    started = a_bare_computer(monkeypatch, tmp_path)
    with fetching_hub() as client:
        setup_ = client.app.state.setup
        until_ended(setup_)
        assert (setup_.job["status"], setup_.job["build"], setup_.job["auto"]) == ("done", "vulkan", True)
        swap = tmp_path / "localchat-home" / "engines" / "llama-swap"
        assert (swap / "llama-swap.exe").exists() and (swap / "config.yaml").exists() and started == [
            swap / "llama-swap.exe"]  # fmt: skip
        assert client.post("/v1/setup/check").json()["resources"]["status"] == "ready"

        mine = tmp_path / "mine" / "llama-swap.exe"
        client.app.state.db.call(lambda conn: autostart.set_choice(conn, autostart.START_SETTING, str(mine)))
        checked = client.post("/v1/setup/check").json()
        assert checked["resources"]["status"] == "own" and str(mine) in checked["resources"]["words"]
        assert checked["job"]["status"] == "done"  # nothing fetched again


def test_the_engines_fetch_meets_github_s_limit_an_antivirus_and_a_full_disk(github, monkeypatch, tmp_path):
    """39.1 (the plan's risks): GitHub answers 60 questions an hour - a fetch that failed (its limit, no network) is
    tried again by itself later, not at each look; a program gone after the install (an antivirus took it) is not
    fetched again and again - the words say so; a disk with too little room is said before the download."""
    from hub import model_install as mi
    from hub import resources

    a_bare_computer(monkeypatch, tmp_path)
    monkeypatch.setattr(resources, "RETRY_S", 5.0)
    FakeGitHub.limited = True
    with fetching_hub() as client:
        setup_ = client.app.state.setup
        failed = until_ended(setup_)
        assert (failed["status"], failed["auto"]) == ("failed", True) and "слишком много запросов" in failed["error"]
        asked = len(FakeGitHub.asked)
        checked = client.post("/v1/setup/check").json()["resources"]  # the window came: not asked again yet
        assert checked["status"] == "failed" and "Попробую снова" in checked["words"]
        assert len(FakeGitHub.asked) == asked

        FakeGitHub.limited = False  # the hour is over: the scanner tries again by itself
        deadline = time.monotonic() + 25
        while setup_.job["status"] != "done":
            assert time.monotonic() < deadline, setup_.job
            time.sleep(0.05)
        assert client.post("/v1/setup/check").json()["resources"]["status"] == "ready"

        exe = tmp_path / "localchat-home" / "engines" / "llama-swap" / "llama-swap.exe"
        exe.unlink()  # an antivirus took it
        asked = len(FakeGitHub.asked)
        gone = client.post("/v1/setup/check").json()
        assert gone["resources"]["status"] == "vanished" and "антивирус" in gone["resources"]["words"]
        assert str(exe) in gone["resources"]["words"] and gone["job"]["status"] == "done"
        assert len(FakeGitHub.asked) == asked  # not fetched again

        monkeypatch.setattr(mi, "DISK_MARGIN_GIB", 10.0**9)  # «Установить движок» on a full disk
        client.post("/v1/setup/engine", json={})
        full = until_ended(setup_)
        assert full["status"] == "failed" and "не хватает места" in full["error"]
        assert not exe.exists()


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
