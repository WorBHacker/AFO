"""AFO's own Strata (stage 45.2а, hub/strata.py): the pinned release from a stand-in GitHub, each file's sha256
checked, the control program unpacked, the engine Strata names for this computer fetched and put in by Strata - a
stand-in Strata, a real process with a Python of its own, as the real one is. Every folder it is given is AFO's; the
person's own Strata on PATH keeps its strata.ps1 (45.0: a newer Strata renames it - the stand-in does the same)."""

import asyncio
import hashlib
import io
import json
import os
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from common import hardware
from hub import engines, resources, strata
from hub import llama_config as lc
from tests.test_llama_config import CONFIG, FakeLlamaSwap
from tests.test_model_install import fake_hub, hub, wait_for  # noqa: F401 - the installer's hub fixture

ENGINE = "strata-engine-0.1.64-windows-x86_64-cuda-cuda13.tar.gz"
ENGINE_BYTES = b"engine " * 1000

# the stand-in strata.py: what AFO asks of the real one (update --check / update), the heal of an older Strata's
# strata.ps1 on PATH (strata_cli/launcher_heal.py), and a record of the environment it was run in
STAND_IN = r"""
import hashlib, json, os, sys
from pathlib import Path

args = sys.argv[1:]
for folder in os.environ.get("PATH", "").split(os.pathsep):
    ps1 = Path(folder) / "strata.ps1"
    if folder and ps1.is_file() and (Path(folder) / "strata.cmd").is_file():
        ps1.replace(ps1.with_name("strata-direct.ps1"))
record = Path(os.environ["STRATA_DATA_DIR"]) / "seen.json"
runs = json.loads(record.read_text()) if record.exists() else []
names = ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "STRATA_DATA_DIR", "STRATA_COMPONENTS_DIR", "STRATA_SETTINGS_FILE",
         "PYTHONPATH", "VIRTUAL_ENV", "PATH")
runs.append({"args": args, "env": {k: os.environ.get(k, "") for k in names}})
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(runs))
feed = Path(args[args.index("--feed") + 1])
offered = json.loads((feed / "strata-feed.json").read_text())["engine"]
components = Path(os.environ["STRATA_COMPONENTS_DIR"]) / "engine"
current = components.is_dir() and any(components.iterdir())
print("  Reading the signed update feed")
if "--check" in args:
    print(json.dumps({"ok": True, "engine": {"status": "current" if current else "install", "offered": offered,
                                             "problem": None}}))
else:
    archive = feed / offered["url"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != offered["sha256"]:
        print(json.dumps({"ok": False, "error": "the package does not match the feed"}))
        sys.exit(3)
    folder = components / f"{offered['version']}-{offered['variant']}"
    folder.mkdir(parents=True)
    (folder / "strata.exe").write_bytes(archive.read_bytes()[:16])
    print(json.dumps({"ok": True, "results": [{"component": "engine", "status": "updated",
                                                "installed_after": {"version": offered["version"]}}]}))
"""


