"""Starting by itself (Q8): the hub at Windows login (a Run-key value, here in memory - the registry is never touched)
and llama-swap started by the hub when it does not answer (here a fake starter and a mock llama-swap)."""

import asyncio
import socket
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import httpx
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import autostart
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings
from tests.test_setup import OwnSwap
from tests.test_tools import BASE, hub  # noqa: F401 - the hub fixture

PORT_TAKEN = autostart.port_taken


@pytest.fixture(autouse=True)
def no_holder(monkeypatch):
    """Who holds this computer's 8080 really (the user's llama-swap, say) is not these tests' business."""
    monkeypatch.setattr(autostart, "port_taken", lambda url: None)


def test_the_login_value():
    """43.4 (the user's word, 2026-11-13): at login AFO itself starts, in the tray - its window starts the hub."""
    store = autostart.MemoryRunStore()
    assert not autostart.login_enabled(store)
    autostart.set_login(store, True)
    assert store.value == autostart.login_command() and store.value.endswith('" -m app --tray')
    assert "pythonw.exe" in store.value.lower() or "python" in store.value.lower()
    autostart.set_login(store, False)
    assert store.value is None and isinstance(autostart.default_run_store(), autostart.MemoryRunStore)


def test_the_login_value_under_an_old_name_moves():
    """10.4b, 43.4: the hub at login under an old name («LocalChat hub», «AFO hub» - the hub alone) becomes AFO's
    «AFO» - AFO in the tray now; none - nothing; one set already stays."""
    assert (autostart.RUN_VALUE, autostart.OLD_RUN_VALUES) == ("AFO", ("AFO hub", "LocalChat hub"))
    new, alone, older = autostart.MemoryRunStore(), autostart.MemoryRunStore(), autostart.MemoryRunStore()
    autostart.renamed(new, [alone, older])
    assert new.value is None and alone.value is None and older.value is None
    older.value = '"C:\\Python\\pythonw.exe" -m hub'
    autostart.renamed(new, [alone, older])
    assert new.value == autostart.login_command() and older.value is None
    alone.value, new.value = '"C:\\Python\\pythonw.exe" -m hub', '"D:\\AFO\\pythonw.exe" -m app --tray'
    autostart.renamed(new, [alone, older])
    assert new.value == '"D:\\AFO\\pythonw.exe" -m app --tray' and alone.value is None


class FakeLlamaSwap:
    """Answers once "started"; counts the starts."""

    def __init__(self, up=False):
        self.up = up
        self.starts = []

    def transport(self):
        def answer(request):
            if not self.up:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json={"data": []})

        return httpx.MockTransport(answer)

    def starter(self, command, cwd, log_file):
        self.starts.append((command, log_file))
        self.up = True


def ensure(fake, url="http://127.0.0.1:8080", wanted=True, script=None, tmp_path=None):
    script = script or (tmp_path / "start-llama-swap.cmd")
    return asyncio.run(autostart.ensure_llama_swap(url, script, wanted, tmp_path / "logs" / "out.log",
                                                   starter=fake.starter, transport=fake.transport(),
                                                   ready_timeout=3))  # fmt: skip


def test_llama_swap_is_started_only_when_it_is_down_and_wanted(tmp_path):
    script = tmp_path / "start-llama-swap.cmd"
    assert ensure(FakeLlamaSwap(up=True), tmp_path=tmp_path) == "running"
    down = FakeLlamaSwap()
    assert ensure(down, tmp_path=tmp_path) == "missing" and down.starts == []  # no script: nothing to run
    script.write_text("@echo off\n", encoding="utf-8")
    assert ensure(down, wanted=False, tmp_path=tmp_path) == "off" and down.starts == []
    assert ensure(down, tmp_path=tmp_path) == "started"
    assert down.starts == [(["cmd", "/c", str(script)], tmp_path / "logs" / "out.log")]
    remote = FakeLlamaSwap()
    assert ensure(remote, url="http://192.168.0.5:8080", tmp_path=tmp_path) == "remote" and remote.starts == []

    class Stuck(FakeLlamaSwap):
        def starter(self, command, cwd, log_file):
            self.starts.append(command)  # started, but never answers within the wait

    assert ensure(Stuck(), tmp_path=tmp_path) == "slow"


