"""Starting by itself (Q8): the hub at Windows login (a Run-key value, here in memory - the registry is never touched)
and llama-swap started by the hub when it does not answer (here a fake starter and a mock llama-swap)."""

import asyncio

import httpx

from hub import autostart
from hub.auth import create_client
from tests.test_tools import hub  # noqa: F401 - the hub fixture


def test_the_login_value():
    store = autostart.MemoryRunStore()
    assert not autostart.login_enabled(store)
    autostart.set_login(store, True)
    assert store.value == autostart.login_command() and store.value.endswith('" -m hub')
    assert "pythonw.exe" in store.value.lower() or "python" in store.value.lower()
    autostart.set_login(store, False)
    assert store.value is None and isinstance(autostart.default_run_store(), autostart.MemoryRunStore)


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


def test_the_api(hub):  # noqa: F811
    found = hub.get("/v1/autostart").json()
    assert found["login"] is False and found["llama_swap"] is True and found["login_command"].endswith("-m hub")
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