# (45.2в) the stand-in's `strata launch`, shaped as Strata 0.1.64's own modules where hub/strata_prepare.py takes hold
# of them: cli.main -> launch.cmd.launch, its words through cmd._printer, the steps in launch.runner.Runner.run. The
# model's file comes in pieces into <data-dir>/models (STAND_IN_PACE seconds between them) and goes on where it
# stopped; started, it would leave `started` - the model loaded
STAND_IN_CLI = r"""
import sys
from strata_cli.launch import cmd

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "launch":
        return cmd.launch(argv[1:])
    print("no such command", argv)
    return 2
"""
STAND_IN_CMD = r"""
import argparse
from strata_cli.launch import runner

def _printer(quiet=False):
    def emit(event):
        print(f"[{event.stage}] {event.text}", flush=True)
    return emit

def launch(argv):
    parser = argparse.ArgumentParser(prog="strata launch")
    for flag in ("--model", "--family", "--data-dir", "--context", "--vision", "--access", "--port", "--tune", "--kv",
                 "--parallel", "--drafting"):
        parser.add_argument(flag)
    parser.add_argument("--no-tui", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        runner.Runner(args, _printer(False)).run()
    except SystemExit as exc:
        return int(exc.code or 1)
    return 0
"""
STAND_IN_RUNNER = r"""
import json, os, time
from dataclasses import dataclass
from pathlib import Path

SIZE = 1_000_000

@dataclass
class Event:
    stage: str
    text: str
    pct: float | None = None
    level: str = "info"

class Runner:
    def __init__(self, args, emit_cb):
        self.a, self.emit_cb = args, emit_cb
        self.name = f"{args.model.lower()}-{int(args.context) // 1024}k"
        self.profile, self.config_path = "launch-" + self.name, None

    def emit(self, stage, text, pct=None, level="info"):
        self.emit_cb(Event(stage, text, pct, level))

    def run(self, *, start=True):
        data = Path(self.a.data_dir)
        names = ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "STRATA_DATA_DIR", "PYTHONPATH", "PATH")
        seen = Path(os.environ["STRATA_DATA_DIR"]) / "prepared-in.json"
        seen.write_text(json.dumps({"args": vars(self.a), "env": {k: os.environ.get(k, "") for k in names}}))
        time.sleep(float(os.environ.get("STAND_IN_WAIT") or 0))
        self.emit("engine", "Engine 0.1.64 (cuda) is installed.", 100)
        if os.environ.get("STAND_IN_FAIL"):
            self.emit("model", "Hugging Face did not answer.", None, level="error")
            raise SystemExit(2)
        model = data / "models" / f"{self.a.family}{self.a.model}" / "model.gguf"
        model.parent.mkdir(parents=True, exist_ok=True)
        have = model.stat().st_size // 100_000 * 100_000 if model.exists() else 0  # whole pieces only
        with open(model, "ab") as out:
            out.truncate(have)
            while have < SIZE:
                out.write(b"x" * 100_000)
                out.flush()
                have += 100_000
                self.emit("model", f"Downloading {have / 1e9:.4f} of {SIZE / 1e9:.4f} GB", 100.0 * have / SIZE)
                time.sleep(float(os.environ.get("STAND_IN_PACE") or 0))
        (data / "mtp" / "rt").mkdir(parents=True, exist_ok=True)
        (data / "mtp" / "rt" / "experts.bin").write_bytes(b"mtp")
        self.emit("model", "Preparing the model files for this graphics card…", 0)
        launch = Path(os.environ["LOCALAPPDATA"]) / "Strata" / "launch"
        launch.mkdir(parents=True, exist_ok=True)
        self.config_path = launch / f"{self.name}.json"
        chosen = {k: getattr(self.a, k) for k in ("kv", "parallel", "drafting") if getattr(self.a, k)}
        self.config_path.write_text(json.dumps({"model": str(model), "context": int(self.a.context), **chosen}))
        (launch / f"{self.name}.engine.log").write_text("engine")
        profiles = Path(os.environ["STRATA_PROFILE_DIR"])
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / f"{self.profile}.json").write_text("{}")
        self.emit("config", "Settings are saved.", 100)
        if start:
            (data / "started").write_text("the model was loaded")
        return 0
"""


def control_zip() -> bytes:
    """The control package as the release has it - rooted at the bundle - with a Python of its own: a copy of this
    test's venv launcher, its pyvenv.cfg beside the folder (the real one carries Python 3.12)."""
    venv = Path(sys.prefix)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("strata.py", STAND_IN)
        zf.writestr("strata_cli/__init__.py", "")
        zf.writestr("strata_cli/cli.py", STAND_IN_CLI)
        zf.writestr("strata_cli/launch/__init__.py", "")
        zf.writestr("strata_cli/launch/cmd.py", STAND_IN_CMD)
        zf.writestr("strata_cli/launch/runner.py", STAND_IN_RUNNER)
        zf.writestr("bin/strata.cmd", "@echo off\r\n")
        zf.writestr("BUNDLE.json", json.dumps({"component": "strata-control", "version": strata.CONTROL_VERSION}))
        zf.write(Path(sys.executable), "python/python.exe")
        zf.write(venv / "pyvenv.cfg", "pyvenv.cfg")
    return out.getvalue()