def test_llama_swaps_port_held_by_another_program(tmp_path, monkeypatch):
    """23.4: a model of Strata on 8080 - llama-swap cannot start there, and the hub says who holds the port."""
    strata = {"pid": 4242, "name": "strata.exe", "exe": "C:\\Strata\\strata.exe"}
    holders = {8080: strata, 8081: {"pid": 7, "name": "llama-swap.exe", "exe": ""}, 8082: {"pid": 8, "name": "",
                                                                                         "exe": ""}}  # fmt: skip
    monkeypatch.setattr(autostart.processes, "listener", holders.get)
    assert PORT_TAKEN("http://127.0.0.1:8080") == {"port": 8080, **strata}
    assert PORT_TAKEN("http://127.0.0.1:8081") is None  # llama-swap itself
    assert PORT_TAKEN("http://localhost:8082") is None  # a program whose name cannot be read: nothing to say
    assert PORT_TAKEN("http://127.0.0.1:8083") is None  # nobody
    assert PORT_TAKEN("http://192.168.0.5:8080") is None  # another computer's
    monkeypatch.setattr(autostart, "port_taken", PORT_TAKEN)
    (tmp_path / "start-llama-swap.cmd").write_text("@echo off\n", encoding="utf-8")
    model = FakeLlamaSwap(up=True)  # it answers /v1/models as llama-server does - and is not llama-swap
    assert ensure(model, tmp_path=tmp_path) == "taken" and model.starts == []


def test_the_api(hub):  # noqa: F811
    found = hub.get("/v1/autostart").json()
    assert found["login"] is False and found["llama_swap"] is True and found["login_command"].endswith("-m app --tray")
    changed = hub.put("/v1/autostart", json={"login": True, "llama_swap": False}).json()
    assert changed["login"] is True and changed["llama_swap"] is False
    assert hub.app.state.run_store.get() == found["login_command"]
    assert hub.put("/v1/autostart", json={"login": False}).json() == {**changed, "login": False}
    feed = hub.get("/v1/feed", params={"types": "autostart.changed"}).json()["events"]
    assert len(feed) == 2
    other = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.put("/v1/autostart", json={"login": True}, headers={"Authorization": f"Bearer {other}"}).status_code \
        == 403  # fmt: skip


def test_the_test_hub_never_starts_llama_swap(hub):  # noqa: F811
    assert hub.app.state.settings.manage_llama_swap is False  # only `python -m hub` (HubSettings.from_env) does


def test_where_llama_swap_and_its_config_are_found(tmp_path, monkeypatch):
    """10.3: the environment, then the window's choice, then the files beside LocalChat's folder on the computer it
    was built on, then the engines the first-run wizard put into LocalChat's own folder."""
    from hub import llama_config as lc

    monkeypatch.delenv(lc.CONFIG_ENV)
    monkeypatch.delenv(autostart.LLAMA_SWAP_START_ENV)
    monkeypatch.setattr(lc, "DEFAULT_CONFIG", tmp_path / "old" / "config.yaml")
    monkeypatch.setattr(autostart, "DEFAULT_LLAMA_SWAP_START", tmp_path / "old" / "start-llama-swap.cmd")
    monkeypatch.setattr(lc, "configured", None)
    monkeypatch.setattr(autostart, "configured_start", None)
    home = tmp_path / "localchat-home" / "engines" / "llama-swap"
    assert lc.config_path() == home / "config.yaml" and lc.config_source() == "default"
    assert autostart.llama_swap_script() == home / "llama-swap.exe"
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "config.yaml").write_text("models:\n", encoding="utf-8")
    (tmp_path / "old" / "start-llama-swap.cmd").write_text("@echo off\n", encoding="utf-8")
    assert lc.config_path() == tmp_path / "old" / "config.yaml"  # the first computer's own setup stays
    assert autostart.llama_swap_script() == tmp_path / "old" / "start-llama-swap.cmd"
    monkeypatch.setattr(lc, "configured", tmp_path / "mine.yaml")
    assert lc.config_path() == tmp_path / "mine.yaml" and lc.config_source() == "chosen"
    monkeypatch.setenv(lc.CONFIG_ENV, str(tmp_path / "env.yaml"))
    assert lc.config_path() == tmp_path / "env.yaml" and lc.config_source() == "env"

    exe = autostart.start_command(home / "llama-swap.exe", "http://127.0.0.1:8099")
    assert exe == [str(home / "llama-swap.exe"), "--config", str(tmp_path / "env.yaml"), "--listen", "127.0.0.1:8099",
                   "--watch-config"]  # fmt: skip
    script = autostart.start_command(tmp_path / "go.cmd", "http://127.0.0.1:8080")
    assert script == ["cmd", "/c", str(tmp_path / "go.cmd")]


def test_the_window_chooses_the_paths(hub, tmp_path, monkeypatch):  # noqa: F811
    from hub import llama_config as lc

    monkeypatch.delenv(lc.CONFIG_ENV)
    monkeypatch.delenv(autostart.LLAMA_SWAP_START_ENV)
    found = hub.get("/v1/autostart").json()
    assert found["data_dir"] == str(hub.app.state.settings.paths.root) and found["home"].endswith("localchat-home")
    config = tmp_path / "my-swap" / "config.yaml"
    config.parent.mkdir()
    config.write_text('models:\n  "a":\n    cmd: |\n      llama-server -m a.gguf\n', encoding="utf-8")
    start = tmp_path / "my-swap" / "llama-swap.exe"
    start.write_bytes(b"MZ")
    chosen = hub.put("/v1/autostart", json={"llama_swap_config": str(config), "llama_swap_start": str(start)}).json()
    assert (chosen["llama_swap_config"], chosen["llama_swap_config_source"], chosen["llama_swap_config_found"]) == (
        str(config), "chosen", True)  # fmt: skip
    assert (chosen["llama_swap_script"], chosen["llama_swap_script_source"]) == (str(start), "chosen")
    assert lc.config_path() == config and autostart.llama_swap_script() == start
    lc.configured = autostart.configured_start = None  # what a restarted hub reads again from its settings
    hub.app.state.db.call(autostart.load_choices)
    assert lc.config_path() == config and autostart.llama_swap_script() == start
    for body, said in (({"llama_swap_config": str(tmp_path / "none.yaml")}, "нет такого файла"),
                       ({"llama_swap_config": str(start)}, ".yaml"), ({"llama_swap_start": str(config)}, ".exe"),
                       ({"llama_swap_config": "relative.yaml"}, "нет такого файла")):  # fmt: skip
        refused = hub.put("/v1/autostart", json=body)
        assert refused.status_code == 422 and said in refused.json()["detail"]
    not_swap = tmp_path / "other.yaml"
    not_swap.write_text("name: x\n", encoding="utf-8")
    refused = hub.put("/v1/autostart", json={"llama_swap_config": str(not_swap)})
    assert "не конфиг llama-swap" in refused.json()["detail"]
    back = hub.put("/v1/autostart", json={"llama_swap_config": "", "llama_swap_start": ""}).json()
    assert back["llama_swap_config_source"] == "default" and back["llama_swap_script_source"] == "default"