class FakeGitHub(BaseHTTPRequestHandler):
    port = 0
    files: dict[str, bytes] = {}  # noqa: RUF012 - the release's files, set by the fixture
    digests: dict[str, str] = {}  # noqa: RUF012 - a name -> the sha256 GitHub gives ("" - none)
    asked: list[str] = []  # noqa: RUF012 - every path asked for

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.asked.append(self.path)
        url = f"http://127.0.0.1:{self.port}/download/"
        if self.path == f"/repos/{strata.REPO}/releases/tags/{strata.TAG}":
            assets = []
            for name, data in self.files.items():
                asset = {"name": name, "size": len(data), "browser_download_url": url + name}
                sha = self.digests.get(name, hashlib.sha256(data).hexdigest())
                if sha:
                    asset["digest"] = "sha256:" + sha
                assets.append(asset)
            self._send({"tag_name": strata.TAG, "assets": assets})
        elif self.path.startswith("/download/") and self.path[10:] in self.files:
            self._send(self.files[self.path[10:]], "application/octet-stream")
        else:
            self.send_response(404)
            self.end_headers()


def release_files(feed_sha: str | None = None) -> dict[str, bytes]:
    feed = {"engine": {"url": ENGINE, "sha256": feed_sha or hashlib.sha256(ENGINE_BYTES).hexdigest(),
                       "version": "0.1.64", "variant": "cuda13"}}  # fmt: skip
    return {strata.CONTROL: control_zip(), "strata-feed.json": json.dumps(feed).encode(),
            "strata-feed.json.sig": b"signed", ENGINE: ENGINE_BYTES,
            "strata-engine-0.1.64-windows-x86_64-cuda-cuda12.tar.gz": b"another card's"}  # fmt: skip


@pytest.fixture
def github(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    FakeGitHub.port = server.server_port
    FakeGitHub.files, FakeGitHub.digests, FakeGitHub.asked = release_files(), {}, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(engines.GITHUB_ENV, f"http://127.0.0.1:{server.server_port}")
    yield FakeGitHub
    server.shutdown()


@pytest.fixture
def persons_strata(tmp_path, monkeypatch):
    """The person's own Strata on PATH, as the user's computer has it (0.1.51: strata.cmd and strata.ps1)."""
    folder = tmp_path / "persons" / "Strata" / "terminal" / "current" / "bin"
    folder.mkdir(parents=True)
    (folder / "strata.cmd").write_text("@echo off\r\n")
    (folder / "strata.ps1").write_text("# Strata Terminal launcher for PowerShell\n")
    monkeypatch.setenv("PATH", os.pathsep.join([str(folder), os.environ["PATH"]]))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hubs-own"))  # the hub's: not Strata's
    return folder


def assets() -> dict:
    async def run():
        async with httpx.AsyncClient() as http:
            return await strata.release(http)

    return asyncio.run(run())


def test_strata_is_put_in_with_folders_of_its_own(github, persons_strata, tmp_path):
    base = tmp_path / "engines" / "strata"
    reported = []
    ready = strata.install(assets(), base, report=lambda n, total: reported.append((n, total)),
                           cancel=threading.Event())  # fmt: skip
    assert ready == {"tag": strata.TAG, "control": strata.CONTROL_VERSION, "engine": "0.1.64-cuda13"}
    assert strata.installed(base) == ready
    assert (strata.data(base) / "components" / "engine" / "0.1.64-cuda13" / "strata.exe").is_file()
    assert not (base / "downloads").exists()  # the archives go once it is in
    # the engine the feed names for this computer was fetched - not the other card's
    assert reported[-1][0] == reported[-1][1] == sum(len(github.files[n]) for n in (strata.CONTROL, *strata.FEED,
                                                                                     ENGINE))  # fmt: skip
    runs = json.loads((strata.data(base) / "seen.json").read_text())
    assert [r["args"][:2] for r in runs] == [["update", "--check"], ["update", "--no-control"]]
    for env in (r["env"] for r in runs):
        assert all(Path(env[k]).is_relative_to(base) for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE",
                                                                "STRATA_DATA_DIR", "STRATA_COMPONENTS_DIR",
                                                                "STRATA_SETTINGS_FILE"))  # fmt: skip
        assert str(persons_strata) not in env["PATH"] and not env["PYTHONPATH"] and not env["VIRTUAL_ENV"]
    assert (persons_strata / "strata.ps1").is_file() and not (persons_strata / "strata-direct.ps1").exists()
    # put in again: the engine is current - nothing of it fetched again
    again = strata.install(assets(), base, report=lambda n, total: None, cancel=threading.Event())
    assert again == ready and len(json.loads((strata.data(base) / "seen.json").read_text())) == 3