def test_the_running_llama_swap_is_found_and_kept(hub, tmp_path, monkeypatch):  # noqa: F811
    """The installed AFO beside a llama-swap the person runs already (2026-11-04: «Конфиг llama-swap … файла нет»
    though llama-swap answered): with nothing chosen and AFO's own files not there, the llama-swap running at the
    hub's address gives its program and its config - from its command line - and they are kept, so that the hub can
    start it later itself. The window's choice and the environment come first."""
    from hub import llama_config as lc

    monkeypatch.delenv(lc.CONFIG_ENV)
    monkeypatch.delenv(autostart.LLAMA_SWAP_START_ENV)
    monkeypatch.setattr(lc, "DEFAULT_CONFIG", tmp_path / "none" / "config.yaml")
    monkeypatch.setattr(autostart, "DEFAULT_LLAMA_SWAP_START", tmp_path / "none" / "start-llama-swap.cmd")
    swap = tmp_path / "AI" / "llama-swap"
    swap.mkdir(parents=True)
    exe = swap / "llama-swap.exe"
    exe.write_bytes(b"MZ")
    (swap / "config.yaml").write_text('models:\n  "a":\n    cmd: |\n      llama-server -m a.gguf\n', encoding="utf-8")

    class Process:
        def __init__(self, name, exe, cmdline, cwd):
            self.info = {"name": name, "exe": exe, "cmdline": cmdline, "cwd": cwd}

    running = [Process("llama-swap.exe", "C:/other/llama-swap.exe", ["llama-swap.exe"], "C:/other"),  # :8080
               Process("llama-server.exe", "C:/x/llama-server.exe", ["llama-server", "--port", "9"], "C:/x"),
               Process("llama-swap.exe", str(exe), [str(exe), "-config", "config.yaml", "-listen=127.0.0.1:9"],
                       str(swap))]  # fmt: skip
    monkeypatch.setattr(autostart.psutil, "process_iter", lambda attrs: iter(running))
    assert autostart.running_llama_swap("http://127.0.0.1:9") == (exe, swap / "config.yaml")
    assert autostart.running_llama_swap("http://127.0.0.1:7") is None
    found = hub.get("/v1/autostart").json()
    assert (found["llama_swap_config"], found["llama_swap_config_source"], found["llama_swap_config_found"]) == (
        str(swap / "config.yaml"), "found", True)  # fmt: skip
    assert (found["llama_swap_script"], found["llama_swap_script_source"], found["llama_swap_found"]) == (
        str(exe), "found", True)  # fmt: skip
    # kept: a hub started again, llama-swap not running now, still knows them
    monkeypatch.setattr(autostart.psutil, "process_iter", lambda attrs: iter([]))
    lc.found = autostart.found_start = None
    hub.app.state.db.call(autostart.load_choices)
    assert lc.config_path() == swap / "config.yaml" and autostart.llama_swap_script() == exe
    assert autostart.start_command(exe, "http://127.0.0.1:9")[:3] == [str(exe), "--config", str(swap / "config.yaml")]
    # the window's choice comes first
    other = tmp_path / "mine.yaml"
    other.write_text('models:\n  "b":\n    cmd: |\n      llama-server -m b.gguf\n', encoding="utf-8")
    chosen = hub.put("/v1/autostart", json={"llama_swap_config": str(other)}).json()
    assert (chosen["llama_swap_config"], chosen["llama_swap_config_source"]) == (str(other), "chosen")


def test_llama_swap_moves_off_a_port_another_program_holds(tmp_path, monkeypatch):
    """39.2 (2026-11-12, the user's word: «Порт llama-swap (8080) занят чужой программой — llama-swap сам переходит на
    свободный»): a laptop with Tomcat on 8080 - AFO's own llama-swap (an .exe it tells where to listen) goes to a free
    port after it, before the hub builds what talks to it, and the port is kept: a llama-swap still running there
    (it outlives the hub) is found there. When nobody holds the default port and nothing of AFO's runs on the kept
    one, it is back on the default; a script listens where it says - it is not moved. Real sockets: the test's own
    process holds the port, a program that is not llama-swap."""
    monkeypatch.setattr(autostart, "port_taken", PORT_TAKEN)  # who really listens, not the file's stand-in
    exe = tmp_path / "engines" / "llama-swap" / "llama-swap.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(autostart.LLAMA_SWAP_START_ENV, str(exe))
    told = []

    async def fake_start(url, script, wanted, log_file, **kwargs):  # the real llama-swap never starts here
        told.append(autostart.start_command(script, url))
        return "started"

    monkeypatch.setattr(autostart, "ensure_llama_swap", fake_start)
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    default = f"http://127.0.0.1:{port}"

    def started() -> str:
        settings = HubSettings(paths=data_paths(), llama_swap_url=default, status_interval_s=0, process_interval_s=0,
                               manage_llama_swap=True, move_llama_swap=True)  # fmt: skip
        with TestClient(create_app(settings), base_url=BASE) as client:
            state = client.app.state
            client.portal.call(asyncio.wait_for, state.initial_sync, 30)
            url = state.settings.llama_swap_url
            assert state.gateway.provider("llama-swap").base_url == url and state.installer._url == url
            return url

    try:
        moved = started()
        new = int(moved.rsplit(":", 1)[1])
        assert port < new <= port + autostart.PORTS_TRIED and told[-1][-3:-1] == ["--listen", f"127.0.0.1:{new}"]
        assert started() == moved  # kept: the same one while the default is held
        holder.close()

        swap = ThreadingHTTPServer(("127.0.0.1", new), OwnSwap)  # AFO's llama-swap, still running from before
        threading.Thread(target=swap.serve_forever, daemon=True).start()
        try:
            assert started() == moved  # found where it runs, not started again on the default
        finally:
            swap.shutdown()
            swap.server_close()
        assert started() == default  # nothing there now, the default free: back on it

        holder = socket.socket()
        holder.bind(("127.0.0.1", port))
        holder.listen()
        script = tmp_path / "start-llama-swap.cmd"
        script.write_text("@echo off\n", encoding="utf-8")
        monkeypatch.setenv(autostart.LLAMA_SWAP_START_ENV, str(script))
        assert started() == default  # a script says itself where it listens: nothing to move
    finally:
        holder.close()


def test_the_hub_moves_off_a_port_another_program_holds(tmp_path):
    """43.9 (the user's word, 2026-11-13: «хаб сам уходит на свободный порт»): a Docker container answers on 8765 at
    the author's. The hub listens on a free port after its own, and stays there next time though its own is free
    again: the address the person gave other programs holds, and a program that starts after AFO at login finds its
    port free. The port it moved to is kept in the database for the port it wanted; read before the hub has opened
    the database, without changing it. Real sockets: the test's own process holds the ports."""
    holders = []

    def hold(port: int = 0) -> int:
        holder = socket.socket()
        holder.bind(("127.0.0.1", port))
        holder.listen()
        holders.append(holder)
        return holder.getsockname()[1]

    try:
        wanted = hold()
        moved = autostart.hub_port("127.0.0.1", wanted, None)
        assert moved is not None and wanted < moved <= wanted + autostart.PORTS_TRIED
        assert autostart.hub_port("127.0.0.1", wanted, moved) == moved
        holders.pop(0).close()  # the other program is gone
        assert autostart.hub_port("127.0.0.1", wanted, moved) == moved  # kept, though its own is free
        hold(moved)  # and now something else holds the kept one
        assert autostart.hub_port("127.0.0.1", wanted, moved) == wanted  # back on its own
        assert autostart.hub_port("127.0.0.1", wanted, None) == wanted
    finally:
        for holder in holders:
            holder.close()

    db = data_paths().db
    assert autostart.kept_hub_port(tmp_path / "none.db", 8765) is None  # no database yet
    assert not (tmp_path / "none.db").exists()
    settings = HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                           process_interval_s=0, port=8766, wanted_port=8765)  # fmt: skip
    with TestClient(create_app(settings), base_url=BASE):
        pass
    assert autostart.kept_hub_port(db, 8765) == 8766
    assert autostart.kept_hub_port(db, 9000) is None  # kept for the port it wanted, not another one asked for
    with TestClient(create_app(replace(settings, port=8765, wanted_port=None)), base_url=BASE):
        pass
    assert autostart.kept_hub_port(db, 8765) is None  # on its own port again: nothing kept