def test_an_engine_not_of_the_release_or_unchecked_is_not_put_in(github, tmp_path):
    github.files = release_files(feed_sha="0" * 64)  # the feed names another file than the release's
    base = tmp_path / "strata"
    with pytest.raises(strata.StrataError, match="не совпадает"):
        strata.install(assets(), base, report=lambda n, total: None, cancel=threading.Event())
    assert strata.installed(base) is None
    github.files, github.digests = release_files(), {strata.CONTROL: ""}
    with pytest.raises(strata.StrataError, match="контрольную сумму"):
        assets()


def a_computer(monkeypatch, tmp_path, *, vendor="nvidia", vram=16.0, ram=63.4, cuda=True):
    """The card and the memory the scanner reads (the user's computer by default: an RTX 5080 of 16 GB, 63.4 GB of
    RAM, CUDA 13's driver) - and no Strata of the person's in their %LOCALAPPDATA%."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "persons-local"))
    name = "NVIDIA GeForce RTX 5080" if vendor == "nvidia" else "Radeon RX 9070"
    monkeypatch.setattr(hardware, "adapters", lambda: [{"name": name, "vendor": vendor, "vram_gib": vram,
                                                        "driver": "32.0"}])  # fmt: skip
    driver = {"version": "621.14", "cuda": 13050, "compute": [12, 0]} if cuda and vendor == "nvidia" else None
    monkeypatch.setattr(hardware, "nvidia_driver", lambda: driver)
    monkeypatch.setattr(strata, "memory_sample", lambda: {"ram_total_gib": ram})


def scanner(enabled=True) -> resources.Resources:
    from types import SimpleNamespace

    return resources.Resources(None, SimpleNamespace(), url="http://127.0.0.1:9", enabled=enabled)


def test_the_scanner_puts_strata_in_where_its_smallest_model_fits(github, monkeypatch, tmp_path):
    """45.2б (the user's word 2026-11-12: «AFO ставит её сам там, где её модели помещаются»): an NVIDIA card of 16 GB
    and 63.4 GB of RAM - the Coder's 32 GB fit - Strata is fetched by itself, once; its models stay a click."""
    a_computer(monkeypatch, tmp_path)

    async def run():
        found = scanner()
        first = await found.strata_check()
        again = await found.strata_check()  # the window looks again meanwhile: the one fetch going on
        await found._strata
        return first, again, await found.strata_check()

    first, again, then = asyncio.run(run())
    assert first["status"] == again["status"] == "fetching" and then == {"status": "ready", "words": ""}
    assert strata.installed(strata.home()) is not None
    assert github.asked.count(f"/repos/{strata.REPO}/releases/tags/{strata.TAG}") == 1


@pytest.mark.parametrize("computer", [{"vendor": "amd"}, {"cuda": False}, {"vram": 6.0}, {"ram": 32.0}])
def test_where_it_does_not_fit_or_is_the_persons_strata_is_not_fetched(github, monkeypatch, tmp_path, computer):
    """An AMD card, an NVIDIA card without CUDA's driver, 6 GB on the card, 32 GB of RAM (29 of them for the
    experts): nothing fetched, nothing said. The person's own Strata - and the switch off - neither."""
    a_computer(monkeypatch, tmp_path, **computer)
    assert asyncio.run(scanner().strata_check())["status"] == "unfit"
    a_computer(monkeypatch, tmp_path)
    terminal = tmp_path / "persons-local" / "Strata" / "terminal" / "0.1.51"
    terminal.mkdir(parents=True)
    (terminal / "strata.py").write_text("")
    assert asyncio.run(scanner().strata_check())["status"] == "own"
    (terminal / "strata.py").unlink()
    assert asyncio.run(scanner(enabled=False).strata_check())["status"] == "off"
    assert github.asked == [] and not strata.home().exists()


CODER = "flash-next-coder-iq1_m"


@pytest.fixture
def small_models(monkeypatch):
    """Strata's models as the stand-in downloads them - 1 MB, nothing beside: the test's disk has no 66 GB."""
    for key, entry in list(strata.MODELS.items()):
        monkeypatch.setitem(strata.MODELS, key, {**entry, "download_gb": 0.001})
    monkeypatch.setattr(strata, "EXTRA_GB", 0.0)


def put_in() -> None:
    """AFO's own Strata, put in from the stand-in GitHub as the scanner does it."""
    strata.install(assets(), strata.home(), report=lambda n, total: None, cancel=threading.Event())


def job_of(client, job_id: str) -> dict:
    return next(j for j in client.get("/v1/install/jobs").json()["jobs"] if j["id"] == job_id)


def downloading(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = job_of(client, job_id)
        if found["status"] == "downloading" and found["percent"] > 0:
            return found
        time.sleep(0.05)
    raise AssertionError(f"{job_id}: {found['status']} ({found['error']})")


def test_a_strata_model_installed_tried_and_removed(github, hub, persons_strata, small_models, monkeypatch,  # noqa: F811
                                                    tmp_path):  # fmt: skip
    """45.2в: a model of AFO's Strata. Strata downloads it into the install's folder and prepares it without starting
    it (its own Runner.run(start=False): the card free meanwhile, nothing tuned), in AFO's folders and with no Strata
    of the person's on PATH; its block is `serve --profile` of that run config with the same folders in its `env`;
    one trial question. Removed: its folder, run config and profile go, Strata stays. Not for an AMD card."""
    a_computer(monkeypatch, tmp_path)
    assert not hub.get("/v1/install/strata").json()["ready"]
    refused = hub.post("/v1/install/strata", json={"id": CODER})
    assert refused.status_code == 409 and "Strata ещё не установлена" in refused.json()["detail"]
    put_in()
    listed = hub.get("/v1/install/strata").json()
    assert listed["ready"] and [(m["id"], m["verdict"]) for m in listed["models"]] == [
        (CODER, "fits"), ("flash-next-q2_0", "fits"), ("flash-next-iq2_xs", "fits"), ("flash-next-iq3_xxs", "fits"),
        ("flash-next-iq3_s", "slower")]  # 63.4 GB less 3: IQ3_S's experts want 62  # fmt: skip

    job = hub.post("/v1/install/strata", json={"id": CODER, "drive": "T"}).json()["job"]
    assert job["status"] == "queued" and job["plan"]["engine"] == "strata" and job["total_bytes"] == 1_000_000
    done = wait_for(hub, job["id"], "done", "failed", timeout=60.0)
    assert done["status"] == "done", done["error"]
    assert done["percent"] == 100.0 and done["trial"]["seconds"] is not None
    folder = hub.tmp / "T" / "AFO" / "models" / "strata" / CODER
    assert (folder / "models" / "coderIQ1_M" / "model.gguf").stat().st_size == 1_000_000
    assert (folder / "mtp" / "rt" / "experts.bin").is_file() and not (folder / "started").exists()
    base = strata.home()
    prepared = json.loads((strata.data(base) / "prepared-in.json").read_text())
    assert (prepared["args"]["data_dir"], prepared["args"]["tune"], prepared["args"]["context"]) == (
        str(folder), "skip", "65536")  # fmt: skip
    env = prepared["env"]
    assert all(Path(env[k]).is_relative_to(base) for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE",
                                                            "STRATA_DATA_DIR"))  # fmt: skip
    assert str(persons_strata) not in env["PATH"] and not env["PYTHONPATH"]
    assert (persons_strata / "strata.ps1").is_file()
    config = Path(done["plan"]["config"])
    assert config == base / "home" / "Local" / "Strata" / "launch" / "iq1_m-64k.json" and config.is_file()

    text = hub.config.read_text(encoding="utf-8")
    model_id = done["model_id"]
    view = lc.model_view(text, model_id)
    assert (view["engine"], view["editable"], view["ttl"], view["profile"]) == ("strata", False, 900,
                                                                               config.as_posix())  # fmt: skip
    start, end = lc.model_blocks(text)[model_id]
    block = "\n".join(text.splitlines()[start:end])
    python, script = (Path(p).as_posix() for p in strata.command(base))
    assert f'"{python}" "{script}" serve' in block and "--host 127.0.0.1 --port ${PORT}" in block
    assert f"      - 'LOCALAPPDATA={base / 'home' / 'Local'}'" in block and str(persons_strata) not in block
    assert [line[9:-1] for line in block.splitlines() if line.startswith("      - '")] == strata.serve_env(base)
    assert "checkEndpoint: /health" in block and f'localchat_install: "{job["id"]}"' in block
    assert FakeLlamaSwap.asked[-1]["model"] == model_id
    assert {m["id"]: m for m in hub.get("/v1/models").json()["models"]}[f"llama-swap/{model_id}"]["enabled"]
    again = hub.post("/v1/install/strata", json={"id": CODER})
    assert again.status_code == 409 and "уже установлена" in again.json()["detail"]

    gone = hub.delete(f"/v1/install/models/{model_id}").json()["job"]
    assert gone["status"] == "removed" and not folder.exists() and not folder.parent.exists()
    assert not config.exists() and not (strata.data(base) / "profiles" / "launch-iq1_m-64k.json").exists()
    assert hub.config.read_text(encoding="utf-8") == CONFIG and strata.installed(base)
    a_computer(monkeypatch, tmp_path, vendor="amd")
    refused = hub.post("/v1/install/strata", json={"id": CODER})
    assert refused.status_code == 409 and "NVIDIA" in refused.json()["detail"]


def test_a_strata_install_paused_resumed_cancelled_and_failing_in_words(github, hub, small_models,  # noqa: F811
                                                                       monkeypatch, tmp_path):  # fmt: skip
    """45.2в: «Приостановить» stops Strata with every process it started - its file grows no more - and «Продолжить»
    goes on where its download stopped; «Отменить» takes the folder away; Strata's own last words say why it failed,
    and nothing is written into the config."""
    a_computer(monkeypatch, tmp_path)
    put_in()
    monkeypatch.setenv("STAND_IN_PACE", "0.3")
    job = hub.post("/v1/install/strata", json={"id": CODER}).json()["job"]
    downloading(hub, job["id"])
    assert hub.post(f"/v1/install/jobs/{job['id']}/pause").status_code == 200
    wait_for(hub, job["id"], "paused")
    folder = hub.tmp / "localchat-model" / "strata" / CODER
    model = folder / "models" / "coderIQ1_M" / "model.gguf"
    size = model.stat().st_size
    time.sleep(1.0)
    assert model.stat().st_size == size < 1_000_000  # stopped, not left writing
    monkeypatch.setenv("STAND_IN_PACE", "0")
    hub.post(f"/v1/install/jobs/{job['id']}/resume")
    done = wait_for(hub, job["id"], "done", "failed", timeout=60.0)
    assert done["status"] == "done", done["error"]
    assert model.stat().st_size == 1_000_000
    hub.delete(f"/v1/install/models/{done['model_id']}")

    monkeypatch.setenv("STAND_IN_PACE", "0.3")
    job = hub.post("/v1/install/strata", json={"id": CODER}).json()["job"]
    downloading(hub, job["id"])
    hub.post(f"/v1/install/jobs/{job['id']}/cancel")
    wait_for(hub, job["id"], "cancelled")
    assert not folder.exists()

    monkeypatch.setenv("STAND_IN_FAIL", "1")
    job = hub.post("/v1/install/strata", json={"id": CODER}).json()["job"]
    failed = wait_for(hub, job["id"], "failed", "done", timeout=60.0)
    assert failed["status"] == "failed" and "Strata не подготовила" in failed["error"]
    assert "Hugging Face did not answer." in failed["error"] and hub.config.read_text(encoding="utf-8") == CONFIG


def test_a_strata_model_bigger_than_the_disk_is_refused_before_anything_starts(github, hub, monkeypatch,  # noqa: F811
                                                                                tmp_path):  # fmt: skip
    a_computer(monkeypatch, tmp_path)
    put_in()
    monkeypatch.setitem(strata.MODELS, CODER, {**strata.MODELS[CODER], "download_gb": 1e9})
    refused = hub.post("/v1/install/strata", json={"id": CODER})
    assert refused.status_code == 507 and "не хватает места" in refused.json()["detail"]
    assert hub.get("/v1/install/jobs").json()["jobs"] == []


def test_a_strata_models_settings_are_prepared_again_and_the_earlier_come_back_if_they_do_not_start(
        github, hub, small_models, monkeypatch, tmp_path):  # noqa: F811  # fmt: skip
    """45.1в: an installed model of AFO's Strata with other KV, requests at once, context or drafter - Strata prepares
    its run config again with them (not the command's flags) and one trial question follows; another context is a
    run config of another name, the earlier one goes. Ones that do not start: the run config as it was, the block
    with it, said in words. Cancelling meanwhile would throw the model away - refused; a value Strata has not - 422."""
    a_computer(monkeypatch, tmp_path)
    put_in()
    job = hub.post("/v1/install/strata", json={"id": CODER}).json()["job"]
    model_id = wait_for(hub, job["id"], "done", "failed", timeout=60.0)["model_id"]
    base = strata.home()
    launch = base / "home" / "Local" / "Strata" / "launch"

    def shown() -> dict:
        return lc.model_view(hub.config.read_text(encoding="utf-8"), model_id)

    assert shown()["strata"] == {"context": 65536, "kv": "int8", "parallel": 0, "drafting": "auto"}
    monkeypatch.setenv("STAND_IN_WAIT", "1.5")
    changing = hub.put(f"/v1/install/strata/{model_id}", json={"changes": {"kv": "q4_0", "parallel": 2}})
    assert changing.status_code == 200 and changing.json()["job"]["status"] == "queued"
    refused = hub.post(f"/v1/install/jobs/{job['id']}/cancel")
    assert refused.status_code == 409 and "не отменить" in refused.json()["detail"]
    monkeypatch.setenv("STAND_IN_WAIT", "0")
    done = wait_for(hub, job["id"], "done", "failed", timeout=60.0)
    assert done["status"] == "done" and not done["error"] and "was" not in done["plan"]
    args = json.loads((strata.data(base) / "prepared-in.json").read_text())["args"]
    assert (args["kv"], args["parallel"], args["drafting"]) == ("q4_0", "2", None)
    assert json.loads((launch / "iq1_m-64k.json").read_text())["kv"] == "q4_0"
    assert shown()["strata"] == {"context": 65536, "kv": "q4_0", "parallel": 2, "drafting": "auto"}
    assert (hub.tmp / "localchat-model" / "strata" / CODER / "models").is_dir()  # its files stayed

    done = hub.put(f"/v1/install/strata/{model_id}", json={"changes": {"context": 131072}}).json()["job"]
    done = wait_for(hub, job["id"], "done", "failed", timeout=60.0)
    assert done["status"] == "done" and Path(done["plan"]["config"]) == launch / "iq1_m-128k.json"
    assert not (launch / "iq1_m-64k.json").exists() and shown()["profile"] == (launch / "iq1_m-128k.json").as_posix()
    assert json.loads((launch / "iq1_m-128k.json").read_text())["kv"] == "q4_0"  # the earlier choices kept

    FakeLlamaSwap.fail = True  # the next settings do not start
    hub.put(f"/v1/install/strata/{model_id}", json={"changes": {"drafting": "off", "context": 262144}})
    time.sleep(0.2)
    back = wait_for(hub, job["id"], "done", "failed", timeout=60.0)
    assert back["status"] == "done" and "новые настройки Strata не запустились" in back["error"]
    assert "drafting" not in back["plan"] and back["plan"]["context"] == 131072
    config = json.loads((launch / "iq1_m-128k.json").read_text())
    assert {k: config.get(k) for k in ("context", "kv", "parallel", "drafting")} == {
        "context": 131072, "kv": "q4_0", "parallel": "2", "drafting": None}  # as it was  # fmt: skip
    assert not (launch / "iq1_m-256k.json").exists()
    assert shown()["strata"] == {"context": 131072, "kv": "q4_0", "parallel": 2, "drafting": "auto"}
    FakeLlamaSwap.fail = False

    assert hub.put(f"/v1/install/strata/{model_id}", json={"changes": {"kv": "q9"}}).status_code == 422
    assert hub.put("/v1/install/strata/big", json={"changes": {"kv": "q4_0"}}).status_code == 404
